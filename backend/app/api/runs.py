"""Run execution API: async kickoff, WebSocket event streaming, and retrieval.

Phase 2 uses an in-memory run store. A POST validates the workflow, starts the
graph in a background task, and returns a run id immediately (HTTP 202). Clients
subscribe over a WebSocket to receive node/llm events live; anything already
emitted is replayed on connect so a late subscriber still sees the full trace.
Runs are lost on process restart — persistence is deferred to Phase 3.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from schema.models import RunEvent
from app.engine.runner import run_workflow_sync
from app.api.workflows import _load_workflow

router = APIRouter()

# Bound the in-memory store so a long-lived process doesn't grow unbounded.
MAX_RUNS = 200


def _is_terminal(event: dict[str, Any]) -> bool:
    """True for the event that marks a run as finished (success or fatal error)."""
    if event.get("type") == "run_end":
        return True
    if event.get("type") == "node_error" and event.get("data", {}).get("fatal"):
        return True
    return False


@dataclass
class RunRecord:
    """One run's state: status, event log, result, and live WebSocket subscribers."""

    run_id: str
    workflow_id: str
    input_data: dict[str, Any]
    status: str = "running"  # running | completed | failed
    events: list[dict[str, Any]] = field(default_factory=list)
    output_data: dict[str, Any] | None = None
    error: str | None = None
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    estimated_cost_usd: float = 0.0
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    _seq: int = 0
    _loop: asyncio.AbstractEventLoop | None = None

    def emit(self, event: RunEvent | dict[str, Any]) -> None:
        """Normalize an event, stamp it with a sequence number, store it, and fan
        it out to live subscribers. Safe to call from the worker thread that runs
        the graph (queues are poked via call_soon_threadsafe)."""
        payload = event.model_dump() if isinstance(event, RunEvent) else dict(event)
        payload = jsonable_encoder(payload)
        self._seq += 1
        payload["seq"] = self._seq
        self.events.append(payload)
        for queue in list(self.subscribers):
            if self._loop is not None:
                self._loop.call_soon_threadsafe(queue.put_nowait, payload)
            else:
                queue.put_nowait(payload)


RUNS: dict[str, RunRecord] = {}


def _prune_runs() -> None:
    """Evict the oldest finished runs once the store exceeds MAX_RUNS."""
    if len(RUNS) <= MAX_RUNS:
        return
    finished = sorted(
        (r for r in RUNS.values() if r.status != "running"),
        key=lambda r: r.started_at,
    )
    for record in finished[: len(RUNS) - MAX_RUNS]:
        RUNS.pop(record.run_id, None)


async def _execute(record: RunRecord, workflow: Any, input_data: dict[str, Any]) -> None:
    """Run the graph in a worker thread (streaming events), then emit a terminal."""
    record._loop = asyncio.get_running_loop()
    try:
        result = await asyncio.to_thread(
            run_workflow_sync, workflow, input_data, on_event=record.emit
        )
        record.status = "completed"
        record.output_data = {
            "output": result.get("output", ""),
            "messages": result.get("messages", []),
            "data": result.get("data", {}),
            "node_outputs": result.get("node_outputs", {}),
        }
        record.total_tokens_input = result.get("total_tokens_input", 0)
        record.total_tokens_output = result.get("total_tokens_output", 0)
        record.estimated_cost_usd = result.get("estimated_cost_usd", 0.0)
        terminal: dict[str, Any] = {
            "type": "run_end",
            "node_id": None,
            "timestamp": time.time(),
            "data": {
                "output": result.get("output", ""),
                "total_tokens_input": record.total_tokens_input,
                "total_tokens_output": record.total_tokens_output,
                "estimated_cost_usd": record.estimated_cost_usd,
            },
        }
    except Exception as exc:  # noqa: BLE001 - surface any failure to the client
        record.status = "failed"
        record.error = str(exc)
        terminal = {
            "type": "node_error",
            "node_id": None,
            "timestamp": time.time(),
            "data": {"error": str(exc), "fatal": True},
        }
    record.completed_at = time.time()
    record.emit(terminal)
    _prune_runs()


@router.post("/workflows/{workflow_id}/run", status_code=202)
async def run_workflow(workflow_id: str, input_data: dict[str, Any] = {}):
    """Kick off a run in the background and return its id for streaming."""
    workflow = _load_workflow(workflow_id)  # raises 404 if missing
    record = RunRecord(
        run_id=uuid.uuid4().hex, workflow_id=workflow_id, input_data=input_data
    )
    RUNS[record.run_id] = record
    asyncio.create_task(_execute(record, workflow, input_data))
    return {"run_id": record.run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    """Fetch a run's current state (in-memory; gone after a process restart)."""
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
    except WebSocketDisconnect:
        pass
    finally:
        record.subscribers.discard(queue)
