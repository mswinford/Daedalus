"""Tests for run cancellation: POST /runs/{id}/cancel.

Covers paused-run cancel (immediate terminal + checkpoint-thread deletion +
no resurrection on recovery), running-run cancel (step-boundary stop), the
404/409 edges, resume-after-cancel, and the metrics exclusion of cancelled
runs.
"""
import asyncio
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import workflows as wf_module
from app import runs as runs_module
from app.config import get_settings
from app.runs.recovery import recover_paused_runs
from app.runs.store import (
    _load_finished_summaries,
    _load_terminal_runs,
    flush_store,
)


@pytest.fixture(autouse=True)
def _clear_runs():
    runs_module.RUNS.clear()
    yield
    runs_module.RUNS.clear()


@pytest.fixture()
def hil_client(tmp_path, monkeypatch):
    """Client with a workflow that pauses at a human_in_loop node."""
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
             "config": {"code": 'result["done"] = True', "output_fields": ["done"]}},
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


@pytest.fixture()
def slow_client(tmp_path, monkeypatch):
    """Client with a workflow of two slow nodes (cancel window mid-run).

    The sandbox forbids imports, so slowness comes from stubbing
    run_sandboxed itself — each node's execution takes ~0.8s."""
    import app.engine.nodes.custom_function as cf_module

    def fake_run_sandboxed(code, state, timeout):
        time.sleep(0.8)
        return {"one": 1, "two": 2}

    monkeypatch.setattr(cf_module, "run_sandboxed", fake_run_sandboxed)
    monkeypatch.setattr(wf_module.settings, "workflows_dir", tmp_path)

    wf = {
        "id": "slow-wf",
        "name": "Slow WF",
        "description": None,
        "schema_version": 1,
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "config": {}},
            {"id": "slow1", "type": "custom_function", "position": {"x": 200, "y": 0},
             "config": {"code": "result['one'] = 1", "output_fields": ["one"]}},
            {"id": "slow2", "type": "custom_function", "position": {"x": 400, "y": 0},
             "config": {"code": "result['two'] = 2", "output_fields": ["two"]}},
            {"id": "end", "type": "end", "position": {"x": 600, "y": 0}, "config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "slow1"},
            {"id": "e2", "source_node_id": "slow1", "source_handle": "default", "target_node_id": "slow2"},
            {"id": "e3", "source_node_id": "slow2", "source_handle": "default", "target_node_id": "end"},
        ],
        "tools": [],
        "models": [],
    }
    (tmp_path / "slow-wf.json").write_text(json.dumps(wf))

    with TestClient(app) as c:
        yield c


def _wait_for_status(client, run_id, statuses, timeout=5.0):
    """Poll GET until the run reaches one of `statuses`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} never reached {statuses}")


def _thread_row_counts(run_id):
    conn = sqlite3.connect(str(get_settings().checkpoint_db), timeout=5.0)
    try:
        cp = conn.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (run_id,)
        ).fetchone()[0]
        w = conn.execute(
            "SELECT COUNT(*) FROM writes WHERE thread_id = ?", (run_id,)
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return 0, 0  # no runs ever started in this DB
    finally:
        conn.close()
    return cp, w


def _wait_thread_gone(run_id, timeout=3.0):
    """Wait until the run's checkpoint thread is fully deleted (async path)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _thread_row_counts(run_id) == (0, 0):
            return
        time.sleep(0.05)
    raise AssertionError(f"Checkpoint thread for {run_id} not deleted")


def test_cancel_paused_run(hil_client):
    """Cancelling a paused run terminates it and deletes its checkpoint thread."""
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    _wait_for_status(hil_client, run_id, {"paused"})

    resp = hil_client.post(f"/api/runs/{run_id}/cancel")
    assert resp.status_code == 200
    assert resp.json() == {"run_id": run_id, "status": "cancelled"}

    body = hil_client.get(f"/api/runs/{run_id}").json()
    assert body["status"] == "cancelled"
    assert body["error"] == "Cancelled by user"
    assert body["completed_at"] is not None
    types = [e["type"] for e in body["events"]]
    assert "run_cancelled" in types
    assert types[-1] == "run_cancelled"

    # Checkpoint thread is gone, so recovery can never resurrect this run.
    _wait_thread_gone(run_id)


def test_cancel_paused_run_not_resurrected(hil_client):
    """A cancelled paused run stays dead across a simulated restart."""
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    _wait_for_status(hil_client, run_id, {"paused"})
    hil_client.post(f"/api/runs/{run_id}/cancel")

    runs_module.RUNS.clear()  # simulate a process restart
    recovered = asyncio.run(recover_paused_runs())
    assert recovered == 0
    assert run_id not in runs_module.RUNS


def test_cancel_paused_run_drops_from_paused_list(hil_client):
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    _wait_for_status(hil_client, run_id, {"paused"})
    assert any(r["id"] == run_id for r in hil_client.get("/api/runs/paused").json())

    hil_client.post(f"/api/runs/{run_id}/cancel")
    assert all(r["id"] != run_id for r in hil_client.get("/api/runs/paused").json())


def test_resume_after_cancel_409(hil_client):
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    _wait_for_status(hil_client, run_id, {"paused"})
    hil_client.post(f"/api/runs/{run_id}/cancel")

    resp = hil_client.post(f"/api/runs/{run_id}/resume", json={"answer": "x"})
    assert resp.status_code == 409


def test_cancel_unknown_run_404(hil_client):
    assert hil_client.post("/api/runs/nope/cancel").status_code == 404


def test_cancel_completed_run_409(tmp_path, monkeypatch):
    monkeypatch.setattr(wf_module.settings, "workflows_dir", tmp_path)
    wf = {
        "id": "quick-wf",
        "name": "Quick WF",
        "description": None,
        "schema_version": 1,
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "config": {}},
            {"id": "end", "type": "end", "position": {"x": 200, "y": 0}, "config": {}},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "source_handle": "default", "target_node_id": "end"},
        ],
        "tools": [],
        "models": [],
    }
    (tmp_path / "quick-wf.json").write_text(json.dumps(wf))

    with TestClient(app) as c:
        run_id = c.post("/api/workflows/quick-wf/run", json={}).json()["run_id"]
        _wait_for_status(c, run_id, {"completed"})
        assert c.post(f"/api/runs/{run_id}/cancel").status_code == 409


def test_cancel_running_run_stops_at_step_boundary(slow_client):
    """A running run stops after the current super-step; later nodes never run."""
    run_id = slow_client.post("/api/workflows/slow-wf/run", json={}).json()["run_id"]
    _wait_for_status(slow_client, run_id, {"running"})
    time.sleep(0.15)  # let the first node start executing

    resp = slow_client.post(f"/api/runs/{run_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelling"

    body = _wait_for_status(slow_client, run_id, {"cancelled"}, timeout=5.0)
    assert body["error"] == "Cancelled by user"
    types = [e["type"] for e in body["events"]]
    assert types[-1] == "run_cancelled"

    started = {
        e.get("node_id")
        for e in body["events"]
        if e["type"] == "node_start"
    }
    assert "slow1" in started
    assert "slow2" not in started  # stopped at the step boundary


def test_cancel_running_run_deletes_thread(slow_client):
    run_id = slow_client.post("/api/workflows/slow-wf/run", json={}).json()["run_id"]
    _wait_for_status(slow_client, run_id, {"running"})
    time.sleep(0.15)
    slow_client.post(f"/api/runs/{run_id}/cancel")
    _wait_for_status(slow_client, run_id, {"cancelled"}, timeout=5.0)
    _wait_thread_gone(run_id)


def test_recovery_skips_thread_of_cancelled_run(hil_client):
    """Crash window: summary says cancelled but the thread survived — recovery skips it."""
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    _wait_for_status(hil_client, run_id, {"paused"})

    # Simulate a crash between the summary write and the thread deletion.
    conn = sqlite3.connect(str(get_settings().checkpoint_db), timeout=5.0)
    conn.execute(
        "UPDATE runs SET status = 'cancelled', completed_at = ? WHERE run_id = ?",
        (time.time(), run_id),
    )
    conn.commit()
    conn.close()
    assert _thread_row_counts(run_id)[0] > 0  # thread still present

    runs_module.RUNS.clear()
    recovered = asyncio.run(recover_paused_runs())
    assert recovered == 0
    assert run_id not in runs_module.RUNS


def test_cancelled_run_excluded_from_metrics_rows(hil_client):
    """Cancelled runs stay inspectable but never feed capability aggregation."""
    run_id = hil_client.post("/api/workflows/hil-wf/run", json={}).json()["run_id"]
    _wait_for_status(hil_client, run_id, {"paused"})
    hil_client.post(f"/api/runs/{run_id}/cancel")
    flush_store()

    finished = {row["run_id"] for row in _load_finished_summaries()}
    assert run_id in finished  # still rebuilt after a restart (inspectable)

    terminal = {row["run_id"] for row in _load_terminal_runs()}
    assert run_id not in terminal  # excluded from the metrics pass
