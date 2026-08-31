"""Run execution: drive a workflow (or resume it) in a worker thread."""
import asyncio
import time
from typing import Any

from app.engine.runner import run_workflow_sync, resume_workflow
from app.runs.record import _prune_runs
from app.runs.store import _prune_store, _save_run_summary
from app.runs.timeouts import _schedule_human_timeout


async def _drive(record, workflow, *, input_data=None, human_input=None):
    """Run (or resume) the graph in a worker thread (streaming events), then emit
    a terminal."""
    record._loop = asyncio.get_running_loop()
    record.status = "running"
    try:
        if human_input is None:
            result = await asyncio.to_thread(
                run_workflow_sync, workflow, input_data, on_event=record.emit,
                thread_id=record.run_id,
            )
        else:
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
