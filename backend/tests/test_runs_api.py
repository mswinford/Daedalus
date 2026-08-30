"""Tests for the async run API: POST 202, GET polling, and WebSocket streaming."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import workflows as wf_module
from app.api import runs as runs_module


@pytest.fixture(autouse=True)
def _clear_runs():
    runs_module.RUNS.clear()
    yield
    runs_module.RUNS.clear()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(wf_module.settings, "workflows_dir", tmp_path)

    wf = {
        "id": "test-wf",
        "name": "Test WF",
        "description": None,
        "schema_version": 1,
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "config": {}},
            {"id": "cf", "type": "custom_function", "position": {"x": 200, "y": 0},
             "config": {"code": 'result["grade"] = "A"', "output_fields": ["grade"]}},
            {"id": "end", "type": "end", "position": {"x": 400, "y": 0}, "config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "cf"},
            {"id": "e2", "source_node_id": "cf", "source_handle": "default", "target_node_id": "end"},
        ],
        "tools": [],
        "models": [],
    }
    (tmp_path / "test-wf.json").write_text(json.dumps(wf))

    with TestClient(app) as c:
        yield c


def _wait_for_run(client, run_id, timeout=5.0):
    """Poll GET until the run is no longer running."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} still running after {timeout}s")


def test_run_returns_202_with_run_id(client):
    resp = client.post("/api/workflows/test-wf/run", json={})
    assert resp.status_code == 202
    body = resp.json()
    assert "run_id" in body
    assert len(body["run_id"]) > 0


def test_run_unknown_workflow_404(client):
    resp = client.post("/api/workflows/nope/run", json={})
    assert resp.status_code == 404


def test_get_run_completes_with_events(client):
    run_id = client.post("/api/workflows/test-wf/run", json={}).json()["run_id"]
    body = _wait_for_run(client, run_id)

    assert body["id"] == run_id
    assert body["workflow_id"] == "test-wf"
    assert body["status"] == "completed"
    assert body["error"] is None
    assert len(body["events"]) > 0
    assert body["output_data"]["node_outputs"]["cf"]["grade"] == "A"

    types = [e["type"] for e in body["events"]]
    assert "node_start" in types
    assert "node_end" in types
    assert "run_end" in types


def test_get_run_unknown_404(client):
    resp = client.get("/api/runs/nonexistent")
    assert resp.status_code == 404


def test_ws_streams_events_to_terminal(client):
    run_id = client.post("/api/workflows/test-wf/run", json={}).json()["run_id"]

    with client.websocket_connect(f"/api/runs/{run_id}/events") as ws:
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] == "run_end" or (ev["type"] == "node_error" and ev.get("data", {}).get("fatal")):
                break

    types = [e["type"] for e in events]
    assert "node_start" in types
    assert "node_end" in types
    assert "run_end" in types
    assert events[-1]["type"] == "run_end"

    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_ws_unknown_run_sends_fatal_error(client):
    with client.websocket_connect("/api/runs/nonexistent/events") as ws:
        ev = ws.receive_json()
        assert ev["type"] == "node_error"
        assert ev["data"]["fatal"] is True


# ─── Human-in-loop tests ─────────────────────────────────────────────────────


@pytest.fixture()
def hil_client(tmp_path, monkeypatch):
    """Client with a workflow containing a human_in_loop node."""
    monkeypatch.setattr(wf_module.settings, "workflows_dir", tmp_path)

    wf = {
        "id": "hil-wf",
        "name": "HIL WF",
        "description": None,
        "schema_version": 1,
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "config": {}},
            {"id": "human", "type": "human_in_loop", "position": {"x": 200, "y": 0},
             "config": {
                 "input_fields": [
                     {"name": "answer", "label": "Your answer", "type": "text", "required": True}
                 ],
                 "approval_required": False,
                 "output_fields": ["human_answer"],
             }},
            {"id": "cf", "type": "custom_function", "position": {"x": 400, "y": 0},
             "config": {"code": 'result["doubled"] = state["data"].get("human_answer", "") + "!"',
                        "output_fields": ["doubled"]}},
            {"id": "end", "type": "end", "position": {"x": 600, "y": 0}, "config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "human"},
            {"id": "e2", "source_node_id": "human", "source_handle": "default", "target_node_id": "cf"},
            {"id": "e3", "source_node_id": "cf", "source_handle": "default", "target_node_id": "end"},
        ],
        "tools": [],
        "models": [],
    }
    (tmp_path / "hil-wf.json").write_text(json.dumps(wf))

    with TestClient(app) as c:
        yield c


def test_hil_run_pauses(hil_client):
    """A run hitting a human_in_loop node pauses with status='paused'."""
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    body = _wait_for_run(hil_client, run_id)

    assert body["status"] == "paused"
    assert body["interrupt_value"] is not None
    payload = body["interrupt_value"]
    assert payload["node_id"] == "human"
    assert len(payload["fields"]) == 1
    assert payload["fields"][0]["name"] == "answer"

    types = [e["type"] for e in body["events"]]
    assert "human_request" in types


def test_hil_resume_completes(hil_client):
    """Resuming a paused run with input completes the workflow."""
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    _wait_for_run(hil_client, run_id)

    resp = hil_client.post(f"/api/runs/{run_id}/resume", json={"answer": "hello"})
    assert resp.status_code == 202

    body = _wait_for_run(hil_client, run_id)
    assert body["status"] == "completed"
    assert body["output_data"]["node_outputs"]["cf"]["doubled"] == "hello!"


def test_hil_resume_wrong_status_409(hil_client):
    """Resuming a non-paused run returns 409."""
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    _wait_for_run(hil_client, run_id)

    # Resume once to complete it
    hil_client.post(f"/api/runs/{run_id}/resume", json={"answer": "x"})
    _wait_for_run(hil_client, run_id)

    resp = hil_client.post(f"/api/runs/{run_id}/resume", json={"answer": "y"})
    assert resp.status_code == 409


def test_hil_resume_unknown_404(hil_client):
    resp = hil_client.post("/api/runs/nonexistent/resume", json={"a": 1})
    assert resp.status_code == 404


def test_hil_resume_empty_body_does_not_repause(hil_client):
    """Resuming with no body ({}) must resume, not silently re-fire the interrupt.

    LangGraph misreads a bare `Command(resume={})` as an empty interrupt-id map;
    the runner now routes single-interrupt resumes through the explicit map form.
    The downstream 'cf' node fails on the missing value — that's expected here;
    the regression is that the run reaches a terminal state instead of pausing.
    """
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    _wait_for_run(hil_client, run_id)

    resp = hil_client.post(f"/api/runs/{run_id}/resume")  # no body -> {}
    assert resp.status_code == 202

    body = _wait_for_run(hil_client, run_id)
    assert body["status"] != "paused"
    assert body["status"] in ("completed", "failed")


def test_resume_workflow_none_input():
    """resume_workflow must accept None without hitting langgraph's UnboundLocalError."""
    import uuid

    from app.engine.runner import resume_workflow, run_workflow_sync
    from schema.models import Workflow

    wf = Workflow.model_validate({
        "id": "none-wf",
        "name": "None WF",
        "description": None,
        "schema_version": 1,
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "config": {}},
            {"id": "human", "type": "human_in_loop", "position": {"x": 200, "y": 0},
             "config": {"input_fields": [], "approval_required": False,
                        "output_fields": ["note"]}},
            {"id": "end", "type": "end", "position": {"x": 400, "y": 0}, "config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "human"},
            {"id": "e2", "source_node_id": "human", "source_handle": "default", "target_node_id": "end"},
        ],
        "tools": [],
        "models": [],
    })

    thread_id = f"none-repro-{uuid.uuid4().hex[:8]}"
    result = run_workflow_sync(wf, {}, thread_id=thread_id)
    assert result["paused"] is True

    out = resume_workflow(wf, thread_id, None)
    assert not out.get("paused")


def test_hil_ws_streams_to_human_request(hil_client):
    """WebSocket streams events until human_request, then closes."""
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]

    with hil_client.websocket_connect(f"/api/runs/{run_id}/events") as ws:
        events = []
        while True:
            ev = ws.receive_json()
            events.append(ev)
            if ev["type"] == "human_request":
                break

    types = [e["type"] for e in events]
    assert "node_start" in types
    assert "human_request" in types
    assert "run_end" not in types


def test_list_paused_runs(hil_client):
    """GET /runs/paused lists paused runs and drops them once resumed."""
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    _wait_for_run(hil_client, run_id)

    body = hil_client.get("/api/runs/paused").json()
    assert len(body) == 1
    entry = body[0]
    assert entry["id"] == run_id
    assert entry["workflow_id"] == "hil-wf"
    assert entry["node_id"] == "human"
    assert entry["requested_at"] is not None

    hil_client.post(f"/api/runs/{run_id}/resume", json={"answer": "x"})
    _wait_for_run(hil_client, run_id)

    assert hil_client.get("/api/runs/paused").json() == []


# ─── Human-in-loop timeout tests ─────────────────────────────────────────────


@pytest.fixture()
def hil_timeout_client(tmp_path, monkeypatch):
    """Like hil_client, but the human node has timeout_seconds=1."""
    monkeypatch.setattr(wf_module.settings, "workflows_dir", tmp_path)

    wf = {
        "id": "hil-to-wf",
        "name": "HIL Timeout WF",
        "description": None,
        "schema_version": 1,
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "config": {}},
            {"id": "human", "type": "human_in_loop", "position": {"x": 200, "y": 0},
             "config": {
                 "input_fields": [
                     {"name": "answer", "label": "Your answer", "type": "text", "required": True}
                 ],
                 "approval_required": False,
                 "timeout_seconds": 1,
                 "output_fields": ["human_answer"],
             }},
            {"id": "cf", "type": "custom_function", "position": {"x": 400, "y": 0},
             "config": {"code": 'result["doubled"] = state["data"].get("human_answer", "") + "!"',
                        "output_fields": ["doubled"]}},
            {"id": "end", "type": "end", "position": {"x": 600, "y": 0}, "config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "human"},
            {"id": "e2", "source_node_id": "human", "source_handle": "default", "target_node_id": "cf"},
            {"id": "e3", "source_node_id": "cf", "source_handle": "default", "target_node_id": "end"},
        ],
        "tools": [],
        "models": [],
    }
    (tmp_path / "hil-to-wf.json").write_text(json.dumps(wf))

    with TestClient(app) as c:
        yield c


def _wait_for_status(client, run_id, status, timeout=10.0):
    """Poll GET until the run reaches a specific status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] == status:
            return body
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} never reached status '{status}'")


def test_hil_timeout_auto_fails(hil_timeout_client):
    """A paused run with a timeout fails automatically when no input arrives."""
    run_id = hil_timeout_client.post("/api/workflows/hil-to-wf/run", json={}).json()["run_id"]

    body = _wait_for_run(hil_timeout_client, run_id)
    assert body["status"] == "paused"
    # The interrupt payload carries the timeout so clients can show a countdown.
    assert body["interrupt_value"]["timeout_seconds"] == 1
    assert body["interrupt_value"]["requested_at"] is not None

    body = _wait_for_status(hil_timeout_client, run_id, "failed")
    assert "timed out" in (body["error"] or "")
    assert body["completed_at"] is not None

    types = [e["type"] for e in body["events"]]
    assert "human_request" in types
    assert types[-1] == "human_timeout"
    timeout_event = body["events"][-1]
    assert timeout_event["node_id"] == "human"
    assert timeout_event["data"]["fatal"] is True

    # Late input after the timeout is rejected.
    resp = hil_timeout_client.post(f"/api/runs/{run_id}/resume", json={"answer": "late"})
    assert resp.status_code == 409


def test_hil_resume_before_timeout_cancels_timer(hil_timeout_client):
    """Resuming before the deadline completes the run; the timer no longer fires."""
    run_id = hil_timeout_client.post("/api/workflows/hil-to-wf/run", json={}).json()["run_id"]
    _wait_for_run(hil_timeout_client, run_id)

    resp = hil_timeout_client.post(f"/api/runs/{run_id}/resume", json={"answer": "hello"})
    assert resp.status_code == 202

    body = _wait_for_run(hil_timeout_client, run_id)
    assert body["status"] == "completed"
    assert body["output_data"]["node_outputs"]["cf"]["doubled"] == "hello!"

    # Wait past the original 1s deadline: the cancelled timer must not fail the run.
    time.sleep(1.5)
    body = hil_timeout_client.get(f"/api/runs/{run_id}").json()
    assert body["status"] == "completed"
    assert "human_timeout" not in [e["type"] for e in body["events"]]


# ─── Restart / checkpoint recovery tests ─────────────────────────────────────


def test_paused_run_survives_restart(hil_client):
    """A paused run's checkpoint survives a restart; the record is rebuilt and resumable."""
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    _wait_for_run(hil_client, run_id)

    # Simulate a process restart: in-memory records are lost, and a fresh app
    # instance (new lifespan) must rebuild the record from the SQLite checkpoint.
    runs_module.RUNS.clear()
    with TestClient(app) as restarted:
        paused = restarted.get("/api/runs/paused").json()
        assert [r["id"] for r in paused] == [run_id]

        resp = restarted.post(f"/api/runs/{run_id}/resume", json={"answer": "back"})
        assert resp.status_code == 202
        body = _wait_for_run(restarted, run_id)
        assert body["status"] == "completed"
        assert body["output_data"]["node_outputs"]["cf"]["doubled"] == "back!"


def test_recovered_run_with_expired_human_timeout_fails(hil_timeout_client):
    """A run whose human-input deadline passed while the process was down is failed,
    not resurrected as paused, when recovery rebuilds its record."""
    run_id = hil_timeout_client.post("/api/workflows/hil-to-wf/run", json={}).json()["run_id"]
    _wait_for_run(hil_timeout_client, run_id)  # paused; deadline is +1s

    runs_module.RUNS.clear()  # "restart" right after the pause
    time.sleep(1.3)           # the deadline passes while "down"

    with TestClient(app) as restarted:
        body = restarted.get(f"/api/runs/{run_id}").json()
        assert body["status"] == "failed"
        assert "timed out" in (body["error"] or "")
        types = [e["type"] for e in body["events"]]
        assert types[-1] == "human_timeout"
