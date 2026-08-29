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
