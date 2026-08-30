"""Run execution API: async kickoff, WebSocket event streaming, and retrieval.

Phase 2 uses an in-memory run store. A POST validates the workflow, starts the
graph in a background task, and returns a run id immediately (HTTP 202). Clients
subscribe over a WebSocket to receive node/llm events live; anything already
emitted is replayed on connect so a late subscriber still sees the full trace.

Run metadata and event logs persist in SQLite (~/.ai-forge/checkpoints.db)
alongside the graph checkpoints, so runs survive process restarts: on startup
`recover_paused_runs` rebuilds paused human-in-loop records (with their full
event history, ready to resume) and `recover_finished_runs` restores terminal
records for inspection.

Human-in-loop: when a run hits a human_in_loop node, it pauses (status="paused")
and emits a `human_request` event. The client calls POST /runs/{id}/resume with
the human's input to continue execution. If the node has a timeout_seconds set,
a background timer auto-fails the run (status="failed", terminal `human_timeout`
event) when no input arrives in time; resuming before the deadline cancels it.
"""
import asyncio
import json
import os
import queue
import sqlite3
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, Body, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from schema.models import RunEvent
from app.config import get_settings
from app.engine.builder import GraphBuilder
from app.engine.runner import run_workflow_sync, resume_workflow
from app.api.workflows import _load_workflow

router = APIRouter()

# Bound the in-memory store so a long-lived process doesn't grow unbounded.
MAX_RUNS = 200

# Run metadata + event log persistence, in the same SQLite file as the
# checkpoints. emit() runs on the event-loop thread while the graph executes,
# so writes must never block it: they are queued and applied by a dedicated
# writer thread using short-lived connections (open, write, commit, close).
_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    status TEXT NOT NULL,
    input_data TEXT NOT NULL DEFAULT '{}',
    output_data TEXT,
    error TEXT,
    total_tokens_input INTEGER NOT NULL DEFAULT 0,
    total_tokens_output INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0.0,
    started_at REAL NOT NULL,
    completed_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
"""

_write_queue: "queue.Queue[tuple]" = queue.Queue()
_writer_thread: threading.Thread | None = None
_writer_lock = threading.Lock()


def _store_connect(path: str) -> sqlite3.Connection:
    """A short-lived connection to the store DB (WAL, owner-only perms)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    for suffix in ("", "-wal", "-shm"):  # keep run data owner-only (like secrets.json)
        if os.path.exists(path + suffix):
            os.chmod(path + suffix, 0o600)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_STORE_SCHEMA)
    return conn


def _write_event(path: str, run_id: str, seq: int, payload: dict[str, Any]) -> None:
    conn = _store_connect(path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO events (run_id, seq, payload) VALUES (?, ?, ?)",
            (run_id, seq, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()


def _write_summary(path: str, fields: tuple) -> None:
    conn = _store_connect(path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs (
                run_id, workflow_id, status, input_data, output_data, error,
                total_tokens_input, total_tokens_output, estimated_cost_usd,
                started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fields,
        )
        conn.commit()
    finally:
        conn.close()


def _prune_store_sync(path: str) -> None:
    """Evict the oldest finished runs once the store exceeds MAX_RUNS."""
    conn = _store_connect(path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        if count <= MAX_RUNS:
            return
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE status != 'running' "
            "ORDER BY started_at LIMIT ?",
            (count - MAX_RUNS,),
        ).fetchall()
        for row in rows:
            conn.execute("DELETE FROM events WHERE run_id = ?", (row["run_id"],))
            conn.execute("DELETE FROM runs WHERE run_id = ?", (row["run_id"],))
        conn.commit()
    finally:
        conn.close()


def _writer_loop() -> None:
    while True:
        item = _write_queue.get()
        try:
            if item is not None:  # None is the shutdown sentinel
                kind, path = item[0], item[1]
                if kind == "event":
                    _write_event(path, *item[2:])
                elif kind == "summary":
                    _write_summary(path, item[2])
                else:
                    _prune_store_sync(path)
        except Exception as e:  # best-effort persistence; keep the writer alive
            if item is not None:
                print(f"run store write failed ({item[0]}): {e}", file=sys.stderr)
        finally:
            _write_queue.task_done()
            if item is None:
                break


def _enqueue_write(item: tuple) -> None:
    """Queue a store write. Non-blocking and safe from the event-loop thread."""
    global _writer_thread
    _write_queue.put(item)
    with _writer_lock:
        if _writer_thread is None or not _writer_thread.is_alive():
            _writer_thread = threading.Thread(
                target=_writer_loop, name="run-store-writer", daemon=True
            )
            _writer_thread.start()


def flush_store() -> None:
    """Block until every queued write has been applied (used by tests)."""
    _write_queue.join()


def shutdown_store(timeout: float = 5.0) -> None:
    """Drain pending writes and stop the writer thread (app shutdown)."""
    global _writer_thread
    with _writer_lock:
        if _writer_thread is None or not _writer_thread.is_alive():
            return
        thread = _writer_thread
        _writer_thread = None
    _write_queue.put(None)
    thread.join(timeout)


def _persist_event(run_id: str, seq: int, payload: dict[str, Any]) -> None:
    _enqueue_write(
        ("event", str(get_settings().checkpoint_db), run_id, seq, payload)
    )


def _load_events(run_id: str) -> list[dict[str, Any]]:
    conn = _store_connect(str(get_settings().checkpoint_db))
    try:
        rows = conn.execute(
            "SELECT payload FROM events WHERE run_id = ? ORDER BY seq", (run_id,)
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(row["payload"]) for row in rows]


def _save_run_summary(record: RunRecord) -> None:
    """Queue the record's metadata upsert so it survives a restart."""
    _enqueue_write(("summary", str(get_settings().checkpoint_db), (
        record.run_id, record.workflow_id, record.status,
        json.dumps(record.input_data), json.dumps(record.output_data),
        record.error, record.total_tokens_input,
        record.total_tokens_output, record.estimated_cost_usd,
        record.started_at, record.completed_at,
    )))


def _load_run_summary(run_id: str) -> sqlite3.Row | None:
    conn = _store_connect(str(get_settings().checkpoint_db))
    try:
        return conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()


def _load_finished_summaries() -> list[sqlite3.Row]:
    conn = _store_connect(str(get_settings().checkpoint_db))
    try:
        return conn.execute(
            "SELECT * FROM runs WHERE status IN ('completed', 'failed')"
        ).fetchall()
    finally:
        conn.close()


def _prune_store() -> None:
    """Queue eviction of the oldest finished runs beyond MAX_RUNS."""
    _enqueue_write(("prune", str(get_settings().checkpoint_db)))


def _is_terminal(event: dict[str, Any]) -> bool:
    """True for the event that marks a run as finished (success or fatal error)."""
    if event.get("type") in ("run_end", "human_timeout"):
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
    status: str = "running"  # running | completed | failed | paused
    events: list[dict[str, Any]] = field(default_factory=list)
    output_data: dict[str, Any] | None = None
    error: str | None = None
    interrupt_value: Any = None
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    estimated_cost_usd: float = 0.0
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    timeout_task: asyncio.Task | None = field(default=None, repr=False)
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
        _persist_event(self.run_id, self._seq, payload)
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


def _cancel_human_timeout(record: RunRecord) -> None:
    """Cancel a pending human-input timeout, if any."""
    if record.timeout_task is not None and not record.timeout_task.done():
        record.timeout_task.cancel()
    record.timeout_task = None


def _fail_run_on_human_timeout(record: RunRecord, node_id: str, timeout_seconds: int) -> None:
    """Mark a paused run as failed because its human-input deadline passed."""
    if record.status != "paused":
        return  # Resumed (or otherwise finished) before the deadline.
    error = f"Human-in-loop timed out at node '{node_id}' after {timeout_seconds}s"
    record.status = "failed"
    record.error = error
    record.completed_at = time.time()
    record.emit({
        "type": "human_timeout",
        "node_id": node_id,
        "timestamp": time.time(),
        "data": {"error": error, "fatal": True},
    })
    _save_run_summary(record)
    _prune_runs()
    _prune_store()


async def _auto_fail_on_timeout(
    record: RunRecord, node_id: str, timeout_seconds: int, delay: float
) -> None:
    """Fail a paused run when no human input arrives within the deadline."""
    await asyncio.sleep(delay)
    _fail_run_on_human_timeout(record, node_id, timeout_seconds)


def _human_timeout_remaining(interrupt_value: Any) -> float | None:
    """Seconds left until the human-input deadline (None if none was set).

    Derived from `requested_at` + `timeout_seconds` in the interrupt payload so
    a restarted process can re-arm the timer with only the *remaining* time.
    """
    if not isinstance(interrupt_value, dict):
        return None
    timeout_seconds = interrupt_value.get("timeout_seconds")
    requested_at = interrupt_value.get("requested_at")
    if timeout_seconds is None or requested_at is None:
        return None
    deadline = float(requested_at) + int(timeout_seconds)
    return max(0.0, deadline - time.time())


def _schedule_human_timeout(record: RunRecord, interrupt_value: Any) -> None:
    """If the paused human_in_loop node has a timeout, arm the auto-fail timer.

    Also used by `recover_paused_runs` after a restart: if the deadline already
    passed while the process was down, the run is failed immediately instead of
    arming a zero-length timer.
    """
    if not isinstance(interrupt_value, dict):
        return
    node_id = interrupt_value.get("node_id")
    timeout_seconds = interrupt_value.get("timeout_seconds")
    if not node_id or timeout_seconds is None:
        return
    remaining = _human_timeout_remaining(interrupt_value)
    if remaining is None:
        return
    if remaining <= 0:
        _fail_run_on_human_timeout(record, str(node_id), int(timeout_seconds))
        return
    record.timeout_task = asyncio.create_task(
        _auto_fail_on_timeout(record, str(node_id), int(timeout_seconds), remaining)
    )


# Matches the default serde of AsyncSqliteSaver; used to decode raw
# __interrupt__ writes during recovery.
_RECOVERY_SERDE = JsonPlusSerializer()


def _checkpoint_started_at(ts: Any) -> float:
    """Parse a checkpoint's ISO timestamp into an epoch value."""
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (TypeError, ValueError):
        return time.time()


def _pending_interrupt_threads(db_path: str) -> list[tuple[str, Any, bytes]]:
    """(thread_id, type, value) rows for threads whose LATEST checkpoint has a
    pending __interrupt__ write — i.e. runs paused at a human_in_loop node."""
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        return conn.execute(
            """
            SELECT w.thread_id, w.type, w.value
            FROM writes w
            JOIN (
                SELECT thread_id, MAX(checkpoint_id) AS latest
                FROM checkpoints
                WHERE checkpoint_ns = ''
                GROUP BY thread_id
            ) m ON m.thread_id = w.thread_id AND m.latest = w.checkpoint_id
            WHERE w.channel = '__interrupt__' AND w.checkpoint_ns = ''
            """
        ).fetchall()
    finally:
        conn.close()


async def recover_paused_runs() -> int:
    """Rebuild in-memory records for runs that were paused before a restart.

    Checkpoints survive in SQLite; this re-derives each paused run's interrupt
    payload (and workflow id) from its thread, restores the RunRecord so it can
    be listed and resumed, and re-arms any not-yet-expired timeout — failing
    the run immediately if the deadline passed while the process was down.
    Returns the number of runs recovered. Called from the app lifespan at startup.

    Task reconstruction in LangGraph depends on the graph's own node/edge
    structure, so each thread is read back through a graph built from its real
    workflow (loaded via the workflow_id embedded in the interrupt payload).
    """
    settings = get_settings()
    db_path = str(settings.checkpoint_db)
    try:
        rows = _pending_interrupt_threads(db_path)
    except sqlite3.OperationalError:
        return 0  # No runs have ever been started (tables not created yet).
    if not rows:
        return 0

    recovered = 0
    graphs: dict[str, Any] = {}
    conn = await aiosqlite.connect(db_path, timeout=5.0)
    try:
        saver = AsyncSqliteSaver(conn)
        for thread_id, ctype, value in rows:
            if thread_id in RUNS:
                continue  # Already tracked (no restart happened).
            interrupts = _RECOVERY_SERDE.loads_typed((ctype, value))
            payload = getattr(interrupts[0], "value", None) if interrupts else None
            if not isinstance(payload, dict):
                continue
            workflow_id = str(payload.get("workflow_id") or "")
            graph = graphs.get(workflow_id)
            if graph is None:
                try:
                    workflow = _load_workflow(workflow_id)
                except Exception:
                    continue  # Workflow deleted since the run paused.
                graph = GraphBuilder(workflow).build(checkpointer=saver)
                graphs[workflow_id] = graph
            snapshot = await graph.aget_state(
                {"configurable": {"thread_id": thread_id}}
            )
            interrupts = [
                intr
                for task in snapshot.tasks
                for intr in (task.interrupts or ())
            ]
            if not interrupts:
                continue  # Stale write; latest state is not actually paused.
            payload = interrupts[0].value
            tuple_ = await saver.aget_tuple(
                {"configurable": {"thread_id": thread_id}}
            )
            summary = _load_run_summary(thread_id)
            record = RunRecord(
                run_id=thread_id,
                workflow_id=workflow_id,
                input_data=json.loads(summary["input_data"]) if summary else {},
                status="paused",
                interrupt_value=payload,
                started_at=(
                    float(summary["started_at"])
                    if summary
                    else _checkpoint_started_at(
                        tuple_.checkpoint.get("ts") if tuple_ else None
                    )
                ),
            )
            events = _load_events(thread_id)
            if events:
                # Full pre-restart history; continue seq numbering after it.
                record.events = events
                record._seq = max(int(e.get("seq", 0)) for e in events)
            RUNS[thread_id] = record
            if not events:
                # No stored history (pre-persistence run): synthesize the pause.
                record.emit({
                    "type": "human_request",
                    "node_id": payload.get("node_id"),
                    "timestamp": time.time(),
                    "data": {"payload": payload},
                })
            _schedule_human_timeout(record, payload)
            recovered += 1
    finally:
        await conn.close()
    return recovered


async def recover_finished_runs() -> int:
    """Rebuild in-memory records for terminal runs persisted before a restart,
    so their logs stay inspectable. Called from the app lifespan at startup."""
    recovered = 0
    for row in _load_finished_summaries():
        if row["run_id"] in RUNS:
            continue
        events = _load_events(row["run_id"])
        record = RunRecord(
            run_id=row["run_id"],
            workflow_id=row["workflow_id"],
            input_data=json.loads(row["input_data"]),
            status=row["status"],
            output_data=(
                json.loads(row["output_data"]) if row["output_data"] else None
            ),
            error=row["error"],
            total_tokens_input=row["total_tokens_input"],
            total_tokens_output=row["total_tokens_output"],
            estimated_cost_usd=row["estimated_cost_usd"],
            started_at=float(row["started_at"]),
            completed_at=row["completed_at"],
        )
        record.events = events
        record._seq = max((int(e.get("seq", 0)) for e in events), default=0)
        RUNS[record.run_id] = record
        recovered += 1
    return recovered


async def _execute(record: RunRecord, workflow: Any, input_data: dict[str, Any]) -> None:
    """Run the graph in a worker thread (streaming events), then emit a terminal."""
    record._loop = asyncio.get_running_loop()
    try:
        result = await asyncio.to_thread(
            run_workflow_sync, workflow, input_data, on_event=record.emit,
            thread_id=record.run_id,
        )
        if result.get("paused"):
            record.status = "paused"
            record.interrupt_value = result.get("interrupt_value")
            record.emit({
                "type": "human_request",
                "node_id": None,
                "timestamp": time.time(),
                "data": {"payload": result.get("interrupt_value")},
            })
            _save_run_summary(record)
            _schedule_human_timeout(record, result.get("interrupt_value"))
            return  # No terminal event; run is waiting for human input.
        record.status = "completed"
        record.output_data = {
            "output": result.get("output", ""),
            "messages_by_node": result.get("messages_by_node", {}),
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
    _save_run_summary(record)
    _prune_runs()
    _prune_store()


async def _resume(record: RunRecord, workflow: Any, human_input: Any) -> None:
    """Resume a paused run with human input (streaming events)."""
    record._loop = asyncio.get_running_loop()
    record.status = "running"
    try:
        result = await asyncio.to_thread(
            resume_workflow, workflow, record.run_id, human_input, on_event=record.emit
        )
        if result.get("paused"):
            record.status = "paused"
            record.interrupt_value = result.get("interrupt_value")
            record.emit({
                "type": "human_request",
                "node_id": None,
                "timestamp": time.time(),
                "data": {"payload": result.get("interrupt_value")},
            })
            _save_run_summary(record)
            _schedule_human_timeout(record, result.get("interrupt_value"))
            return
        record.status = "completed"
        record.output_data = {
            "output": result.get("output", ""),
            "messages_by_node": result.get("messages_by_node", {}),
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
    except Exception as exc:  # noqa: BLE001
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
    _save_run_summary(record)
    _prune_runs()
    _prune_store()


@router.post("/workflows/{workflow_id}/run", status_code=202)
async def run_workflow(workflow_id: str, input_data: dict[str, Any] = {}):
    """Kick off a run in the background and return its id for streaming."""
    workflow = _load_workflow(workflow_id)  # raises 404 if missing
    record = RunRecord(
        run_id=uuid.uuid4().hex, workflow_id=workflow_id, input_data=input_data
    )
    RUNS[record.run_id] = record
    _save_run_summary(record)
    asyncio.create_task(_execute(record, workflow, input_data))
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
    asyncio.create_task(_resume(record, workflow, human_input))
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
