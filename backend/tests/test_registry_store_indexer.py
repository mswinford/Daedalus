"""Tests for the registry version store and git indexer."""
import asyncio

import pytest

from registry.db import Database
from registry.indexer import (
    commit_all,
    ensure_repo,
    sync_from_repo,
    write_manifest_to_repo,
)
from registry.store import (
    InvalidTransitionError,
    VersionConflictError,
    extract_artifact,
    get_artifact,
    list_capabilities,
    resolve_version,
    search,
    transition_stage,
    upsert_version,
)
from schema.capability import (
    CapabilityManifest,
    LifecycleStage,
)
from schema.models import ToolDefinition, Workflow


def _wf_manifest(
    name="acme/wf", version="1.0.0", desc="Demo workflow that does things",
    tags=None, stage=LifecycleStage.DRAFT, created_at=1700000000.0,
):
    return CapabilityManifest(
        name=name, version=version, description=desc, tags=tags or ["demo"],
        kind="workflow",
        spec={"kind": "workflow", "workflow": Workflow(id="w", name="w").model_dump()},
        interface={"type": "ai_forge_workflow"},
        governance={"owner": "acme"},
        stage=stage, created_at=created_at,
    )


def _prompt_manifest(name="acme/prompts", version="1.0.0", stage=LifecycleStage.DRAFT):
    return CapabilityManifest(
        name=name, version=version,
        description="Invoice extraction prompt for OCR pipelines",
        tags=["invoice", "ocr"],
        kind="prompt",
        spec={"kind": "prompt", "text": "Extract {{fields}} from the invoice."},
        governance={"owner": "acme"},
        stage=stage, created_at=1700000001.0,
    )


@pytest.fixture()
def db(tmp_path):
    d = asyncio.run(Database.connect(tmp_path / "registry.db"))
    yield d
    asyncio.run(d.close())


# ─── upsert / immutability ───────────────────────────────────────────────────

def test_upsert_inserts_then_noops(db):
    async def scenario():
        assert await upsert_version(db, _wf_manifest()) is True
        assert await upsert_version(db, _wf_manifest()) is False
        rows = await db.conn.execute_fetchall("SELECT COUNT(*) AS n FROM capability_fts")
        assert rows[0]["n"] == 1
    asyncio.run(scenario())


def test_upsert_conflict_on_different_content(db):
    async def scenario():
        await upsert_version(db, _wf_manifest())
        with pytest.raises(VersionConflictError):
            await upsert_version(db, _wf_manifest(desc="different content"))
    asyncio.run(scenario())


# ─── artifact extraction ─────────────────────────────────────────────────────

def test_extract_artifact_per_kind():
    m = _wf_manifest()
    assert extract_artifact(m) == Workflow(id="w", name="w").model_dump(mode="json")

    p = _prompt_manifest()
    art = extract_artifact(p)
    assert art["text"].startswith("Extract") and "kind" not in art

    t = CapabilityManifest(
        name="acme/tool", version="1.0.0", description="d", kind="tool",
        spec={"kind": "tool", "tool": ToolDefinition(
            id="t", name="n", description="d", parameters={},
            implementation={"type": "builtin", "config": {"function": "f"}},
        ).model_dump()},
        interface={"type": "mcp"},
        governance={"owner": "acme"}, created_at=1.0,
    )
    assert extract_artifact(t)["name"] == "n"


# ─── lifecycle transitions ───────────────────────────────────────────────────

def test_lifecycle_happy_path(db):
    async def scenario():
        await upsert_version(db, _wf_manifest())
        assert await transition_stage(db, "acme/wf", "1.0.0", LifecycleStage.REVIEW) == "review"
        assert await transition_stage(db, "acme/wf", "1.0.0", LifecycleStage.APPROVED) == "approved"
        assert await transition_stage(db, "acme/wf", "1.0.0", LifecycleStage.PUBLISHED) == "published"
    asyncio.run(scenario())


def test_lifecycle_rejects_invalid_jump(db):
    async def scenario():
        await upsert_version(db, _wf_manifest())
        with pytest.raises(InvalidTransitionError):
            await transition_stage(db, "acme/wf", "1.0.0", LifecycleStage.PUBLISHED)
        with pytest.raises(KeyError):
            await transition_stage(db, "acme/wf", "9.9.9", LifecycleStage.REVIEW)
    asyncio.run(scenario())


# ─── version resolution ──────────────────────────────────────────────────────

def test_latest_resolves_newest_published(db):
    async def scenario():
        await upsert_version(db, _wf_manifest(version="1.0.0", stage=LifecycleStage.PUBLISHED))
        await upsert_version(db, _wf_manifest(version="2.0.0"))  # draft
        resolved = await resolve_version(db, "acme/wf")
        assert resolved["version"] == "1.0.0"
        exact = await resolve_version(db, "acme/wf", "2.0.0")
        assert exact["version"] == "2.0.0" and exact["stage"] == "draft"
    asyncio.run(scenario())


def test_latest_without_published_raises(db):
    async def scenario():
        await upsert_version(db, _wf_manifest())
        with pytest.raises(LookupError):
            await resolve_version(db, "acme/wf")
        with pytest.raises(KeyError):
            await resolve_version(db, "acme/wf", "9.9.9")
        with pytest.raises(KeyError):
            await resolve_version(db, "nope/nothing")
    asyncio.run(scenario())


def test_get_artifact_returns_payload(db):
    async def scenario():
        await upsert_version(db, _wf_manifest(stage=LifecycleStage.PUBLISHED))
        result = await get_artifact(db, "acme/wf")
        assert result["version"] == "1.0.0"
        assert result["artifact"]["id"] == "w"
        assert result["manifest"]["name"] == "acme/wf"
    asyncio.run(scenario())


# ─── list / search ───────────────────────────────────────────────────────────

def test_list_capabilities_groups_by_name(db):
    async def scenario():
        await upsert_version(db, _wf_manifest(version="1.0.0", stage=LifecycleStage.PUBLISHED))
        await upsert_version(db, _wf_manifest(version="1.1.0"))
        await upsert_version(db, _prompt_manifest())
        listing = await list_capabilities(db)
        assert [c["name"] for c in listing] == ["acme/prompts", "acme/wf"]
        wf = next(c for c in listing if c["name"] == "acme/wf")
        assert wf["version_count"] == 2
        assert wf["newest_version"] == "1.1.0"
        assert wf["latest_published"] == "1.0.0"
    asyncio.run(scenario())


def test_search_fts(db):
    async def scenario():
        await upsert_version(db, _wf_manifest(stage=LifecycleStage.PUBLISHED))
        await upsert_version(db, _prompt_manifest(stage=LifecycleStage.PUBLISHED))

        hits = await search(db, "invoice")
        assert [h["name"] for h in hits] == ["acme/prompts"]

        hits = await search(db, "demo workflow", kind="workflow")
        assert [h["name"] for h in hits] == ["acme/wf"]

        assert await search(db, "zzz-not-there") == []
        assert await search(db, "") == []
    asyncio.run(scenario())


# ─── git indexer ─────────────────────────────────────────────────────────────

def test_sync_from_repo(tmp_path):
    repo = tmp_path / "caps"

    async def scenario():
        await ensure_repo(repo)
        await write_manifest_to_repo(repo, _wf_manifest(version="1.0.0"))
        await write_manifest_to_repo(
            repo, _wf_manifest(version="1.1.0", desc="Updated demo workflow")
        )
        sha = await commit_all(repo, "publish 1.0.0 and 1.1.0")
        assert sha

        db = await Database.connect(tmp_path / "registry.db")
        report = await sync_from_repo(repo, db)
        assert report["synced"] == 2
        assert not report["skipped"] and not report["conflicts"]

        rows = await db.conn.execute_fetchall(
            "SELECT source_commit FROM capability_versions"
        )
        assert {r["source_commit"] for r in rows} == {sha}

        # idempotent re-sync
        report2 = await sync_from_repo(repo, db)
        assert report2["synced"] == 0
        await db.close()
    asyncio.run(scenario())


def test_sync_skips_invalid_and_mismatched(tmp_path):
    repo = tmp_path / "caps"

    async def scenario():
        await ensure_repo(repo)
        # valid
        await write_manifest_to_repo(repo, _wf_manifest(version="1.0.0"))
        # invalid JSON
        bad = repo / "acme" / "broken" / "1.0.0"
        bad.mkdir(parents=True)
        (bad / "manifest.json").write_text("{not json")
        # path/name mismatch
        mismatched = repo / "other" / "x" / "1.0.0"
        mismatched.mkdir(parents=True)
        (mismatched / "manifest.json").write_text(
            _wf_manifest().model_dump_json()
        )

        db = await Database.connect(tmp_path / "registry.db")
        report = await sync_from_repo(repo, db)
        assert report["synced"] == 1
        assert len(report["skipped"]) == 2
        errors = " | ".join(s["error"] for s in report["skipped"])
        assert "invalid manifest" in errors
        assert "does not match" in errors
        await db.close()
    asyncio.run(scenario())


def test_commit_all_noop_returns_same_head(tmp_path):
    repo = tmp_path / "caps"

    async def scenario():
        await ensure_repo(repo)
        await write_manifest_to_repo(repo, _wf_manifest())
        sha1 = await commit_all(repo, "first")
        sha2 = await commit_all(repo, "nothing changed")
        assert sha1 == sha2
    asyncio.run(scenario())
