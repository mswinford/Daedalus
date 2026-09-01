"""Tests for publish-time governance checks (R2): dependency resolution,
kind stability, and per-kind breaking-change detection with semver
enforcement. API-level tests go through TestClient; pure detection logic is
unit-tested directly."""
import json

from fastapi.testclient import TestClient

from registry.cli import _publish as cli_publish
from registry.publish_checks import _secret_hygiene, detect_breaking_changes
from schema.capability import CapabilityManifest
from schema.models import ModelConfig, Workflow


# ─── Manifest builders ───────────────────────────────────────────────────────

def _manifest(kind, name, version, spec, interface=None, dependencies=None,
              stage=None, **top):
    m = {
        "name": name, "version": version,
        "description": f"{name} {version}",
        "kind": kind,
        "spec": {"kind": kind, **spec},
        "governance": {"owner": "acme"},
    }
    if interface is not None:
        m["interface"] = interface
    if dependencies:
        m["dependencies"] = dependencies
    if stage is not None:
        m["stage"] = stage
    m.update(top)
    return m


def _tool_manifest(name="acme/t", version="1.0.0", params=None, tool_id="t",
                   output_schema=None, **kw):
    interface = {
        "type": "http",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": output_schema or {"type": "string"},
    }
    return _manifest(
        "tool", name, version,
        spec={"tool": {
            "id": tool_id, "name": tool_id, "description": "d",
            "parameters": params or {},
            "implementation": {"type": "builtin", "config": {"function": "echo"}},
        }},
        interface=interface, **kw,
    )


def _prompt_manifest(name="acme/p", version="1.0.0", text="Hello {{a}}",
                     variables=None, role="system", **kw):
    return _manifest("prompt", name, version,
                     spec={"text": text, "variables": variables or [],
                           "role": role}, **kw)


def _profile_manifest(name="acme/mp", version="1.0.0", model="llama-3.2-1b",
                      temperature=0.7, api_key_ref=None, **kw):
    model_spec = {
        "id": "local", "name": "Local Llama",
        "provider": "openai_compatible", "model": model,
        "base_url": "http://localhost:8080/v1",
        "default_temperature": temperature,
    }
    if api_key_ref is not None:
        model_spec["api_key_ref"] = api_key_ref
    return _manifest("model_profile", name, version,
                     spec={"model": model_spec}, **kw)


def _skill_manifest(name="acme/s", version="1.0.0", tools=None, prompt="do it",
                    **kw):
    return _manifest("skill", name, version,
                     spec={"prompt": prompt, "tools": tools or []}, **kw)


def _agent_manifest(name="acme/a", version="1.0.0", model_ref=None, tools=None,
                    skills=None, **kw):
    return _manifest("agent", name, version,
                     spec={
                         "model_profile": model_ref or {
                             "name": "acme/mp", "version": "latest"},
                         "prompt": "be helpful",
                         "tools": tools or [],
                         "skills": skills or [],
                     }, **kw)


def _workflow_manifest(name="acme/wf", version="1.0.0", input_schema=None,
                       output_schema=None, wf=None, **kw):
    interface = {
        "type": "ai_forge_workflow",
        "input_schema": input_schema or {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        "output_schema": output_schema or {"type": "string"},
    }
    return _manifest("workflow", name, version,
                     spec={"workflow": (wf or Workflow(id="w", name="w")).model_dump()},
                     interface=interface, **kw)


def _ref(name, version="latest"):
    return {"name": name, "version": version}


# ─── API-level tests ─────────────────────────────────────────────────────────

def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_FORGE_REGISTRY_DB", str(tmp_path / "registry.db"))
    monkeypatch.setenv("AI_FORGE_CAPABILITIES_REPO", str(tmp_path / "caps"))
    from registry.main import app
    return TestClient(app)


def _publish(client, manifest):
    return client.post("/registry/capabilities", json=manifest)


def _detail(r):
    d = r.json()["detail"]
    return " ".join(d) if isinstance(d, list) else str(d)


# ── Dependency resolution ──

def test_publish_rejects_unknown_dependency(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        m = _skill_manifest(tools=[_ref("acme/nope")])
        r = _publish(client, m)
        assert r.status_code == 422
        assert "not found" in _detail(r)
        # nothing was committed to the repo
        assert not (tmp_path / "caps" / "acme").exists()


def test_publish_rejects_latest_without_published(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _tool_manifest()).status_code == 201  # draft
        r = _publish(client, _skill_manifest(tools=[_ref("acme/t")]))
        assert r.status_code == 422
        assert "no published version" in _detail(r)


def test_publish_accepts_explicit_version_of_unpublished(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _tool_manifest()).status_code == 201  # draft
        r = _publish(client, _skill_manifest(tools=[_ref("acme/t", "1.0.0")]))
        assert r.status_code == 201, r.text


def test_publish_rejects_wrong_kind_ref(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _tool_manifest(stage="published")).status_code == 201
        m = _skill_manifest(prompt="")
        del m["spec"]["prompt"]
        m["spec"]["prompt_ref"] = _ref("acme/t")  # a tool, not a prompt
        r = _publish(client, m)
        assert r.status_code == 422
        assert "is kind 'tool', expected 'prompt'" in _detail(r)


def test_publish_rejects_unknown_top_level_dependency(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        m = _tool_manifest(dependencies=[_ref("acme/ghost")])
        r = _publish(client, m)
        assert r.status_code == 422
        assert "dependencies[0]" in _detail(r) and "not found" in _detail(r)


# ── Kind stability ──

def test_publish_rejects_kind_change_across_versions(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _tool_manifest(name="acme/k")).status_code == 201
        r = _publish(client, _prompt_manifest(name="acme/k", version="2.0.0"))
        assert r.status_code == 422
        assert "publish under a new name" in _detail(r)


# ── Secret hygiene ──

def test_publish_rejects_embedded_api_key_value(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = _publish(client, _profile_manifest(api_key_ref="sk-abc123"))
        assert r.status_code == 422
        assert "embedded API key value" in _detail(r)


def test_publish_rejects_undeclared_secret_ref(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = _publish(client, _profile_manifest(api_key_ref="OPENAI_API_KEY"))
        assert r.status_code == 422
        assert "must be declared in secrets_required" in _detail(r)


def test_publish_accepts_declared_secret_ref(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = _publish(client, _profile_manifest(
            api_key_ref="OPENAI_API_KEY", secrets_required=["OPENAI_API_KEY"]))
        assert r.status_code == 201, r.text


# ── Breaking changes: tool ──

def test_tool_param_removed_requires_major(tmp_path, monkeypatch):
    params = {
        "message": {"type": "string", "required": True},
        "note": {"type": "string"},
    }
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _tool_manifest(params=params)).status_code == 201

        r = _publish(client, _tool_manifest(
            version="1.1.0", params={"message": {"type": "string", "required": True}}))
        assert r.status_code == 422
        d = _detail(r)
        assert "removed parameter 'note'" in d and ">= 2.0.0" in d

        assert _publish(client, _tool_manifest(
            version="2.0.0", params={"message": {"type": "string", "required": True}})
        ).status_code == 201


def test_tool_param_now_required_requires_major(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _tool_manifest(
            params={"note": {"type": "string"}})).status_code == 201
        r = _publish(client, _tool_manifest(
            version="1.1.0", params={"note": {"type": "string", "required": True}}))
        assert r.status_code == 422
        assert "is now required" in _detail(r)


def test_tool_param_type_change_requires_major(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _tool_manifest(
            params={"message": {"type": "string"}})).status_code == 201
        r = _publish(client, _tool_manifest(
            version="1.1.0", params={"message": {"type": "number"}}))
        assert r.status_code == 422
        assert "type changed" in _detail(r)


def test_tool_added_optional_param_is_minor(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _tool_manifest(
            params={"message": {"type": "string"}})).status_code == 201
        r = _publish(client, _tool_manifest(
            version="1.1.0",
            params={"message": {"type": "string"}, "note": {"type": "string"}}))
        assert r.status_code == 201, r.text


def test_tool_prerelease_major_bump_allowed(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _tool_manifest(
            params={"message": {"type": "string"}})).status_code == 201
        r = _publish(client, _tool_manifest(version="2.0.0-rc.1", params={}))
        assert r.status_code == 201, r.text


# ── Breaking changes: model_profile / prompt / skill / agent / workflow ──

def test_model_profile_swap_requires_major(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _profile_manifest()).status_code == 201
        r = _publish(client, _profile_manifest(
            version="1.1.0", model="llama-3.2-3b"))
        assert r.status_code == 422
        assert "model changed" in _detail(r)


def test_model_profile_param_tweak_is_minor(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _profile_manifest()).status_code == 201
        r = _publish(client, _profile_manifest(version="1.1.0", temperature=0.2))
        assert r.status_code == 201, r.text


def test_prompt_added_variable_requires_major(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _prompt_manifest(
            variables=["a"])).status_code == 201
        r = _publish(client, _prompt_manifest(
            version="1.1.0", text="Hello {{a}} {{b}}", variables=["a", "b"]))
        assert r.status_code == 422
        assert "added variable 'b'" in _detail(r)


def test_prompt_text_edit_is_minor(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _prompt_manifest(variables=["a"])).status_code == 201
        r = _publish(client, _prompt_manifest(
            version="1.1.0", text="Hi there {{a}}!", variables=["a"]))
        assert r.status_code == 201, r.text


def test_skill_tool_ref_change_requires_major(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _tool_manifest(stage="published")).status_code == 201
        assert _publish(client, _skill_manifest(
            tools=[_ref("acme/t")])).status_code == 201
        r = _publish(client, _skill_manifest(version="1.1.0", tools=[]))
        assert r.status_code == 422
        assert "tool refs changed" in _detail(r)


def test_agent_model_ref_change_requires_major(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _profile_manifest(stage="published")).status_code == 201
        assert _publish(client, _agent_manifest()).status_code == 201
        r = _publish(client, _agent_manifest(
            version="1.1.0", model_ref=_ref("acme/mp", "1.0.0")))
        assert r.status_code == 422
        assert "model_profile changed" in _detail(r)


def test_workflow_input_removed_requires_major(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _workflow_manifest()).status_code == 201
        r = _publish(client, _workflow_manifest(
            version="1.1.0", input_schema={"type": "object", "properties": {}}))
        assert r.status_code == 422
        assert "input removed 'message'" in _detail(r)


def test_workflow_graph_only_change_is_minor(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert _publish(client, _workflow_manifest()).status_code == 201
        r = _publish(client, _workflow_manifest(
            version="1.1.0", wf=Workflow(id="w", name="w", description="tweaked")))
        assert r.status_code == 201, r.text


# ─── CLI path (batch-aware) ──────────────────────────────────────────────────

def _cli(tmp_path, monkeypatch, manifests):
    """Write manifests to temp files and run the CLI publish on them."""
    monkeypatch.setenv("AI_FORGE_REGISTRY_DB", str(tmp_path / "registry.db"))
    monkeypatch.setenv("AI_FORGE_CAPABILITIES_REPO", str(tmp_path / "caps"))
    import sqlite3
    from pathlib import Path

    files = []
    for i, m in enumerate(manifests):
        p = tmp_path / f"m{i}.json"
        p.write_text(json.dumps(m))
        files.append(p)
    rc = cli_publish(files)
    if not (tmp_path / "registry.db").exists():
        return rc, 0
    conn = sqlite3.connect(tmp_path / "registry.db")
    try:
        n = conn.execute("SELECT COUNT(*) FROM capability_versions").fetchone()[0]
    finally:
        conn.close()
    return rc, n


def test_cli_batch_resolves_siblings_in_same_call(tmp_path, monkeypatch):
    rc, n = _cli(tmp_path, monkeypatch, [
        _profile_manifest(stage="published"),
        _agent_manifest(),  # refs acme/mp@latest — only in this batch
    ])
    assert rc == 0
    assert n == 2


def test_cli_rejects_unresolvable_dependency_before_writing(tmp_path, monkeypatch):
    rc, n = _cli(tmp_path, monkeypatch, [
        _skill_manifest(tools=[_ref("acme/ghost")]),
    ])
    assert rc == 1
    assert n == 0


# ─── detect_breaking_changes unit tests ──────────────────────────────────────

def _m(d):
    return CapabilityManifest.model_validate(d)


def test_detect_tool_enum_narrowing():
    old = _m(_tool_manifest(params={
        "mode": {"type": "string", "enum": ["a", "b", "c"]}}))
    new = _m(_tool_manifest(version="2.0.0", params={
        "mode": {"type": "string", "enum": ["a", "b"]}}))
    assert any("enum values removed" in c for c in detect_breaking_changes(old, new))


def test_detect_tool_id_change():
    old = _m(_tool_manifest(tool_id="echo"))
    new = _m(_tool_manifest(version="2.0.0", tool_id="echo2"))
    assert any("tool id changed" in c for c in detect_breaking_changes(old, new))


def test_detect_workflow_ref_change():
    old = _m(_workflow_manifest())
    d = _workflow_manifest(version="2.0.0")
    d["spec"]["workflow_ref"] = "acme/other"
    assert any("workflow_ref changed" in c for c in detect_breaking_changes(old, _m(d)))


def test_detect_prompt_role_change():
    old = _m(_prompt_manifest(role="system"))
    new = _m(_prompt_manifest(version="2.0.0", role="user"))
    assert any("role changed" in c for c in detect_breaking_changes(old, new))


def test_detect_identical_is_compatible():
    old = _m(_tool_manifest(params={"x": {"type": "string"}}))
    new = _m(_tool_manifest(version="1.1.0", params={"x": {"type": "string"}}))
    assert detect_breaking_changes(old, new) == []


# ─── Secret hygiene unit tests ───────────────────────────────────────────────

def test_secret_hygiene_rejects_embedded_key_value():
    m = _m(_profile_manifest(api_key_ref="sk-abc123"))
    assert any("embedded API key value" in e for e in _secret_hygiene(m))


def test_secret_hygiene_requires_declaration():
    m = _m(_profile_manifest(api_key_ref="OPENAI_API_KEY"))
    assert any("must be declared in secrets_required" in e for e in _secret_hygiene(m))


def test_secret_hygiene_ok_when_declared():
    m = _m(_profile_manifest(
        api_key_ref="OPENAI_API_KEY", secrets_required=["OPENAI_API_KEY"]))
    assert _secret_hygiene(m) == []


def test_secret_hygiene_null_ref_is_fine():
    assert _secret_hygiene(_m(_profile_manifest())) == []


def test_secret_hygiene_workflow_embedded_model():
    wf = Workflow(id="w", name="w", models=[ModelConfig(
        id="m", name="M", provider="openai_compatible", model="x",
        api_key_ref="sk-leak")])
    m = _m(_workflow_manifest(wf=wf))
    assert any("spec.workflow.models[0]" in e and "embedded API key value" in e
               for e in _secret_hygiene(m))


# ─── Composite secret coverage ───────────────────────────────────────────────

def test_publish_rejects_skill_with_undeclared_member_secret(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        member = _tool_manifest(name="acme/gh", stage="published",
                                secrets_required=["GITHUB_TOKEN"])
        assert _publish(client, member).status_code == 201
        r = _publish(client, _skill_manifest(tools=[_ref("acme/gh")]))
        assert r.status_code == 422
        assert "GITHUB_TOKEN" in _detail(r) and "secrets_required" in _detail(r)


def test_publish_accepts_skill_declaring_member_secret(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        member = _tool_manifest(name="acme/gh", stage="published",
                                secrets_required=["GITHUB_TOKEN"])
        assert _publish(client, member).status_code == 201
        r = _publish(client, _skill_manifest(
            tools=[_ref("acme/gh")], secrets_required=["GITHUB_TOKEN"]))
        assert r.status_code == 201


def test_top_level_dependencies_exempt_from_secret_coverage(tmp_path, monkeypatch):
    """Top-level dependencies are metadata (not inlined at import) — no coverage duty."""
    with _client(tmp_path, monkeypatch) as client:
        member = _tool_manifest(name="acme/gh", stage="published",
                                secrets_required=["GITHUB_TOKEN"])
        assert _publish(client, member).status_code == 201
        r = _publish(client, _tool_manifest(
            name="acme/dep-only", dependencies=[_ref("acme/gh")]))
        assert r.status_code == 201


def test_cli_batch_covers_secrets_of_sibling_members(tmp_path):
    """Batch publish: a skill may cover secrets of a tool in the same batch."""
    import asyncio
    from registry.db import Database
    from registry.publish_checks import check_publish

    db = asyncio.run(Database.connect(tmp_path / "registry.db"))
    try:
        tool = _m(_tool_manifest(name="acme/gh", stage="published",
                                 secrets_required=["GITHUB_TOKEN"]))
        skill = _m(_skill_manifest(tools=[_ref("acme/gh", "1.0.0")],
                                   secrets_required=["GITHUB_TOKEN"]))
        errors = asyncio.run(check_publish(db, skill, batch=[tool]))
        assert errors == []
    finally:
        asyncio.run(db.close())
