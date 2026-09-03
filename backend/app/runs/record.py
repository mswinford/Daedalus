"""In-memory run records: RunRecord, the RUNS registry, and pruning."""
import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi.encoders import jsonable_encoder
from schema.models import RunEvent

from app.runs.store import MAX_RUNS, _persist_event


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
    # capability name → resolved semver, for invoke nodes and live-tracked imports;
    # resume/restart re-resolves with these pins so the expanded graph and artifact
    # content are stable for the run's lifetime.
    capability_pins: dict[str, str] | None = None
    # capability name → version snapshot taken at run start (invoke pins ∪
    # model/tool provenance); persisted with the run for registry evaluation.
    capability_usage: dict[str, str | None] | None = None
    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    timeout_task: asyncio.Task | None = field(default=None, repr=False)
    # Set by POST /runs/{id}/cancel while the graph is running; the engine
    # checks it between super-steps and stops after the current step.
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
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
