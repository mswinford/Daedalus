"""Git -> DB sync: parse manifests from the capabilities repo, upsert versions.

Repo layout (one directory per capability version):

    <repo>/<owner>/<name>/<version>/manifest.json

The manifest's own name/version are authoritative; a path that disagrees is
skipped (and reported) rather than trusted.
"""
import asyncio
import json
from pathlib import Path
from typing import Any, Optional

from registry.db import Database
from registry.store import VersionConflictError, upsert_version
from schema.capability import CapabilityManifest

GIT_IDENTITY = ("AI Forge Registry", "registry@ai-forge.local")


async def _git(repo: Path, *args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(repo), *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, (out or err).decode().strip()


async def git_head(repo: Path) -> Optional[str]:
    code, out = await _git(repo, "rev-parse", "HEAD")
    return out if code == 0 else None


async def ensure_repo(repo: Path) -> None:
    """Create the capabilities git repo on first use."""
    if (repo / ".git").exists():
        return
    repo.mkdir(parents=True, exist_ok=True)
    code, _ = await _git(repo, "init")
    if code != 0:
        raise RuntimeError(f"git init failed for {repo}")


async def write_manifest_to_repo(
    repo: Path, manifest: CapabilityManifest
) -> Path:
    """Write the canonical manifest file for a version (pretty-printed JSON)."""
    path = repo / manifest.name / manifest.version / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True))
    return path


async def commit_all(repo: Path, message: str) -> Optional[str]:
    """git add -A + commit. Returns the new HEAD sha, or the unchanged HEAD
    when there was nothing to commit."""
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
    return await git_head(repo)


async def sync_from_repo(repo: Path, db: Database) -> dict[str, Any]:
    """Walk the repo for manifest.json files and upsert each version.

    Idempotent: identical content is a no-op. Returns a report:
    {"synced": n, "skipped": [{path, error}], "conflicts": [{name, version}]}.
    """
    report: dict[str, Any] = {"synced": 0, "skipped": [], "conflicts": []}
    if not repo.exists():
        return report

    head = await git_head(repo)
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
        except VersionConflictError as e:
            report["conflicts"].append({"name": manifest.name, "version": manifest.version})
    return report
