"""Git -> DB sync: parse manifests from the capabilities repo, upsert versions.

Repo layout (one directory per capability version):

    <repo>/<owner>/<name>/<version>/manifest.json

The manifest's own name/version are authoritative; a path that disagrees is
skipped (and reported) rather than trusted.
"""
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from registry.db import Database
from registry.store import VersionConflictError, upsert_version
from schema.capability import CapabilityManifest

GIT_IDENTITY = ("Daedalus Registry", "registry@daedalus.local")


async def _git(repo: Path, *args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, (out or err).decode().strip()


async def _git_raw(*args: str) -> tuple[int, str]:
    """Like _git but without -C (for commands like clone that create the repo)."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, (out or err).decode().strip()


def _authed_url(url: str, token: str | None) -> str:
    """Inject a token into an HTTPS URL in-memory only (never persisted to
    .git/config — the remote is always stored with its plain URL)."""
    if not token or not url.startswith("https://"):
        return url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, f"x-access-token:{token}@{parts.netloc}",
                       parts.path, parts.query, parts.fragment))


def _token() -> str:
    return os.environ.get("DAEDALUS_GIT_TOKEN") or ""


async def _fetch(repo: Path, remote: str) -> tuple[int, str]:
    """Fetch all branches into remote-tracking refs.

    The (possibly token-authed) URL is passed explicitly: git's transfer
    commands do not honor `-c remote.origin.url=` overrides, so a named
    remote would silently use the stored plain URL.
    """
    return await _git(
        repo, "fetch", _authed_url(remote, _token()),
        "+refs/heads/*:refs/remotes/origin/*",
    )


async def _push(repo: Path, remote: str, refspec: str) -> tuple[int, str]:
    return await _git(repo, "push", _authed_url(remote, _token()), refspec)


async def _remote_head_target(url: str) -> Optional[str]:
    """The branch a fresh clone would track (the remote's HEAD symref target),
    queryable even when the remote has no commits yet — e.g. 'main' for an
    empty GitHub repo. The first push must create that branch or later clones
    check out an unborn ref."""
    code, out = await _git_raw("ls-remote", "--symref", url, "HEAD")
    if code != 0:
        return None
    for line in out.splitlines():
        # symref lines look like: "ref: refs/heads/main\tHEAD"
        if line.startswith("ref:"):
            target = line[len("ref:"):].split("\t")[0].strip()
            if target.startswith("refs/heads/"):
                return target.split("refs/heads/", 1)[1]
    return None


async def _upstream_branch(repo: Path, remote: str) -> Optional[str]:
    """The remote's default branch as a tracking ref (e.g. 'origin/main'),
    or None when it cannot be determined."""
    target = await _remote_head_target(_authed_url(remote, _token()))
    return f"origin/{target}" if target else None


async def git_head(repo: Path) -> Optional[str]:
    code, out = await _git(repo, "rev-parse", "HEAD")
    return out if code == 0 else None


async def ensure_repo(repo: Path, remote: str | None = None) -> None:
    """Create the capabilities git repo on first use; wire up the remote.

    With a remote: a missing local repo is cloned (an empty remote clones as
    an empty local repo); an existing local repo gets origin added or
    re-pointed to match the configured URL.
    """
    if (repo / ".git").exists():
        if remote:
            code, out = await _git(repo, "remote", "get-url", "origin")
            if code != 0:
                await _git(repo, "remote", "add", "origin", remote)
            elif out.strip() != remote:
                await _git(repo, "remote", "set-url", "origin", remote)
        return
    repo.mkdir(parents=True, exist_ok=True)
    if remote:
        code, out = await _git_raw("clone", _authed_url(remote, _token()), str(repo))
        if code == 0:
            # Clone stores the URL it was given — swap in the plain one so a
            # token never sits in .git/config.
            await _git(repo, "remote", "set-url", "origin", remote)
            return
        # Unreachable or empty remote — fall through to a plain local init;
        # the first push will fail loudly if the remote truly is unreachable.
    code, _ = await _git(repo, "init")
    if code != 0:
        raise RuntimeError(f"git init failed for {repo}")
    if remote:
        await _git(repo, "remote", "add", "origin", remote)


async def write_manifest_to_repo(
    repo: Path, manifest: CapabilityManifest
) -> Path:
    """Write the canonical manifest file for a version (pretty-printed JSON)."""
    path = repo / manifest.name / manifest.version / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True))
    return path


async def push_with_rebase(repo: Path, remote: str) -> None:
    """Push local commits to the remote's default branch.

    Fetches first and rebases onto new upstream work (other publishers,
    humans editing on the hosting service); a non-fast-forward push triggers
    one fetch+rebase+retry. A rebase conflict — two publishers writing the
    same name@version with different content — aborts the rebase and raises
    loudly rather than guessing.
    """
    code, out = await _fetch(repo, remote)
    if code != 0:
        raise RuntimeError(f"git fetch failed for {remote}: {out}")
    branch = await _upstream_branch(repo, remote)
    if branch:
        code, count = await _git(repo, "rev-list", "--count", f"HEAD..{branch}")
        if code == 0 and count.strip() not in ("", "0"):
            code, out = await _git(repo, "rebase", branch)
            if code != 0:
                await _git(repo, "rebase", "--abort")
                raise RuntimeError(
                    f"rebase onto {branch} failed — concurrent publish of the "
                    f"same capability with different content?: {out}"
                )
    if branch:
        refspec = f"HEAD:{branch.split('/', 1)[1]}"
    else:
        # Empty remote whose HEAD target we could not query — 'main' is the
        # default for GitHub and modern git, so this matches in practice.
        refspec = "HEAD:main"
    for attempt in (1, 2):
        code, out = await _push(repo, remote, refspec)
        if code == 0:
            return
        if attempt == 1 and ("rejected" in out or "non-fast-forward" in out):
            await _fetch(repo, remote)
            branch = await _upstream_branch(repo, remote) or branch
            if branch:
                code2, out2 = await _git(repo, "rebase", branch)
                if code2 != 0:
                    await _git(repo, "rebase", "--abort")
                    raise RuntimeError(f"push rejected and rebase failed: {out2}")
            continue
        raise RuntimeError(f"git push failed for {remote}: {out}")


async def pull_for_sync(repo: Path, remote: str) -> bool:
    """Fetch + rebase local onto the remote's default branch (startup path).

    Best-effort by design: returns False when the repo is missing, the remote
    is unreachable, or a rebase conflicts (aborted, working tree left as-is) —
    the caller logs and indexes whatever is present locally.
    """
    if not (repo / ".git").exists():
        return False
    code, _ = await _fetch(repo, remote)
    if code != 0:
        return False
    branch = await _upstream_branch(repo, remote)
    if not branch:
        return True  # empty remote — nothing to merge
    code, _ = await _git(repo, "rev-parse", "--verify", "-q", "HEAD")
    if code != 0:
        return True  # no local commits yet
    code, out = await _git(repo, "rebase", branch)
    if code != 0:
        await _git(repo, "rebase", "--abort")
        print(f"warning: capabilities repo rebase onto {branch} failed; "
              f"indexing local state ({out[:200]})")
        return False
    return True


async def commit_all(
    repo: Path, message: str, remote: str | None = None,
) -> Optional[str]:
    """git add -A + commit (+ push when a remote is configured). Returns the
    new HEAD sha, or the unchanged HEAD when there was nothing to commit."""
    await _git(repo, "add", "-A")
    code, status = await _git(repo, "status", "--porcelain")
    if code == 0 and not status:
        return await git_head(repo)
    args = (
        "-c", f"user.name={GIT_IDENTITY[0]}",
        "-c", f"user.email={GIT_IDENTITY[1]}",
        "commit", "-m", message,
    )
    code, out = await _git(repo, *args)
    if code != 0:
        raise RuntimeError(f"git commit failed: {out}")
    if remote:
        await push_with_rebase(repo, remote)
    return await git_head(repo)


async def sync_from_repo(repo: Path, db: Database) -> dict[str, Any]:
    """Walk the repo for manifest.json files and upsert each version.

    Idempotent: identical content is a no-op. When the repo has at least one
    commit, rows whose (name, version) was not seen in this scan are pruned
    from the index (their manifest was removed from the repo). Returns a
    report: {"synced": n, "skipped": [{path, error}],
             "conflicts": [{name, version}], "pruned": [{name, version}]}.
    """
    report: dict[str, Any] = {
        "synced": 0, "skipped": [], "conflicts": [], "pruned": [],
    }
    if not repo.exists():
        return report

    head = await git_head(repo)
    seen: set[tuple[str, str]] = set()
    for path in sorted(repo.rglob("manifest.json")):
        rel = path.relative_to(repo)
        parts = rel.parts
        if ".git" in parts:
            continue
        try:
            manifest = CapabilityManifest.model_validate_json(path.read_text())
        except Exception as e:
            report["skipped"].append({"path": str(rel), "error": f"invalid manifest: {e}"})
            continue
        expected = Path(manifest.name) / manifest.version / "manifest.json"
        if rel != expected:
            report["skipped"].append({
                "path": str(rel),
                "error": f"path does not match name/version ({expected})",
            })
            continue
        try:
            if await upsert_version(db, manifest, source_commit=head):
                report["synced"] += 1
            seen.add((manifest.name, manifest.version))
        except VersionConflictError as e:
            report["conflicts"].append({"name": manifest.name, "version": manifest.version})
            # The manifest still exists in the repo; keep the DB row (with its
            # lifecycle state) rather than pruning it on a content mismatch.
            seen.add((manifest.name, manifest.version))

    if head is not None:
        rows = await db.conn.execute_fetchall(
            "SELECT rowid, name, version FROM capability_versions"
        )
        stale = [r for r in rows if (r["name"], r["version"]) not in seen]
        for r in stale:
            await db.conn.execute(
                "DELETE FROM capability_fts WHERE rowid=?", (r["rowid"],)
            )
            await db.conn.execute(
                "DELETE FROM capability_versions WHERE rowid=?", (r["rowid"],)
            )
        if stale:
            await db.conn.commit()
            report["pruned"] = [
                {"name": r["name"], "version": r["version"]} for r in stale
            ]
    return report
