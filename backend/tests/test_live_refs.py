"""Live refs (phases 1+2): build-time re-resolution of tracked entries.

Covers the resolution rules in expand.prepare_workflow_for_run — newer-minor
swap, major-skip, registry-down/pruned fallback, resume pin stability — for
pool entries, agent nodes and skills (composite projection into the pools by
id), and workflow-kind wholesale graph swaps; plus the capability_pins store
migration and the API-level notice event.
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
from schema.capability import semver_key
from schema.models import AgentSkill, ModelConfig, PromptDefinition, ToolDefinition, Workflow


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
                 artifacts: dict[tuple[str, str], dict] | None = None,
                 kinds: dict[tuple[str, str], str] | None = None):
        self.versions = versions or {}
        self.artifacts = artifacts or {}
        self.kinds = kinds or {}
        self.use_calls: list[tuple[str, str]] = []

    def list_versions(self, name: str) -> list[dict]:
        if name not in self.versions:
            raise CapabilityNotFoundError(f"capability '{name}' not found")
        return self.versions[name]

    def use(self, name: str, version: str = "latest", inline: bool = False) -> dict:
        if version == "latest":
            pubs = [v["version"] for v in self.versions.get(name, [])
                    if v.get("stage") == "published"]
            if not pubs:
                raise CapabilityNotFoundError(
                    f"capability '{name}' has no published versions")
            version = max(pubs, key=semver_key)
        self.use_calls.append((name, version))
        if (name, version) not in self.artifacts:
            raise CapabilityNotFoundError(
                f"capability '{name}' version '{version}' not found (or unpublished)")
        kind = self.kinds.get((name, version), "tool")
        return {"version": version, "kind": kind, "artifact": self.artifacts[(name, version)]}


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

        def use(self, name, version="latest", **kw):
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

        def use(self, name, version="latest", **kw):
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


# ─── phase 2: composite re-resolution (agent nodes, skills) ─────────────────

def _agent_wf(model_id: str = "m1", model_pool: list | None = None,
              tool_pool: list | None = None, with_skill: bool = False) -> Workflow:
    from schema.models import AgentNodeConfig, Node

    wf = Workflow(id="w1", name="W")
    wf.models = model_pool if model_pool is not None else [ModelConfig(
        id="m1", name="Llama", provider="openai_compatible", model="llama3")]
    wf.tools = tool_pool if tool_pool is not None else []
    cfg = AgentNodeConfig(model_id=model_id, system_prompt="old prompt")
    if with_skill:
        cfg.skills = [AgentSkill(
            name="acme/skill-a", prompt="old skill", tool_ids=["t1"],
            source_capability="acme/skill-a", source_version="1.0.0", track_latest=True)]
    cfg.source_capability = "acme/assistant"
    cfg.source_version = "1.0.0"
    cfg.track_latest = True
    wf.nodes = [Node(id="ag", type="agent", config=cfg)]
    return wf


def _agent_artifact(prompt: str = "new prompt", model: dict | None = None,
                    tools: list | None = None, skills: list | None = None) -> dict:
    return {
        "model": model if model is not None else _model_artifact(model="llama3.2"),
        "prompt": prompt,
        "tools": tools if tools is not None else [_tool_artifact()],
        "skills": skills if skills is not None else [],
    }


def test_tracked_agent_node_swaps_composite():
    wf = _agent_wf(tool_pool=[ToolDefinition(
        id="t1", name="echo", description="old desc", parameters={},
        implementation={"type": "builtin", "config": {}})])
    reg = StubRegistry(
        versions={"acme/assistant": [_pub("1.0.0"), _pub("1.1.0")]},
        artifacts={("acme/assistant", "1.1.0"): _agent_artifact(
            tools=[_tool_artifact(desc="t1 new"), {**_tool_artifact(), "id": "t2", "name": "other"}],
            skills=[{"name": "acme/skill-a", "prompt": "sk prompt",
                     "tools": [{**_tool_artifact(), "id": "t3", "name": "sk-tool"}]}]),
        },
    )
    expanded, _, pins, notices = prepare_workflow_for_run(wf, client=reg)

    assert pins == {"acme/assistant": "1.1.0"}
    cfg = expanded.nodes[0].config
    assert cfg.system_prompt == "new prompt"
    assert cfg.tool_ids == ["t1", "t2"]
    # Nested tools upserted by id: t1 replaced in place, t2/t3 appended stamped.
    by_id = {t.id: t for t in expanded.tools}
    assert by_id["t1"].description == "t1 new"
    assert by_id["t2"].source_capability == "acme/assistant"
    assert by_id["t3"].name == "sk-tool"
    # Model profile upserted into the pool.
    assert expanded.models[0].model == "llama3.2"
    # Projected skill attachment.
    sk = cfg.skills[0]
    assert sk.name == "acme/skill-a" and sk.prompt == "sk prompt" and sk.tool_ids == ["t3"]
    # Saved workflow untouched.
    assert wf.nodes[0].config.system_prompt == "old prompt"
    assert len(wf.tools) == 1
    assert any("tracked" in n for n in notices)


def test_tracked_agent_keeps_user_model_choice():
    user_model = ModelConfig(id="m-user", name="User", provider="openai_compatible", model="x")
    wf = _agent_wf(model_id="m-user", model_pool=[user_model])
    reg = StubRegistry(
        versions={"acme/assistant": [_pub("1.0.0"), _pub("1.1.0")]},
        artifacts={("acme/assistant", "1.1.0"): _agent_artifact()},
    )
    expanded, _, _, _ = prepare_workflow_for_run(wf, client=reg)
    # Deliberate user choice is never overridden; the profile is still upserted.
    assert expanded.nodes[0].config.model_id == "m-user"
    assert {m.id for m in expanded.models} == {"m-user", "m1"}


def test_tracked_agent_restores_dangling_model():
    # User deleted the imported profile from the pool — tracking restores it.
    wf = _agent_wf(model_pool=[])
    reg = StubRegistry(
        versions={"acme/assistant": [_pub("1.0.0"), _pub("1.1.0")]},
        artifacts={("acme/assistant", "1.1.0"): _agent_artifact()},
    )
    expanded, _, _, _ = prepare_workflow_for_run(wf, client=reg)
    assert expanded.nodes[0].config.model_id == "m1"
    assert {m.id for m in expanded.models} == {"m1"}


def test_tracked_skill_on_agent_swaps():
    wf = _agent_wf(with_skill=True, tool_pool=[ToolDefinition(
        id="t1", name="echo", description="old desc", parameters={},
        implementation={"type": "builtin", "config": {}})])
    # The agent itself is not tracked — only its skill attachment.
    wf.nodes[0].config.track_latest = False
    reg = StubRegistry(
        versions={
            "acme/skill-a": [_pub("1.0.0"), _pub("1.2.0")],
        },
        artifacts={("acme/skill-a", "1.2.0"): {
            "name": "acme/skill-a", "prompt": "new skill",
            "tools": [{**_tool_artifact(), "id": "t4", "name": "sk-new"}],
        }},
    )
    expanded, _, pins, notices = prepare_workflow_for_run(wf, client=reg)

    assert pins == {"acme/skill-a": "1.2.0"}
    sk = expanded.nodes[0].config.skills[0]
    assert sk.prompt == "new skill" and sk.tool_ids == ["t4"]
    assert sk.source_version == "1.0.0"  # stamp kept on the run-scoped copy
    by_id = {t.id: t for t in expanded.tools}
    assert by_id["t4"].source_capability == "acme/skill-a"
    assert any("tracked" in n for n in notices)


def test_individual_tracking_wins_over_composite_projection():
    # A pool tool that is both individually tracked and referenced inside a
    # tracked agent: the individual pass runs last and wins.
    wf = _agent_wf(tool_pool=[ToolDefinition(
        id="t1", name="echo", description="old desc", parameters={},
        implementation={"type": "builtin", "config": {}},
        source_capability="acme/echo-tool", source_version="1.0.0", track_latest=True)])
    reg = StubRegistry(
        versions={
            "acme/assistant": [_pub("1.0.0"), _pub("1.1.0")],
            "acme/echo-tool": [_pub("1.0.0"), _pub("1.3.0")],
        },
        artifacts={
            ("acme/assistant", "1.1.0"): _agent_artifact(
                tools=[_tool_artifact(desc="from agent artifact")]),
            ("acme/echo-tool", "1.3.0"): _tool_artifact(desc="from individual tracking"),
        },
    )
    expanded, _, pins, _ = prepare_workflow_for_run(wf, client=reg)

    assert pins == {"acme/assistant": "1.1.0", "acme/echo-tool": "1.3.0"}
    by_id = {t.id: t for t in expanded.tools}
    assert by_id["t1"].description == "from individual tracking"


def test_composite_resume_with_deleted_pinned_version_fails_loudly():
    wf = _agent_wf()
    with pytest.raises(CapabilityNotFoundError):
        prepare_workflow_for_run(
            wf, pins={"acme/assistant": "1.2.0"}, client=StubRegistry())


# ─── phase 2: workflow-kind wholesale swap ───────────────────────────────────

def _wf_kind_wf(graph_nodes=None) -> Workflow:
    from schema.models import Edge, Node

    nodes = graph_nodes or [
        {"id": "start", "type": "start", "config": {}},
        {"id": "end", "type": "end", "config": {}},
    ]
    wf = Workflow(id="w1", name="W")
    wf.nodes = [Node.model_validate(n) for n in nodes]
    if len(nodes) == 2:
        wf.edges = [Edge(id="e1", source_node_id="start", source_handle="default", target_node_id="end")]
    wf.source_capability = "acme/sub-wf"
    wf.source_version = "1.0.0"
    wf.track_latest = True
    return wf


def _upstream_wf_json(tag: str) -> dict:
    from schema.models import Edge, Node

    up = Workflow(id="upstream", name="Upstream")
    up.nodes = [
        Node(id="start", type="start", config={}),
        Node(id="cf", type="custom_function", config={"code": f'result["{tag}"] = 1', "output_fields": [tag]}),
        Node(id="end", type="end", config={}),
    ]
    up.edges = [
        Edge(id="e1", source_node_id="start", source_handle="default", target_node_id="cf"),
        Edge(id="e2", source_node_id="cf", source_handle="default", target_node_id="end"),
    ]
    return up.model_dump()


def test_workflow_kind_wholesale_swap():
    wf = _wf_kind_wf()
    reg = StubRegistry(
        versions={"acme/sub-wf": [_pub("1.0.0"), _pub("1.2.0")]},
        artifacts={("acme/sub-wf", "1.2.0"): _upstream_wf_json("v12")},
        kinds={("acme/sub-wf", "1.2.0"): "workflow"},
    )
    expanded, invocations, pins, notices = prepare_workflow_for_run(wf, client=reg)

    assert pins == {"acme/sub-wf": "1.2.0"}
    # Whole graph replaced; local identity preserved.
    assert expanded.id == "w1"
    assert [n.id for n in expanded.nodes] == ["start", "cf", "end"]
    assert invocations == {}
    assert any("tracked" in n and "1.2.0" in n for n in notices)
    # Saved workflow untouched.
    assert [n.id for n in wf.nodes] == ["start", "end"]


def test_workflow_kind_resume_uses_pin():
    reg = StubRegistry(
        versions={"acme/sub-wf": [_pub("1.0.0"), _pub("1.2.0")]},
        artifacts={("acme/sub-wf", "1.2.0"): _upstream_wf_json("v12")},
        kinds={("acme/sub-wf", "1.2.0"): "workflow"},
    )
    wf = _wf_kind_wf()
    _, _, pins, _ = prepare_workflow_for_run(wf, client=reg)

    # A newer version is published while the run is paused...
    reg.versions["acme/sub-wf"].append(_pub("1.3.0"))
    reg.artifacts[("acme/sub-wf", "1.3.0")] = _upstream_wf_json("v13")
    reg.kinds[("acme/sub-wf", "1.3.0")] = "workflow"

    # ...resume swaps in exactly the pinned version's graph.
    expanded, _, pins2, notices = prepare_workflow_for_run(wf, pins=pins, client=reg)
    assert pins2 == pins
    assert ("acme/sub-wf", "1.2.0") in reg.use_calls
    assert not any("acme/sub-wf" in n for n in notices)
    assert any("v12" in (getattr(n.config, "code", None) or "") for n in expanded.nodes if n.type == "custom_function")


def test_workflow_kind_ref_only_keeps_saved():
    wf = _wf_kind_wf()
    reg = StubRegistry(
        versions={"acme/sub-wf": [_pub("1.0.0"), _pub("1.2.0")]},
        artifacts={("acme/sub-wf", "1.2.0"): {"workflow_ref": "graphs/sub.json"}},
        kinds={("acme/sub-wf", "1.2.0"): "workflow"},
    )
    expanded, _, pins, notices = prepare_workflow_for_run(wf, client=reg)

    assert expanded is wf  # nothing to swap in — saved graph runs
    assert pins == {"acme/sub-wf": "1.2.0"}
    assert any("workflow_ref" in n for n in notices)


def test_workflow_kind_major_jump_is_skipped():
    wf = _wf_kind_wf()
    reg = StubRegistry(
        versions={"acme/sub-wf": [_pub("1.0.0"), _pub("2.0.0")]},
        artifacts={("acme/sub-wf", "2.0.0"): _upstream_wf_json("v2")},
        kinds={("acme/sub-wf", "2.0.0"): "workflow"},
    )
    expanded, _, pins, notices = prepare_workflow_for_run(wf, client=reg)

    assert expanded is wf
    assert pins == {"acme/sub-wf": "1.0.0"}
    assert any("major jump" in n for n in notices)


def test_workflow_swap_expands_inner_invokes():
    from schema.models import Edge, Node

    # The upstream graph contains an invoke node — swap must precede expansion.
    up = _upstream_wf_json("v12")
    up["nodes"].insert(2, {
        "id": "inv", "type": "invoke",
        "config": {"capability": "acme/inner", "version": "latest"},
    })
    up["edges"] = [
        {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "cf"},
        {"id": "e2", "source_node_id": "cf", "source_handle": "default", "target_node_id": "inv"},
        {"id": "e3", "source_node_id": "inv", "source_handle": "default", "target_node_id": "end"},
    ]
    sub = Workflow(id="wf-inner", name="inner")
    sub.nodes = [Node(id="start", type="start", config={}), Node(id="end", type="end", config={})]
    sub.edges = [Edge(id="e1", source_node_id="start", source_handle="default", target_node_id="end")]

    wf = _wf_kind_wf()
    reg = StubRegistry(
        versions={"acme/sub-wf": [_pub("1.0.0"), _pub("1.2.0")], "acme/inner": [_pub("1.0.0")]},
        artifacts={
            ("acme/sub-wf", "1.2.0"): up,
            ("acme/inner", "1.0.0"): sub.model_dump(),
        },
        kinds={("acme/sub-wf", "1.2.0"): "workflow", ("acme/inner", "1.0.0"): "workflow"},
    )
    expanded, invocations, pins, _ = prepare_workflow_for_run(wf, client=reg)

    assert pins == {"acme/sub-wf": "1.2.0", "acme/inner": "1.0.0"}
    assert "inv" in invocations
    # The swapped-in graph is expanded: inner nodes are spliced in with prefixes.
    ids = {n.id for n in expanded.nodes}
    assert any(i.startswith("inv__") for i in ids)
