"""Run execution API: async kickoff, WebSocket event streaming, and retrieval.

A POST validates the workflow, starts the graph in a background task, and
returns a run id immediately (HTTP 202). Clients subscribe over a WebSocket to
receive node/llm events live; anything already emitted is replayed on connect
so a late subscriber still sees the full trace.

Human-in-loop: when a run hits a human_in_loop node, it pauses (status="paused")
and emits a `human_request` event. The client calls POST /runs/{id}/resume with
the human's input to continue execution. If the node has a timeout_seconds set,
a background timer auto-fails the run (status="failed", terminal `human_timeout`
event) when no input arrives in time; resuming before the deadline cancels it.
"""
import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect

from app.api.workflows import _load_workflow
from app.runs.executor import _drive
from app.runs.record import RUNS, RunRecord
from app.runs.store import _is_terminal, _save_run_summary
from app.runs.timeouts import _cancel_human_timeout

router = APIRouter()


@router.post("/workflows/{workflow_id}/run", status_code=202)
async def run_workflow(workflow_id: str, input_data: dict[str, Any] = {}):
    """Kick off a run in the background and return its id for streaming."""
    workflow = _load_workflow(workflow_id)  # raises 404 if missing
    record = RunRecord(
        run_id=uuid.uuid4().hex, workflow_id=workflow_id, input_data=input_data
    )
    RUNS[record.run_id] = record
    _save_run_summary(record)
    asyncio.create_task(_drive(record, workflow, input_data=input_data))
    return {"run_id": record.run_id}


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: str, human_input: dict[str, Any] = Body(default={})):
    """Resume a paused run with human-provided input."""
    record = RUNS.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if record.status != "paused":
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is not paused (status={record.status})",
        )
    _cancel_human_timeout(record)
    workflow = _load_workflow(record.workflow_id)
    asyncio.create_task(_drive(record, workflow, human_input=human_input))
    return {"run_id": run_id, "status": "resuming"}


@router.get("/runs/paused")
def list_paused_runs():
    """List runs currently waiting for human input, oldest first."""
    out = []
    for record in RUNS.values():
        if record.status != "paused":
            continue
        iv = record.interrupt_value if isinstance(record.interrupt_value, dict) else {}
        out.append({
            "id": record.run_id,
            "workflow_id": record.workflow_id,
            "node_id": iv.get("node_id"),
            "message": iv.get("message"),
            "requested_at": iv.get("requested_at"),
            "timeout_seconds": iv.get("timeout_seconds"),
            "started_at": record.started_at,
        })
    out.sort(key=lambda r: r["requested_at"] or 0)
    return out


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    """Fetch a run's current state.

    Run metadata and events persist in SQLite; records for paused and finished
    runs are rebuilt from the store on startup.
    """
    record = RUNS.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return {
        "id": record.run_id,
        "workflow_id": record.workflow_id,
        "status": record.status,
        "input_data": record.input_data,
        "output_data": record.output_data,
        "error": record.error,
        "interrupt_value": record.interrupt_value,
        "events": record.events,
        "total_tokens_input": record.total_tokens_input,
        "total_tokens_output": record.total_tokens_output,
        "estimated_cost_usd": record.estimated_cost_usd,
        "started_at": record.started_at,
        "completed_at": record.completed_at,
    }


@router.websocket("/runs/{run_id}/events")
async def run_events(websocket: WebSocket, run_id: str):
    """Stream a run's events: replay anything already emitted, then live updates."""
    await websocket.accept()
    record = RUNS.get(run_id)
    if record is None:
        await websocket.send_json(
            {"type": "node_error", "data": {"error": "Run not found", "fatal": True}}
        )
        await websocket.close()
        return

    queue: asyncio.Queue = asyncio.Queue()
    record.subscribers.add(queue)
    last_seq = 0
    try:
        # Replay events emitted before we subscribed (the run may already be done).
        terminal_seen = False
        for event in list(record.events):
            last_seq = max(last_seq, int(event.get("seq", 0)))
            await websocket.send_json(event)
            if _is_terminal(event):
                terminal_seen = True
        if record.status != "running" or terminal_seen:
            return  # terminal already replayed above; nothing left to stream
        while True:
            event = await queue.get()
            seq = int(event.get("seq", 0))
            if seq <= last_seq:
                continue  # already delivered via replay
            last_seq = seq
            await websocket.send_json(event)
            if _is_terminal(event):
                break
            if event.get("type") == "human_request":
                break  # Run paused; client reconnects after resume.
    except WebSocketDisconnect:
        pass
    finally:
        record.subscribers.discard(queue)
