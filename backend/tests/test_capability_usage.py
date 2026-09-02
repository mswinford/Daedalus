"""Per-run capability-usage snapshots: which capability versions a run uses
(invoke pins ∪ model/tool provenance), persisted with the run, surviving both
recovery paths (finished and paused) across a simulated restart."""
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import workflows as wf_module
from app.capability_client import CapabilityClient, CapabilityNotFoundError
from schema.models import (
    Edge,
    HumanInputField,
    HumanInLoopNodeConfig,
    Node,
    StateField,
    StateFieldType,
    StateSchema,
    Workflow,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _wait_for_status(client: TestClient, run_id: str, status: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] == status:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} never reached '{status}' (last: {body['status']})")


def _sub(hil: bool = False) -> Workflow:
    """start → cf → [hil] → end."""
    nodes = [
        Node(id="start", type="start", config={}),
        Node(id="cf", type="custom_function",
             config={"code": 'result["y"] = state["data"]["x"] + 1', "output_fields": ["y"]}),
    ]
    edges = [Edge(id="e-cf", source_node_id="start", source_handle="default", target_node_id="cf")]
    prev = "cf"
    if hil:
        nodes.append(Node(id="hil", type="human_in_loop",
                          config=HumanInLoopNodeConfig(
                              input_fields=[HumanInputField(name="note", label="Note", type="text")],
                              output_fields=["note"])))
        edges.append(Edge(id="e-hil", source_node_id=prev, source_handle="default", target_node_id="hil"))
        prev = "hil"
    nodes.append(Node(id="end", type="end", config={}))
    edges.append(Edge(id="e-end", source_node_id=prev, source_handle="default", target_node_id="end"))
    return Workflow(
        id="wf-sub", name="sub",
        state_schema=StateSchema(fields=[StateField(name="x", type=StateFieldType.NUMBER, required=True)]),
        nodes=nodes, edges=edges,
    )


def _provenance_entries() -> tuple[list[dict], list[dict]]:
    """Workflow models/tools stamped with registry provenance (one name-only)."""
    models = [
        {"id": "m1", "name": "Llama", "provider": "openai_compatible", "model": "llama-3",
         "source_capability": "acme/llama-profile", "source_version": "0.9.0"},
    ]
    tools = [
        {"id": "t1", "name": "search", "description": "Search things", "parameters": {},
         "implementation": {"type": "builtin", "config": {}},
         "source_capability": "acme/search-tool", "source_version": "1.2.3"},
        {"id": "t2", "name": "calc", "description": "Calculate", "parameters": {},
         "implementation": {"type": "builtin", "config": {}},
         "source_capability": "acme/calc-tool", "source_version": None},
    ]
    return models, tools


def _parent_json(wf_id: str) -> dict:
    """start → invoke(acme/sub) → end, with provenance-stamped models/tools."""
    models, tools = _provenance_entries()
    return {
        "id": wf_id, "name": wf_id,
        "models": models, "tools": tools,
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "inv", "type": "invoke",
             "config": {"capability": "acme/sub", "version": "latest",
                        "input_mapping": [{"source": "data.x", "target": "x"}],
                        "output_field": "sub"}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "s", "source_node_id": "start", "source_handle": "default", "target_node_id": "inv"},
            {"id": "e", "source_node_id": "inv", "source_handle": "default", "target_node_id": "end"},
        ],
    }


def _plain_json(wf_id: str) -> dict:
    """Legacy workflow: no invoke nodes, no models/tools, no provenance."""
    return {
        "id": wf_id, "name": wf_id,
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "cf", "type": "custom_function",
             "config": {"code": 'result["v"] = 1', "output_fields": ["v"]}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "cf"},
            {"id": "e2", "source_node_id": "cf", "source_handle": "default", "target_node_id": "end"},
        ],
    }


def _provenance_only_json(wf_id: str) -> dict:
    """start → cf → end with provenance-stamped models/tools but no invokes."""
    models, tools = _provenance_entries()
    return {
        "id": wf_id, "name": wf_id,
        "models": models, "tools": tools,
        "nodes": [
            {"id": "start", "type": "start", "config": {}},
            {"id": "cf", "type": "custom_function",
             "config": {"code": 'result["v"] = 1', "output_fields": ["v"]}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "cf"},
            {"id": "e2", "source_node_id": "cf", "source_handle": "default", "target_node_id": "end"},
        ],
    }


class _FakeRegistry:
    """In-memory stand-in for the registry's GET /use endpoint."""

    def __init__(self):
        self.store: dict[str, dict[str, dict]] = {}  # name -> version -> use response

    def add(self, name: str, sub: Workflow, version: str) -> None:
        self.store.setdefault(name, {})[version] = {
            "name": name, "version": version, "kind": "workflow",
            "artifact": sub.model_dump(),
        }

    def use(self, name: str, version: str = "latest") -> dict:
        versions = self.store.get(name)
        if not versions:
            raise CapabilityNotFoundError(f"capability '{name}' version '{version}' not found (or unpublished)")
        if version != "latest":
            if version not in versions:
                raise CapabilityNotFoundError(
                    f"capability '{name}' version '{version}' not found (or unpublished)")
            return versions[version]
        return versions[max(versions)]


def _db_usage(run_id: str) -> dict | None:
    """Read the run's capability_usage column straight from the store DB."""
    from app.config import get_settings

    conn = sqlite3.connect(str(get_settings().checkpoint_db), timeout=5.0)
    try:
        row = conn.execute(
            "SELECT capability_usage FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"no store row for run {run_id}"
    return json.loads(row[0]) if row[0] else None


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_runs():
    from app.runs.api import RUNS
    RUNS.clear()
    yield
    RUNS.clear()


@pytest.fixture()
def usage_client(tmp_path, monkeypatch):
    """App with on-disk workflows and a fake registry behind /use."""
    from app import runs as runs_module

    monkeypatch.setattr(wf_module.settings, "workflows_dir", tmp_path)
    reg = _FakeRegistry()
    monkeypatch.setattr(CapabilityClient, "use",
                        lambda self, name, version="latest": reg.use(name, version))
    (tmp_path / "inv-wf.json").write_text(json.dumps(_parent_json("inv-wf")))
    (tmp_path / "plain-wf.json").write_text(json.dumps(_plain_json("plain-wf")))
    (tmp_path / "prov-wf.json").write_text(json.dumps(_provenance_only_json("prov-wf")))
    runs_module.flush_store()

    with TestClient(app) as client:
        yield client, reg


def _start_run(client: TestClient, wf_id: str, payload: dict | None = None) -> str:
    resp = client.post(f"/api/workflows/{wf_id}/run", json=payload if payload is not None else {"x": 1})
    assert resp.status_code == 202, resp.text
    return resp.json()["run_id"]


# Full union: provenance (model + tools, one name-only) ∪ resolved invoke pin.
EXPECTED_USAGE = {
    "acme/llama-profile": "0.9.0",
    "acme/search-tool": "1.2.3",
    "acme/calc-tool": None,
    "acme/sub": "1.0.0",
}


# ─── 1: snapshot is the union of pins + provenance ───────────────────────────

def test_run_with_pins_and_provenance_snapshots_usage(usage_client):
    client, reg = usage_client
    from app import runs as runs_module

    reg.add("acme/sub", _sub(), "1.0.0")
    run_id = _start_run(client, "inv-wf")
    body = _wait_for_status(client, run_id, "completed")
    assert body["capability_usage"] == EXPECTED_USAGE

    runs_module.flush_store()
    assert _db_usage(run_id) == EXPECTED_USAGE


# ─── 2: legacy workflow → NULL in the store ──────────────────────────────────

def test_legacy_workflow_stores_null_usage(usage_client):
    client, _reg = usage_client
    from app import runs as runs_module

    run_id = _start_run(client, "plain-wf", payload={})
    body = _wait_for_status(client, run_id, "completed")
    assert body["capability_usage"] is None

    runs_module.flush_store()
    assert _db_usage(run_id) is None


# ─── 3: finished run's snapshot survives a restart ───────────────────────────

def test_finished_run_usage_survives_restart(usage_client):
    client, reg = usage_client
    from app import runs as runs_module

    reg.add("acme/sub", _sub(), "1.0.0")
    run_id = _start_run(client, "inv-wf")
    _wait_for_status(client, run_id, "completed")
    runs_module.flush_store()

    # Simulate a process restart: in-memory RUNS gone, fresh app instance.
    runs_module.RUNS.clear()
    with TestClient(app) as restarted:
        body = restarted.get(f"/api/runs/{run_id}").json()
        assert body["status"] == "completed"
        assert body["capability_usage"] == EXPECTED_USAGE


# ─── 4: paused run's snapshot survives a restart ─────────────────────────────

def test_paused_run_usage_survives_restart(usage_client):
    client, reg = usage_client
    from app import runs as runs_module

    reg.add("acme/sub", _sub(hil=True), "1.0.0")
    run_id = _start_run(client, "inv-wf")
    _wait_for_status(client, run_id, "paused")
    runs_module.flush_store()

    # Simulate a process restart: in-memory RUNS gone, fresh app instance.
    runs_module.RUNS.clear()
    with TestClient(app) as restarted:
        # The rebuilt record keeps its attribution...
        assert runs_module.RUNS[run_id].capability_usage == EXPECTED_USAGE
        # ...and GET exposes it while the run is still paused.
        body = restarted.get(f"/api/runs/{run_id}").json()
        assert body["status"] == "paused"
        assert body["capability_usage"] == EXPECTED_USAGE


# ─── 5: GET /runs/{id} exposes capability_usage (provenance-only workflow) ──

def test_get_run_exposes_capability_usage(usage_client):
    client, _reg = usage_client

    run_id = _start_run(client, "prov-wf", payload={})
    body = _wait_for_status(client, run_id, "completed")
    # No invoke nodes: the snapshot is exactly the provenance entries.
    assert body["capability_usage"] == {
        "acme/llama-profile": "0.9.0",
        "acme/search-tool": "1.2.3",
        "acme/calc-tool": None,
    }
