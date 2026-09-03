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

Cancelling: POST /runs/{id}/cancel ends a running or paused run with status
"cancelled" and a terminal `run_cancelled` event. A paused run is cancelled
immediately and its checkpoint thread is deleted, so startup recovery never
resurrects it. A running run stops after the current super-step (the engine
checks the record's cancel_event between steps). Cancelled runs are excluded
from capability metrics aggregation.
"""
import asyncio
import time
import uuid
from typing import Any

from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect

from app.api.workflows import _load_workflow
from app.capability_client import CapabilityFetchError
from app.config import get_settings
from app.engine.expand import ExpansionError, prepare_workflow_for_run
from app.runs.executor import _drive
from app.runs.record import RUNS, RunRecord
from app.runs.store import _delete_checkpoint_thread, _is_terminal, _save_run_summary
from app.runs.timeouts import _cancel_human_timeout

router = APIRouter()


async def _prepare_run(workflow, pins: dict[str, str] | None = None):
    """Expand invoke nodes (fresh resolve, or pinned re-expansion) off the event
    loop. Unresolvable references fail the request before any run starts."""
    try:
        return await asyncio.to_thread(prepare_workflow_for_run, workflow, pins=pins)
    except (CapabilityFetchError, ExpansionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _capability_usage(workflow, pins: dict[str, str] | None) -> dict[str, str | None] | None:
    """Snapshot of which capability versions this run uses (name → version).

    Union of model/tool provenance and the invoke pins resolved at run start;
    on a name clash the pin wins (it is what the run actually resolved to).
    Entries with provenance but no version are kept as name-only attribution.
    Returns None for workflows without any attribution, so the store keeps NULL.
    """
    usage: dict[str, str | None] = {}
    for model in workflow.models:
        if model.source_capability:
            usage[model.source_capability] = model.source_version
    for tool in workflow.tools:
        if tool.source_capability:
            usage[tool.source_capability] = tool.source_version
    for name, version in (pins or {}).items():
        usage[name] = version
    return usage or None


@router.post("/workflows/{workflow_id}/run", status_code=202)
async def run_workflow(workflow_id: str, input_data: dict[str, Any] = {}):
    """Kick off a run in the background and return its id for streaming."""
    workflow = _load_workflow(workflow_id)  # raises 404 if missing
    expanded, invocations, pins, notices = await _prepare_run(workflow)
    record = RunRecord(
        run_id=uuid.uuid4().hex, workflow_id=workflow_id, input_data=input_data,
        capability_pins=pins,
        capability_usage=_capability_usage(workflow, pins),
    )
    RUNS[record.run_id] = record
    _save_run_summary(record)
    for notice in notices:
        record.emit({
            "type": "capability_notice",
            "node_id": None,
            "timestamp": time.time(),
            "data": {"message": notice},
        })
    asyncio.create_task(
        _drive(record, expanded, input_data=input_data, invocations=invocations)
    )
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
    # Re-expand with the pins stored at run start so the graph structure and
    # tracked artifact content are identical to what produced the checkpoint.
    expanded, invocations, _, _ = await _prepare_run(workflow, pins=record.capability_pins)
    if record.status != "paused":
        # Cancelled (or otherwise ended) while we were preparing above.
        raise HTTPException(
            status_code=409,
            detail=f"Run {run_id} is no longer paused (status={record.status})",
        )
    # Close the window between this check and the task actually starting: a
    # cancel arriving before _drive flips the status would otherwise race.
    record.status = "running"
    asyncio.create_task(
        _drive(record, expanded, human_input=human_input, invocations=invocations)
    )
    return {"run_id": run_id, "status": "resuming"}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    """Cancel a running or paused run.

    A paused run is cancelled immediately (terminal `run_cancelled` event) and
    its checkpoint thread is deleted so it cannot be resurrected on restart.
    A running run stops after the current super-step; the terminal event then
    arrives over the event stream."""
    record = RUNS.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if record.status == "paused":
        _cancel_human_timeout(record)
        record.status = "cancelled"
        record.error = "Cancelled by user"
        record.completed_at = time.time()
        record.emit({
            "type": "run_cancelled",
            "node_id": None,
            "timestamp": time.time(),
            "data": {"reason": "cancelled by user"},
        })
        _save_run_summary(record)
        await asyncio.to_thread(
            _delete_checkpoint_thread, str(get_settings().checkpoint_db), record.run_id
        )
        return {"run_id": run_id, "status": "cancelled"}
    if record.status == "running":
        # The engine checks this between super-steps and emits the terminal.
        record.cancel_event.set()
        return {"run_id": run_id, "status": "cancelling"}
    raise HTTPException(
        status_code=409,
        detail=f"Run {run_id} is not cancellable (status={record.status})",
    )


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
        "capability_pins": record.capability_pins,
        "capability_usage": record.capability_usage,
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
