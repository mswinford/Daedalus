"""Phase 2 tests: invoke nodes through the real API — HIL end-to-end (WS
events, pause payload, resume), restart-recovery with stored pins, version
pinning semantics (latest re-resolve, deleted-pin fail-loud), HIL timeout
inside a region, and token accounting across the frame boundary."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import workflows as wf_module
from app.capability_client import CapabilityClient, CapabilityNotFoundError
from app.engine.llm import LLMProvider, LLMResult
from schema.models import (
    AgentNodeConfig,
    Edge,
    HumanInputField,
    HumanInLoopNodeConfig,
    ModelConfig,
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


def _sub(cf_code: str | None = 'result["y"] = state["data"]["x"] + 1',
         hil: bool = False, hil_timeout: int | None = None, agent: bool = False) -> Workflow:
    """start → [cf] → [hil] → [agent] → end (only the requested middle nodes)."""
    nodes = [Node(id="start", type="start", config={})]
    edges = []
    prev = "start"
    if cf_code is not None:
        nodes.append(Node(id="cf", type="custom_function",
                          config={"code": cf_code, "output_fields": ["y"]}))
        edges.append(Edge(id="e-cf", source_node_id=prev, source_handle="default", target_node_id="cf"))
        prev = "cf"
    if hil:
        nodes.append(Node(id="hil", type="human_in_loop",
                          config=HumanInLoopNodeConfig(
                              input_fields=[HumanInputField(name="note", label="Note", type="text")],
                              output_fields=["note"], timeout_seconds=hil_timeout)))
        edges.append(Edge(id="e-hil", source_node_id=prev, source_handle="default", target_node_id="hil"))
        prev = "hil"
    if agent:
        nodes.append(Node(id="agent", type="agent",
                          config=AgentNodeConfig(model_id="m1", system_prompt="hi")))
        edges.append(Edge(id="e-agent", source_node_id=prev, source_handle="default", target_node_id="agent"))
        prev = "agent"
    nodes.append(Node(id="end", type="end", config={}))
    edges.append(Edge(id="e-end", source_node_id=prev, source_handle="default", target_node_id="end"))
    wf = Workflow(
        id="wf-sub", name="sub",
        state_schema=None if agent else StateSchema(
            fields=[StateField(name="x", type=StateFieldType.NUMBER, required=True)]),
        nodes=nodes, edges=edges,
    )
    if agent:
        wf.models = [ModelConfig(id="m1", name="M", provider="openai_compatible", model="x")]
    return wf


def _parent_json(wf_id: str, with_transform: bool) -> dict:
    nodes = [
        {"id": "start", "type": "start", "config": {}},
        {"id": "inv", "type": "invoke",
         "config": {"capability": "acme/sub", "version": "latest",
                    "input_mapping": [{"source": "data.x", "target": "x"}],
                    "output_field": "sub"}},
    ]
    edges = [{"id": "s", "source_node_id": "start", "source_handle": "default", "target_node_id": "inv"}]
    prev = "inv"
    if with_transform:
        nodes.append({"id": "t", "type": "transform",
                      "config": {"mode": "template", "template": "{{data.x}}|{{data.sub.y}}",
                                 "output_field": "output"}})
        edges.append({"id": "i", "source_node_id": prev, "source_handle": "default", "target_node_id": "t"})
        prev = "t"
    nodes.append({"id": "end", "type": "end", "config": {}})
    edges.append({"id": "e", "source_node_id": prev, "source_handle": "default", "target_node_id": "end"})
    return {"id": wf_id, "name": wf_id, "nodes": nodes, "edges": edges}


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


class _FakeProvider(LLMProvider):
    def __init__(self, result: LLMResult):
        self._result = result
        self.calls = 0

    async def chat(self, messages, tools=None, temperature=None, max_tokens=None):
        self.calls += 1
        return self._result

    async def chat_stream(self, *a, **k):
        raise NotImplementedError


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_runs():
    from app.runs.api import RUNS
    RUNS.clear()
    yield
    RUNS.clear()


@pytest.fixture()
def invoke_client(tmp_path, monkeypatch):
    """App with on-disk parent workflows and a fake registry behind /use."""
    from app import runs as runs_module

    monkeypatch.setattr(wf_module.settings, "workflows_dir", tmp_path)
    reg = _FakeRegistry()
    monkeypatch.setattr(CapabilityClient, "use",
                        lambda self, name, version="latest": reg.use(name, version))
    (tmp_path / "inv-wf.json").write_text(json.dumps(_parent_json("inv-wf", with_transform=True)))
    (tmp_path / "inv-tok-wf.json").write_text(json.dumps(_parent_json("inv-tok-wf", with_transform=False)))
    runs_module.flush_store()

    with TestClient(app) as client:
        yield client, reg


def _start_run(client: TestClient, wf_id: str = "inv-wf", payload: dict | None = None) -> str:
    resp = client.post(f"/api/workflows/{wf_id}/run", json=payload if payload is not None else {"x": 1})
    assert resp.status_code == 202, resp.text
    return resp.json()["run_id"]


# ─── item 1: HIL end-to-end through the API ──────────────────────────────────

def test_hil_in_region_end_to_end_via_api(invoke_client):
    client, reg = invoke_client
    reg.add("acme/sub", _sub(hil=True), "1.0.0")

    run_id = _start_run(client)
    body = _wait_for_status(client, run_id, "paused")

    # Pause payload identifies the region's prefixed HIL node and the parent wf.
    iv = body["interrupt_value"]
    assert iv["node_id"] == "inv__hil"
    assert iv["workflow_id"] == "inv-wf"

    # Pre-pause events carry the entry gate and prefixed inner node ids.
    starts = [e["node_id"] for e in body["events"] if e["type"] == "node_start"]
    assert "inv" in starts and "inv__cf" in starts
    assert body["events"][-1]["type"] == "human_request"

    # WS replay of a paused run delivers the full history with unique seqs.
    with client.websocket_connect(f"/api/runs/{run_id}/events") as ws:
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] == "human_request":
                break
    seqs = [e["seq"] for e in events]
    assert len(set(seqs)) == len(seqs)

    resp = client.post(f"/api/runs/{run_id}/resume", json={"note": "hi"})
    assert resp.status_code == 202
    body = _wait_for_status(client, run_id, "completed")
    # Sub frame captured at the exit gate; parent frame restored intact.
    assert body["output_data"]["data"]["sub"] == {"x": 1, "y": 2, "note": "hi"}
    assert body["output_data"]["data"]["x"] == 1
    assert body["output_data"]["output"] == "1|2"
    # The exit gate ran after the resume and wrote the canonical result.
    ends = [e["node_id"] for e in body["events"] if e["type"] == "node_end"]
    assert "inv__exit" in ends


# ─── item 2: restart-recovery with stored pins ───────────────────────────────

def test_paused_invoke_run_survives_restart(invoke_client):
    client, reg = invoke_client
    from app import runs as runs_module

    reg.add("acme/sub", _sub(hil=True), "1.0.0")
    run_id = _start_run(client)
    _wait_for_status(client, run_id, "paused")
    runs_module.flush_store()

    # Simulate a process restart: in-memory RUNS gone, fresh app instance.
    runs_module.RUNS.clear()
    with TestClient(app) as restarted:
        paused = restarted.get("/api/runs/paused").json()
        assert [r["id"] for r in paused] == [run_id]
        assert paused[0]["node_id"] == "inv__hil"

        body = restarted.get(f"/api/runs/{run_id}").json()
        assert body["status"] == "paused"
        assert body["interrupt_value"]["node_id"] == "inv__hil"

        resp = restarted.post(f"/api/runs/{run_id}/resume", json={"note": "back"})
        assert resp.status_code == 202
        body = _wait_for_status(restarted, run_id, "completed")
        assert body["output_data"]["data"]["sub"] == {"x": 1, "y": 2, "note": "back"}
        assert body["output_data"]["output"] == "1|2"


# ─── item 3: version-pinning semantics ───────────────────────────────────────

def test_resume_uses_pinned_version_new_run_resolves_latest(invoke_client):
    client, reg = invoke_client
    reg.add("acme/sub", _sub(hil=True), "1.0.0")  # y = x + 1

    run_id = _start_run(client)
    _wait_for_status(client, run_id, "paused")

    # A newer version is published while the run is paused: latest → 2.0.0.
    reg.add("acme/sub", _sub(cf_code='result["y"] = state["data"]["x"] + 100', hil=True), "2.0.0")

    client.post(f"/api/runs/{run_id}/resume", json={"note": "n"})
    body = _wait_for_status(client, run_id, "completed")
    assert body["output_data"]["data"]["sub"]["y"] == 2  # pinned v1 behavior

    run_id2 = _start_run(client)
    _wait_for_status(client, run_id2, "paused")
    client.post(f"/api/runs/{run_id2}/resume", json={"note": "n"})
    body2 = _wait_for_status(client, run_id2, "completed")
    assert body2["output_data"]["data"]["sub"]["y"] == 101  # fresh resolve → v2


def test_resume_with_deleted_pin_fails_loudly(invoke_client):
    client, reg = invoke_client
    reg.add("acme/sub", _sub(hil=True), "1.0.0")

    run_id = _start_run(client)
    _wait_for_status(client, run_id, "paused")

    del reg.store["acme/sub"]["1.0.0"]
    resp = client.post(f"/api/runs/{run_id}/resume", json={"note": "n"})
    assert resp.status_code == 422
    assert "not found" in resp.json()["detail"]


def test_recovery_with_deleted_pin_fails_run_loudly(invoke_client):
    client, reg = invoke_client
    from app import runs as runs_module

    reg.add("acme/sub", _sub(hil=True), "1.0.0")
    run_id = _start_run(client)
    _wait_for_status(client, run_id, "paused")
    runs_module.flush_store()

    del reg.store["acme/sub"]["1.0.0"]  # pinned version gone before restart
    runs_module.RUNS.clear()
    with TestClient(app) as restarted:
        body = restarted.get(f"/api/runs/{run_id}").json()
        assert body["status"] == "failed"
        assert "not found" in (body["error"] or "")

        # Pre-pause history preserved; terminal event is a fatal node_error.
        events = body["events"]
        assert any(e["type"] == "node_start" and e["node_id"] == "inv__cf" for e in events)
        assert events[-1]["type"] == "node_error"
        assert events[-1]["data"]["fatal"] is True

        # No longer listed as paused.
        assert [r["id"] for r in restarted.get("/api/runs/paused").json()] == []


# ─── item 4: HIL timeout inside a region ─────────────────────────────────────

def test_hil_timeout_inside_region_fails_run(invoke_client):
    client, reg = invoke_client
    reg.add("acme/sub", _sub(hil=True, hil_timeout=1), "1.0.0")

    run_id = _start_run(client)
    body = _wait_for_status(client, run_id, "paused")
    assert body["interrupt_value"]["timeout_seconds"] == 1

    body = _wait_for_status(client, run_id, "failed", timeout=5)
    assert "timed out" in (body["error"] or "")
    events = body["events"]
    assert events[-1]["type"] == "human_timeout"
    assert events[-1]["node_id"] == "inv__hil"


# ─── item 5: token accounting across the frame boundary ──────────────────────

def test_token_usage_across_region_boundary(invoke_client, monkeypatch):
    from app.engine import builder as builder_module

    client, reg = invoke_client
    fake = _FakeProvider(LLMResult(content="hi", tokens_input=10, tokens_output=5))
    monkeypatch.setattr(builder_module, "create_provider", lambda cfg: fake)
    reg.add("acme/sub", _sub(cf_code=None, agent=True), "1.0.0")

    run_id = _start_run(client, wf_id="inv-tok-wf", payload={})
    body = _wait_for_status(client, run_id, "completed")

    # Tokens consumed inside the region are aggregated into the run totals —
    # usage is tracked on the builder, so the frame swap cannot lose it.
    assert body["total_tokens_input"] == 10
    assert body["total_tokens_output"] == 5
    llm = [e for e in body["events"] if e["type"] == "llm_call"]
    assert len(llm) == 1
    assert llm[0]["node_id"] == "inv__agent"


# ─── item 6: capability_pins on finished-run recovery ────────────────────────────

def test_finished_invoke_run_recovers_pins_after_restart(invoke_client):
    """A completed run's stored pins are rebuilt from the store after a restart,
    and GET /runs/{id} exposes them."""
    client, reg = invoke_client
    from app import runs as runs_module

    reg.add("acme/sub", _sub(), "1.0.0")
    run_id = _start_run(client)
    body = _wait_for_status(client, run_id, "completed")
    assert body["capability_pins"] == {"acme/sub": "1.0.0"}

    runs_module.flush_store()
    runs_module.RUNS.clear()  # "restart"
    with TestClient(app) as restarted:
        body = restarted.get(f"/api/runs/{run_id}").json()
        assert body["status"] == "completed"
        assert body["capability_pins"] == {"acme/sub": "1.0.0"}


def test_finished_run_without_pins_recovers(invoke_client):
    """A finished run whose store row has NULL capability_pins recovers without
    crashing and reports pins as null."""
    client, _reg = invoke_client
    from app import runs as runs_module

    plain = {
        "id": "plain-wf", "name": "Plain WF",
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
    }
    (wf_module.settings.workflows_dir / "plain-wf.json").write_text(json.dumps(plain))

    run_id = _start_run(client, wf_id="plain-wf", payload={})
    body = _wait_for_status(client, run_id, "completed")
    assert body["capability_pins"] in (None, {})  # no invoke nodes → nothing stored

    runs_module.flush_store()
    runs_module.RUNS.clear()  # "restart"
    with TestClient(app) as restarted:
        body = restarted.get(f"/api/runs/{run_id}").json()
        assert body["status"] == "completed"
        assert body["capability_pins"] is None
