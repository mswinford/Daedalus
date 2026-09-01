"""Startup recovery: rebuild in-memory run records from the SQLite store."""
import json
import sqlite3
import time
from datetime import datetime
from typing import Any

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import get_settings
from app.engine.builder import GraphBuilder
from app.api.workflows import _load_workflow
from app.runs.record import RUNS, RunRecord
from app.runs.store import (
    _load_events,
    _load_finished_summaries,
    _load_run_summary,
    _save_run_summary,
)
from app.runs.timeouts import _schedule_human_timeout

# Matches the default serde of AsyncSqliteSaver; used to decode raw
# __interrupt__ writes during recovery.
_RECOVERY_SERDE = JsonPlusSerializer()


def _checkpoint_started_at(ts: Any) -> float:
    """Parse a checkpoint's ISO timestamp into an epoch value."""
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (TypeError, ValueError):
        return time.time()


def _fail_unrecoverable_run(thread_id: str, workflow_id: str, summary: Any, error: str) -> None:
    """Fail a paused run whose pinned capability can no longer be resolved.

    The record is rebuilt as failed with a terminal node_error event so the run
    stays inspectable (GET /runs/{id}) instead of vanishing from RUNS. A newer
    version must NOT be substituted: a different artifact changes the expanded
    graph structure and would break checkpoint matching.
    """
    if thread_id in RUNS:
        return
    record = RunRecord(
        run_id=thread_id,
        workflow_id=workflow_id,
        input_data=json.loads(summary["input_data"]) if summary else {},
        status="failed",
        error=error,
        started_at=float(summary["started_at"]) if summary else time.time(),
    )
    record.completed_at = time.time()
    events = _load_events(thread_id)
    if events:
        record.events = events
        record._seq = max(int(e.get("seq", 0)) for e in events)
    record.emit({
        "type": "node_error",
        "node_id": None,
        "timestamp": time.time(),
        "data": {"error": error, "fatal": True},
    })
    RUNS[thread_id] = record
    _save_run_summary(record)


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
            summary = _load_run_summary(thread_id)
            pins = (
                json.loads(summary["invoke_pins"])
                if summary is not None and summary["invoke_pins"]
                else None
            )
            graph = graphs.get(workflow_id)
            if graph is None:
                try:
                    workflow = _load_workflow(workflow_id)
                except Exception:
                    continue  # Workflow deleted.
                invocations: dict[str, Any] | None = None
                if pins:
                    from app.capability_client import CapabilityNotFoundError
                    from app.engine.expand import prepare_workflow_for_run

                    try:
                        # Checkpoints were written against the expanded graph;
                        # rebuild it identically from the stored pins.
                        workflow, invocations, _ = prepare_workflow_for_run(workflow, pins=pins)
                    except CapabilityNotFoundError as exc:
                        # A pinned version was deleted from the registry: the
                        # checkpointed graph can never be rebuilt. Fail loudly.
                        _fail_unrecoverable_run(thread_id, workflow_id, summary, str(exc))
                        continue
                    except Exception:
                        continue  # Registry unreachable; retried on next startup.
                try:
                    graph = GraphBuilder(workflow, invocations=invocations).build(checkpointer=saver)
                except Exception as exc:
                    # The graph can't be rebuilt — e.g. a model's secret is no
                    # longer set, or the workflow is malformed. Fail this run
                    # loudly so it stays inspectable instead of crashing startup.
                    _fail_unrecoverable_run(thread_id, workflow_id, summary, str(exc))
                    continue
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
