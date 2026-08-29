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
