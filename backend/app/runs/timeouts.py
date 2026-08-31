"""Human-in-loop timeout handling: arming, cancellation, and auto-fail."""
import asyncio
import time
from typing import Any

from app.runs.record import RunRecord, _prune_runs
from app.runs.store import _prune_store, _save_run_summary


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
