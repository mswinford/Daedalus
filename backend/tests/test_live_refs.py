"""Live refs (phase 1): build-time re-resolution of tracked pool entries.

Covers the resolution rules in expand.prepare_workflow_for_run — newer-minor
swap, major-skip, registry-down/pruned fallback, resume pin stability — plus
the capability_pins store migration and the API-level notice event.
"""
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.capability_client import (
    CapabilityClient,
    CapabilityFetchError,
    CapabilityNotFoundError,
)
from app.engine.expand import prepare_workflow_for_run
from schema.models import ModelConfig, PromptDefinition, ToolDefinition, Workflow


# ─── helpers ─────────────────────────────────────────────────────────────────

def _tool_artifact(desc: str = "new desc") -> dict:
    return {
        "id": "t1", "name": "echo", "description": desc,
        "parameters": {}, "implementation": {"type": "builtin", "config": {}},
    }


def _model_artifact(model: str = "llama3.2") -> dict:
    return {
        "id": "m1", "name": "Llama", "provider": "openai_compatible",
        "model": model, "base_url": "http://new-host",
    }


def _prompt_artifact(text: str = "new prompt") -> dict:
    return {"name": "sys", "text": text, "variables": ["role"]}


def _tracked_wf() -> Workflow:
    wf = Workflow(id="w1", name="W")
    wf.tools = [ToolDefinition(
        id="t1", name="echo", description="old desc", parameters={},
        implementation={"type": "builtin", "config": {}},
        source_capability="acme/echo-tool", source_version="1.0.0", track_latest=True)]
    wf.models = [ModelConfig(
        id="m1", name="Llama", provider="openai_compatible", model="llama3",
        base_url="http://old-host",
        source_capability="acme/llama-profile", source_version="1.0.0", track_latest=True)]
    wf.prompts = [PromptDefinition(
        id="p1", name="sys", text="old prompt", variables=[],
        source_capability="acme/sys-prompt", source_version="1.0.0", track_latest=True)]
    return wf


class StubRegistry:
    """In-memory stand-in for the registry client (list_versions + use)."""

    def __init__(self, versions: dict[str, list[dict]] | None = None,
                 artifacts: dict[tuple[str, str], dict] | None = None):
        self.versions = versions or {}
        self.artifacts = artifacts or {}
        self.use_calls: list[tuple[str, str]] = []

    def list_versions(self, name: str) -> list[dict]:
        if name not in self.versions:
            raise CapabilityNotFoundError(f"capability '{name}' not found")
        return self.versions[name]

    def use(self, name: str, version: str = "latest") -> dict:
        self.use_calls.append((name, version))
        if (name, version) not in self.artifacts:
            raise CapabilityNotFoundError(
                f"capability '{name}' version '{version}' not found (or unpublished)")
        return {"version": version, "kind": "tool", "artifact": self.artifacts[(name, version)]}


def _pub(version: str) -> dict:
    return {"version": version, "stage": "published"}


# ─── resolution rules ────────────────────────────────────────────────────────

def test_tracked_entries_swap_to_newer_minor():
    wf = _tracked_wf()
    reg = StubRegistry(
        versions={
            "acme/echo-tool": [_pub("1.0.0"), _pub("1.2.0")],
            "acme/llama-profile": [_pub("1.0.0"), _pub("1.1.3")],
            "acme/sys-prompt": [_pub("1.0.0"), _pub("1.4.0")],
        },
        artifacts={
            ("acme/echo-tool", "1.2.0"): _tool_artifact(),
            ("acme/llama-profile", "1.1.3"): _model_artifact(),
            ("acme/sys-prompt", "1.4.0"): _prompt_artifact(),
        },
    )
    expanded, invocations, pins, notices = prepare_workflow_for_run(wf, client=reg)

    assert invocations == {}
    assert pins == {
        "acme/echo-tool": "1.2.0",
        "acme/llama-profile": "1.1.3",
        "acme/sys-prompt": "1.4.0",
    }
    # Content swapped, ids and stamps preserved (refs by id stay valid).
    assert expanded.tools[0].description == "new desc"
    assert expanded.tools[0].id == "t1"
    assert expanded.tools[0].source_version == "1.0.0"
    assert expanded.models[0].model == "llama3.2"
    assert expanded.models[0].base_url == "http://new-host"
    assert expanded.prompts[0].text == "new prompt"
    assert expanded.prompts[0].variables == ["role"]
    # The saved workflow object is never mutated.
    assert wf.tools[0].description == "old desc"
    assert wf.models[0].model == "llama3"
    assert wf.prompts[0].text == "old prompt"
    assert len(notices) == 3
    assert all("tracked" in n for n in notices)


def test_major_jump_is_skipped():
    wf = _tracked_wf()
    reg = StubRegistry(
        versions={
            "acme/echo-tool": [_pub("1.0.0"), _pub("2.0.0")],
            "acme/llama-profile": [_pub("1.0.0")],
            "acme/sys-prompt": [_pub("1.0.0")],
        },
        artifacts={("acme/echo-tool", "2.0.0"): _tool_artifact()},
    )
    expanded, _, pins, notices = prepare_workflow_for_run(wf, client=reg)

    # Only the tool capability has a newer major; the other two have no newer
    # in-major version at all (quiet keep-current).
    assert expanded.tools[0].description == "old desc"
    assert pins["acme/echo-tool"] == "1.0.0"
    assert any("major jump" in n and "2.0.0" in n for n in notices)
    assert len(notices) == 1  # only the major skip is reported


def test_prefers_newer_minor_over_higher_major():
    wf = _tracked_wf()
    reg = StubRegistry(
        versions={"acme/echo-tool": [_pub("1.0.0"), _pub("1.5.0"), _pub("2.0.0")]},
        artifacts={("acme/echo-tool", "1.5.0"): _tool_artifact(desc="v1.5")},
    )
    expanded, _, pins, notices = prepare_workflow_for_run(wf, client=reg)

    assert pins["acme/echo-tool"] == "1.5.0"
    assert expanded.tools[0].description == "v1.5"
    assert reg.use_calls.count(("acme/echo-tool", "1.5.0")) == 1


def test_registry_down_falls_back_to_inlined():
    class DownRegistry:
        def list_versions(self, name):
            raise CapabilityFetchError("registry unreachable")

        def use(self, name, version="latest"):
            raise CapabilityFetchError("registry unreachable")

    wf = _tracked_wf()
    expanded, _, pins, notices = prepare_workflow_for_run(wf, client=DownRegistry())

    assert expanded.tools[0].description == "old desc"
    assert expanded.models[0].model == "llama3"
    assert expanded.prompts[0].text == "old prompt"
    # Pins record the stamped versions so resume stays deterministic.
    assert pins == {
        "acme/echo-tool": "1.0.0",
        "acme/llama-profile": "1.0.0",
        "acme/sys-prompt": "1.0.0",
    }
    assert len(notices) == 3
    assert all("registry unavailable" in n for n in notices)


def test_pruned_capability_falls_back_on_fresh_run():
    class PrunedRegistry:
        def list_versions(self, name):
            raise CapabilityNotFoundError(f"capability '{name}' not found")

        def use(self, name, version="latest"):
            raise CapabilityNotFoundError(f"capability '{name}' not found")

    wf = _tracked_wf()
    expanded, _, pins, notices = prepare_workflow_for_run(wf, client=PrunedRegistry())

    assert expanded.tools[0].description == "old desc"
    assert pins["acme/echo-tool"] == "1.0.0"
    assert any("registry unavailable" in n for n in notices)


# ─── resume / restart pin stability ──────────────────────────────────────────

def test_resume_uses_pinned_version_not_newer():
    reg = StubRegistry(
        versions={
            "acme/echo-tool": [_pub("1.0.0"), _pub("1.2.0")],
            "acme/llama-profile": [_pub("1.0.0")],
            "acme/sys-prompt": [_pub("1.0.0")],
        },
        artifacts={
            ("acme/echo-tool", "1.2.0"): _tool_artifact(desc="v1.2"),
            ("acme/llama-profile", "1.0.0"): _model_artifact(model="llama3"),
            ("acme/sys-prompt", "1.0.0"): _prompt_artifact(text="old prompt"),
        },
    )
    wf = _tracked_wf()
    _, _, pins, _ = prepare_workflow_for_run(wf, client=reg)
    assert pins["acme/echo-tool"] == "1.2.0"

    # A newer version is published while the run is paused...
    reg.versions["acme/echo-tool"].append(_pub("1.3.0"))
    reg.artifacts[("acme/echo-tool", "1.3.0")] = _tool_artifact(desc="v1.3")

    # ...resume re-resolves with the stored pins and fetches exactly 1.2.0.
    expanded, _, pins2, notices = prepare_workflow_for_run(wf, pins=pins, client=reg)
    assert pins2["acme/echo-tool"] == "1.2.0"
    assert expanded.tools[0].description == "v1.2"
    assert ("acme/echo-tool", "1.2.0") in reg.use_calls
    assert not any("acme/echo-tool" in n for n in notices)


def test_resume_with_deleted_pinned_version_fails_loudly():
    wf = _tracked_wf()
    with pytest.raises(CapabilityNotFoundError):
        prepare_workflow_for_run(
            wf, pins={"acme/echo-tool": "1.2.0"}, client=StubRegistry())


# ─── opt-in semantics ────────────────────────────────────────────────────────

def test_untracked_entries_pass_through_without_registry():
    wf = _tracked_wf()
    for entry in list(wf.tools) + list(wf.models) + list(wf.prompts):
        entry.track_latest = False
    expanded, invocations, pins, notices = prepare_workflow_for_run(wf)
    assert expanded is wf and invocations == {} and pins == {} and notices == []


def test_track_latest_without_stamp_is_ignored():
    wf = _tracked_wf()
    for entry in list(wf.tools) + list(wf.models) + list(wf.prompts):
        entry.source_capability = None
    expanded, _, pins, notices = prepare_workflow_for_run(wf)
    assert pins == {} and notices == []  # nothing trackable → no registry calls


# ─── store migration ─────────────────────────────────────────────────────────

def test_store_migration_renames_invoke_pins(tmp_path):
    from app.runs.store import _store_connect

    db = tmp_path / "checkpoints.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, status TEXT NOT NULL,
            input_data TEXT NOT NULL DEFAULT '{}', output_data TEXT, error TEXT,
            total_tokens_input INTEGER NOT NULL DEFAULT 0,
            total_tokens_output INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
            invoke_pins TEXT, capability_usage TEXT,
            started_at REAL NOT NULL, completed_at REAL
        )
    """)
    conn.execute(
        "INSERT INTO runs (run_id, workflow_id, status, input_data, invoke_pins, started_at) "
        "VALUES ('r1', 'w1', 'completed', '{}', ?, 1.0)",
        (json.dumps({"acme/sub": "1.0.0"}),),
    )
    conn.commit()
    conn.close()

    conn = _store_connect(str(db))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "capability_pins" in cols and "invoke_pins" not in cols
        row = conn.execute(
            "SELECT capability_pins FROM runs WHERE run_id='r1'"
        ).fetchone()
        assert json.loads(row[0]) == {"acme/sub": "1.0.0"}
    finally:
        conn.close()


def test_store_migration_adds_column_on_very_old_db(tmp_path):
    from app.runs.store import _store_connect

    db = tmp_path / "checkpoints.db"
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, status TEXT NOT NULL,
            input_data TEXT NOT NULL DEFAULT '{}', output_data TEXT, error TEXT,
            total_tokens_input INTEGER NOT NULL DEFAULT 0,
            total_tokens_output INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
            capability_usage TEXT, started_at REAL NOT NULL, completed_at REAL
        )
    """)
    conn.commit()
    conn.close()

    conn = _store_connect(str(db))
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "capability_pins" in cols
    finally:
        conn.close()


# ─── API level: notice event + pin persistence ──────────────────────────────

@pytest.fixture()
def live_refs_client(tmp_path, monkeypatch):
    from app import runs as runs_module
    from app.api import workflows as wf_module
    from app.main import app

    monkeypatch.setattr(wf_module.settings, "workflows_dir", tmp_path)
    reg = StubRegistry(
        versions={"acme/echo-tool": [_pub("1.0.0"), _pub("1.2.0")]},
        artifacts={("acme/echo-tool", "1.2.0"): _tool_artifact()},
    )
    monkeypatch.setattr(CapabilityClient, "list_versions", reg.list_versions)
    monkeypatch.setattr(CapabilityClient, "use", reg.use)

    wf = {
        "id": "lr-wf", "name": "LR WF",
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "config": {}},
            {"id": "cf", "type": "custom_function", "position": {"x": 200, "y": 0},
             "config": {"code": 'result["v"] = 1', "output_fields": ["v"]}},
            {"id": "end", "type": "end", "position": {"x": 400, "y": 0}, "config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "cf"},
            {"id": "e2", "source_node_id": "cf", "source_handle": "default", "target_node_id": "end"},
        ],
        "tools": [{
            "id": "t1", "name": "echo", "description": "old desc", "parameters": {},
            "implementation": {"type": "builtin", "config": {}},
            "source_capability": "acme/echo-tool", "source_version": "1.0.0",
            "track_latest": True,
        }],
    }
    (tmp_path / "lr-wf.json").write_text(json.dumps(wf))

    from app.runs.api import RUNS
    RUNS.clear()
    runs_module.flush_store()
    with TestClient(app) as client:
        yield client, reg
    RUNS.clear()


def test_run_emits_notice_and_persists_pins(live_refs_client, tmp_path):
    client, _reg = live_refs_client

    resp = client.post("/api/workflows/lr-wf/run", json={})
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]

    deadline = time.time() + 10
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] == "completed":
            break
        time.sleep(0.05)
    assert body["status"] == "completed"

    # The resolved version is what the run recorded, not the stamped one.
    assert body["capability_pins"] == {"acme/echo-tool": "1.2.0"}
    assert body["capability_usage"]["acme/echo-tool"] == "1.2.0"

    notices = [e for e in body["events"] if e["type"] == "capability_notice"]
    assert len(notices) == 1
    assert "acme/echo-tool" in notices[0]["data"]["message"]
    assert "1.2.0" in notices[0]["data"]["message"]

    # The saved workflow JSON is never mutated by tracking.
    on_disk = json.loads((tmp_path / "lr-wf.json").read_text())
    assert on_disk["tools"][0]["description"] == "old desc"
    assert on_disk["tools"][0]["track_latest"] is True
