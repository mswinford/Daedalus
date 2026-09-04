"""Tests for capability ref inlining (R1 import is self-contained)."""
from fastapi.testclient import TestClient

from registry.cli import SAMPLES_DIR, _publish


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_REGISTRY_DB", str(tmp_path / "registry.db"))
    monkeypatch.setenv("DAEDALUS_CAPABILITIES_REPO", str(tmp_path / "caps"))
    from registry.main import app
    return TestClient(app)


def _seed_samples():
    assert _publish(sorted(SAMPLES_DIR.glob("*.json"))) == 0


def _skill_manifest(name, tools):
    return {
        "name": name,
        "version": "1.0.0",
        "kind": "skill",
        "description": "test skill",
        "tags": ["test"],
        "spec": {"kind": "skill", "prompt": "do the thing", "tools": tools},
        "governance": {"owner": "acme"},
    }


def _inject_via_git(tmp_path, monkeypatch, manifest):
    """Commit a manifest straight to the capabilities repo and sync the
    index, bypassing the publish gate — simulates an operator pushing bad
    content directly to git (sync_from_repo is a repair path on purpose)."""
    import asyncio

    from registry.config import get_settings
    from registry.db import Database
    from registry.indexer import (
        commit_all,
        ensure_repo,
        sync_from_repo,
        write_manifest_to_repo,
    )
    from schema.capability import CapabilityManifest

    async def _run():
        settings = get_settings()
        db = await Database.connect(settings.registry_db)
        try:
            await ensure_repo(settings.capabilities_repo)
            await write_manifest_to_repo(
                settings.capabilities_repo,
                CapabilityManifest.model_validate(manifest),
            )
            await commit_all(settings.capabilities_repo, "inject test manifest")
            await sync_from_repo(settings.capabilities_repo, db)
        finally:
            await db.close()

    asyncio.run(_run())


def test_inline_agent_resolves_full_chain(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _seed_samples()
        r = client.get(
            "/registry/capabilities/acme/selftest-agent/use",
            params={"inline": "true"},
        )
        assert r.status_code == 200, r.text
        art = r.json()["artifact"]

        # model profile ref -> inlined ModelConfig
        assert art["model"]["id"] == "local-llama"
        assert art["model"]["provider"] == "openai_compatible"

        # direct tool ref -> inlined ToolDefinition
        assert len(art["tools"]) == 1
        assert art["tools"][0]["id"] == "echo"
        assert art["tools"][0]["implementation"]["type"] == "builtin"

        # skill ref -> inlined skill with ITS tool refs resolved too
        assert len(art["skills"]) == 1
        skill = art["skills"][0]
        assert skill["name"] == "acme/tool-selftest-skill"
        assert "self-test" in skill["prompt"]
        assert [t["id"] for t in skill["tools"]] == ["echo"]

        # agent's own prompt is preserved
        assert "self-test" in art["prompt"]


def test_inline_skill_resolves_tool_refs(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _seed_samples()
        r = client.get(
            "/registry/capabilities/acme/tool-selftest-skill/use",
            params={"inline": "true"},
        )
        assert r.status_code == 200, r.text
        art = r.json()["artifact"]
        assert art["name"] == "acme/tool-selftest-skill"
        assert [t["id"] for t in art["tools"]] == ["echo"]


def test_inline_skill_with_prompt_ref(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _seed_samples()
        manifest = _skill_manifest("acme/ref-prompt-skill", [])
        manifest["spec"]["prompt"] = None
        manifest["spec"]["prompt_ref"] = {
            "name": "acme/courteous-assistant-prompt",
            "version": "latest",
        }
        assert client.post("/registry/capabilities", json=manifest).status_code == 201

        r = client.get(
            "/registry/capabilities/acme/ref-prompt-skill/use",
            params={"inline": "true", "version": "1.0.0"},
        )
        assert r.status_code == 200, r.text
        assert "{{role}}" in r.json()["artifact"]["prompt"]


def test_inline_passthrough_for_selfcontained_kinds(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        _seed_samples()
        for name in ("acme/echo-tool", "acme/courteous-assistant-prompt"):
            raw = client.get(f"/registry/capabilities/{name}/use").json()["artifact"]
            inlined = client.get(
                f"/registry/capabilities/{name}/use", params={"inline": "true"}
            ).json()["artifact"]
            assert inlined == raw


def test_raw_use_still_returns_refs(tmp_path, monkeypatch):
    """Back-compat: without inline=true the agent artifact keeps its refs."""
    with _client(tmp_path, monkeypatch) as client:
        _seed_samples()
        r = client.get("/registry/capabilities/acme/selftest-agent/use")
        assert r.status_code == 200
        art = r.json()["artifact"]
        assert art["skills"][0]["name"] == "acme/tool-selftest-skill"
        assert "prompt" not in art["skills"][0]  # still a ref, not inlined


def test_inline_unknown_ref_422(tmp_path, monkeypatch):
    """Unknown refs are rejected at publish; the inliner still guards against
    bad content committed directly to git."""
    with _client(tmp_path, monkeypatch) as client:
        manifest = _skill_manifest(
            "acme/broken-skill",
            [{"name": "acme/does-not-exist", "version": "latest"}],
        )
        r = client.post("/registry/capabilities", json=manifest)
        assert r.status_code == 422
        assert "does-not-exist" in " ".join(r.json()["detail"])

        _inject_via_git(tmp_path, monkeypatch, manifest)
        r = client.get(
            "/registry/capabilities/acme/broken-skill/use",
            params={"inline": "true", "version": "1.0.0"},
        )
        assert r.status_code == 422
        assert "does-not-exist" in r.json()["detail"]


def test_inline_wrong_kind_ref_422(tmp_path, monkeypatch):
    """A skill whose tools[] points at a prompt capability is a bad ref —
    rejected at publish, and still guarded by the inliner for content that
    reaches git out-of-band."""
    with _client(tmp_path, monkeypatch) as client:
        _seed_samples()
        manifest = _skill_manifest(
            "acme/kind-mismatch-skill",
            [{"name": "acme/courteous-assistant-prompt", "version": "latest"}],
        )
        r = client.post("/registry/capabilities", json=manifest)
        assert r.status_code == 422
        assert "expected 'tool'" in " ".join(r.json()["detail"])

        _inject_via_git(tmp_path, monkeypatch, manifest)
        r = client.get(
            "/registry/capabilities/acme/kind-mismatch-skill/use",
            params={"inline": "true", "version": "1.0.0"},
        )
        assert r.status_code == 422
        assert "expected 'tool'" in r.json()["detail"]


def test_inline_self_ref_422(tmp_path, monkeypatch):
    """The ref graph is structurally acyclic per schema (agent -> {skill,
    tool, model_profile, prompt}, skill -> {tool, prompt}; leaf kinds carry no
    refs), so no cycle guard exists by design — a self ref fails resolution
    at publish and the kind check in the inliner."""
    with _client(tmp_path, monkeypatch) as client:
        manifest = _skill_manifest(
            "acme/self-ref-skill",
            [{"name": "acme/self-ref-skill", "version": "1.0.0"}],
        )
        r = client.post("/registry/capabilities", json=manifest)
        assert r.status_code == 422

        _inject_via_git(tmp_path, monkeypatch, manifest)
        r = client.get(
            "/registry/capabilities/acme/self-ref-skill/use",
            params={"inline": "true", "version": "1.0.0"},
        )
        assert r.status_code == 422
        assert "expected 'tool'" in r.json()["detail"]
