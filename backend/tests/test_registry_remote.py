"""Tests for the registry's git-remote support (clone/push/rebase/sync).

The "remote" is a local bare repository — every network code path (fetch,
push, rebase, clone) runs against it without any real network access.
"""
import asyncio
import subprocess

import pytest

from registry.db import Database
from registry.indexer import (
    _authed_url,
    commit_all,
    ensure_repo,
    pull_for_sync,
    push_with_rebase,
    sync_from_repo,
    write_manifest_to_repo,
)
from schema.capability import CapabilityManifest, LifecycleStage


def _prompt_manifest(name="acme/prompts", version="1.0.0", text="Extract {{fields}}."):
    return CapabilityManifest(
        name=name, version=version,
        description=f"Test prompt {name}",
        tags=["test"],
        kind="prompt",
        spec={"kind": "prompt", "text": text},
        governance={"owner": "acme"},
        stage=LifecycleStage.DRAFT, created_at=1700000000.0,
    )


def _g(repo, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _g_raw(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True,
    )


@pytest.fixture()
def bare_remote(tmp_path):
    """A bare repository standing in for a hosted git remote."""
    path = tmp_path / "remote.git"
    _g_raw("-c", "init.defaultBranch=main", "init", "--bare", str(path))
    return path


@pytest.fixture()
def db(tmp_path):
    d = asyncio.run(Database.connect(tmp_path / "registry.db"))
    yield d
    asyncio.run(d.close())


# ─── token URL rewriting ─────────────────────────────────────────────────────

def test_authed_url_https_injects_token():
    url = _authed_url("https://github.com/acme/caps.git", "gh_tok_123")
    assert url == "https://x-access-token:gh_tok_123@github.com/acme/caps.git"


def test_authed_url_non_https_and_empty_token_untouched():
    ssh = "git@github.com:acme/caps.git"
    assert _authed_url(ssh, "gh_tok_123") == ssh
    https = "https://github.com/acme/caps.git"
    assert _authed_url(https, "") == https


# ─── fresh machine → remote → second machine ────────────────────────────────

def test_fresh_publish_reaches_remote_and_second_machine_clones(tmp_path, bare_remote, db):
    async def scenario():
        machine_a = tmp_path / "machine-a"
        await ensure_repo(machine_a, str(bare_remote))  # clones (empty)
        await write_manifest_to_repo(machine_a, _prompt_manifest())
        sha = await commit_all(machine_a, "publish acme/prompts@1.0.0",
                               remote=str(bare_remote))
        assert sha is not None

        machine_b = tmp_path / "machine-b"
        await ensure_repo(machine_b, str(bare_remote))  # clones with content
        report = await sync_from_repo(machine_b, db)
        assert report["synced"] == 1
        assert report["skipped"] == []

    asyncio.run(scenario())


def test_unreachable_remote_fails_publish_loudly(tmp_path):
    async def scenario():
        repo = tmp_path / "local"
        await ensure_repo(repo, str(tmp_path / "does-not-exist.git"))
        # init fallback happened; the push must fail loudly, not silently
        # commit local-only.
        await write_manifest_to_repo(repo, _prompt_manifest())
        with pytest.raises(RuntimeError, match="fetch|push"):
            await commit_all(repo, "publish", remote=str(tmp_path / "does-not-exist.git"))

    asyncio.run(scenario())


# ─── rebasing on out-of-band remote work ─────────────────────────────────────

def test_publish_rebases_on_remote_work_pushed_elsewhere(tmp_path, bare_remote, db):
    async def scenario():
        a = tmp_path / "a"
        await ensure_repo(a, str(bare_remote))
        await write_manifest_to_repo(a, _prompt_manifest("acme/one"))
        await commit_all(a, "publish acme/one@1.0.0", remote=str(bare_remote))

        b = tmp_path / "b"
        await ensure_repo(b, str(bare_remote))  # has acme/one only

        # Out-of-band publish from machine A (e.g. another box or the CLI).
        await write_manifest_to_repo(a, _prompt_manifest("acme/two"))
        await commit_all(a, "publish acme/two@1.0.0", remote=str(bare_remote))

        # B publishes on top of its stale base — must rebase, not merge/fail.
        await write_manifest_to_repo(b, _prompt_manifest("acme/three"))
        await commit_all(b, "publish acme/three@1.0.0", remote=str(bare_remote))

        # History stays linear (no merge commits) and all three land.
        log = _g(bare_remote, "log", "--oneline")
        assert log.stdout.count("merge") == 0
        c = tmp_path / "c"
        await ensure_repo(c, str(bare_remote))
        report = await sync_from_repo(c, db)
        assert report["synced"] == 3

    asyncio.run(scenario())


def test_push_reject_triggers_fetch_rebase_retry(tmp_path, bare_remote):
    async def scenario():
        a = tmp_path / "a"
        await ensure_repo(a, str(bare_remote))
        await write_manifest_to_repo(a, _prompt_manifest("acme/one"))
        await commit_all(a, "publish acme/one@1.0.0", remote=str(bare_remote))

        b = tmp_path / "b"
        await ensure_repo(b, str(bare_remote))  # stale: no acme/two yet

        # Advance the remote out-of-band.
        await write_manifest_to_repo(a, _prompt_manifest("acme/two"))
        await commit_all(a, "publish acme/two@1.0.0", remote=str(bare_remote))

        # B gets an unpushed local commit without fetching (simulates the
        # window where a push lands non-fast-forward).
        _g(b, "-c", "user.name=t", "-c", "user.email=t@t",
           "commit", "--allow-empty", "-m", "local work")

        await push_with_rebase(b, str(bare_remote))

        log = _g(bare_remote, "log", "--oneline", "main")
        assert "local work" in log.stdout
        assert "merge" not in log.stdout

    asyncio.run(scenario())


# ─── same name@version, different content → fail loudly ─────────────────────

def test_same_version_content_conflict_aborts_rebase(tmp_path, bare_remote):
    async def scenario():
        a = tmp_path / "a"
        await ensure_repo(a, str(bare_remote))
        await write_manifest_to_repo(a, _prompt_manifest("acme/clash"))
        await commit_all(a, "publish acme/clash@1.0.0", remote=str(bare_remote))

        b = tmp_path / "b"
        await ensure_repo(b, str(bare_remote))  # stale clone

        # A human edits the same version directly on the hosting service
        # (different content at the same path) and it lands on the remote.
        c = tmp_path / "c"
        await ensure_repo(c, str(bare_remote))
        await write_manifest_to_repo(
            c, _prompt_manifest("acme/clash", text="EDITED ON THE HOST"),
        )
        _g(c, "-c", "user.name=human", "-c", "user.email=h@h", "commit",
           "-am", "edit manifest in place")
        _g(c, "push")  # upstream is set by the clone

        # B now tries to publish its own different content at the same path.
        await write_manifest_to_repo(
            b, _prompt_manifest("acme/clash", text="LOCAL EDIT"),
        )
        with pytest.raises(RuntimeError, match="rebase"):
            await commit_all(b, "publish acme/clash@1.0.0", remote=str(bare_remote))

        # The rebase was aborted — B's working tree is clean and usable.
        assert not (b / ".git" / "rebase-merge").exists()
        status = _g(b, "status", "--porcelain")
        assert status.stdout.strip() == ""

    asyncio.run(scenario())


# ─── startup sync ────────────────────────────────────────────────────────────

def test_pull_for_sync_brings_stale_clone_current(tmp_path, bare_remote, db):
    async def scenario():
        a = tmp_path / "a"
        await ensure_repo(a, str(bare_remote))
        await write_manifest_to_repo(a, _prompt_manifest("acme/one"))
        await commit_all(a, "publish acme/one@1.0.0", remote=str(bare_remote))

        b = tmp_path / "b"
        await ensure_repo(b, str(bare_remote))  # stale: no acme/two yet

        await write_manifest_to_repo(a, _prompt_manifest("acme/two"))
        await commit_all(a, "publish acme/two@1.0.0", remote=str(bare_remote))

        assert await pull_for_sync(b, str(bare_remote)) is True
        report = await sync_from_repo(b, db)
        assert report["synced"] == 2

    asyncio.run(scenario())


def test_pull_for_sync_offline_is_best_effort(tmp_path, bare_remote):
    async def scenario():
        a = tmp_path / "a"
        await ensure_repo(a, str(bare_remote))
        await write_manifest_to_repo(a, _prompt_manifest("acme/one"))
        await commit_all(a, "publish acme/one@1.0.0", remote=str(bare_remote))

        # Remote vanishes (offline boot) — no exception, False returned.
        assert await pull_for_sync(a, str(tmp_path / "gone.git")) is False
        # Missing local repo — also just False.
        assert await pull_for_sync(tmp_path / "never-existed", str(bare_remote)) is False

    asyncio.run(scenario())
