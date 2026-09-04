"""Execute workflows using LangGraph."""
import threading
from typing import Any, Callable

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from schema.models import Workflow, StateFieldType, RunEvent
from app.config import get_settings
from app.engine.builder import GraphBuilder
from app.sqlite_util import secure_owner_only


class IterationLimitExceeded(RuntimeError):
    """Raised when a run exceeds the per-segment super-step cap (loop guard)."""

    def __init__(self, steps: int):
        super().__init__(f"Run exceeded the {steps}-step limit (possible infinite loop)")
        self.steps = steps


# Hard bound on super-steps per drive segment. Cycles in the graph are legal
# (loops); this cap guarantees a run can never hang. Counted per segment, so
# a paused run gets a fresh budget after each resume — pauses are human-paced,
# and any runaway is still cancellable.
MAX_SUPER_STEPS = 500


async def _invoke_with_cancel(graph: Any, payload: Any, config: dict[str, Any],
                              cancel_event: threading.Event | None) -> dict | None:
    """Drive the graph super-step by super-step, checking for cancellation
    between steps. Returns the final state, or None if a cancel was requested
    (the in-flight step finishes first; no further steps run). Raises
    IterationLimitExceeded when the step cap is hit."""
    # stream_mode="values" (explicit: the default yields per-node updates, not
    # full state snapshots). The final snapshot matches ainvoke's result,
    # including the __interrupt__ key when a human_in_loop node pauses.
    result = None
    steps = 0
    async for chunk in graph.astream(payload, config=config, stream_mode="values"):
        result = chunk
        steps += 1
        if steps > MAX_SUPER_STEPS:
            raise IterationLimitExceeded(MAX_SUPER_STEPS)
        if cancel_event is not None and cancel_event.is_set():
            return None
    return result


async def _open_checkpointer() -> AsyncSqliteSaver:
    """Open a per-run checkpointer on the shared SQLite checkpoint store.

    Checkpoints live in `settings.checkpoint_db` so paused runs survive process
    restarts. Each run executes in its own event loop (worker thread) and
    aiosqlite connections bind to the loop that created them, so we open one
    connection per run instead of sharing a single saver. Concurrent runs are
    safe via WAL mode plus the busy timeout on `aiosqlite.connect`.
    """
    settings = get_settings()
    path = str(settings.checkpoint_db)
    conn = await aiosqlite.connect(path, timeout=5.0)
    secure_owner_only(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    return AsyncSqliteSaver(conn)


def _validate_input(workflow: Workflow, input_data: dict[str, Any]) -> None:
    """Validate run input against workflow.state_schema (if defined)."""
    schema = workflow.state_schema
    if not schema or not schema.fields:
        return

    for field in schema.fields:
        if field.required and field.name not in input_data:
            raise ValueError(
                f"Required input field '{field.name}' ({field.type.value}) is missing"
            )
        if field.name not in input_data:
            continue
        value = input_data[field.name]
        expected = field.type
        if expected == StateFieldType.STRING and not isinstance(value, str):
            raise ValueError(f"Field '{field.name}' expects string, got {type(value).__name__}")
        elif expected == StateFieldType.NUMBER and not isinstance(value, (int, float)):
            raise ValueError(f"Field '{field.name}' expects number, got {type(value).__name__}")
        elif expected == StateFieldType.BOOLEAN and not isinstance(value, bool):
            raise ValueError(f"Field '{field.name}' expects boolean, got {type(value).__name__}")
        elif expected == StateFieldType.ARRAY and not isinstance(value, list):
            raise ValueError(f"Field '{field.name}' expects array, got {type(value).__name__}")
        elif expected == StateFieldType.OBJECT and not isinstance(value, dict):
            raise ValueError(f"Field '{field.name}' expects object, got {type(value).__name__}")


def _build_initial_state(input_data: dict[str, Any]) -> dict[str, Any]:
    """Build initial graph state from run input data."""
    reserved = {"messages_by_node", "output", "error", "data", "_node_outputs", "_invoke_stash"}
    initial_state: dict[str, Any] = {
        "messages_by_node": {},
        "output": "",
        "error": "",
        "data": {},
        "_node_outputs": {},
        "_invoke_stash": {},
    }
    for key, value in input_data.items():
        if key in reserved:
            initial_state[key] = value
        else:
            initial_state["data"][key] = value
    return initial_state


def _extract_result(builder: GraphBuilder, result: dict[str, Any]) -> dict[str, Any]:
    """Extract output + execution trace from a completed graph result."""
    return {
        "output": result.get("output", ""),
        "messages_by_node": result.get("messages_by_node", {}),
        "data": result.get("data", {}),
        "node_outputs": result.get("_node_outputs", {}),
        "events": builder._trace,
        "total_tokens_input": builder.total_tokens_input,
        "total_tokens_output": builder.total_tokens_output,
        "estimated_cost_usd": builder.estimated_cost_usd,
    }


def run_workflow_sync(
    workflow: Workflow,
    input_data: dict[str, Any],
    trace: list | None = None,
    on_event: Callable[[RunEvent], None] | None = None,
    thread_id: str | None = None,
    invocations: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Execute a workflow (blocking).

    If `trace` is provided, node/llm execution events are appended to it (so
    partial traces survive a mid-run failure). If `on_event` is provided, each
    event is also forwarded to it as it happens — used by the async/WebSocket
    layer to stream progress live. This call blocks for the entire run, so
    callers that need responsiveness wrap it in a worker thread.

    If the workflow contains a human_in_loop node, execution pauses and the
    return dict includes `{"paused": True, "interrupt_value": ...}`. The caller
    should then use `resume_workflow` to continue.

    If `cancel_event` is set while the graph runs, execution stops after the
    current super-step and the return dict is `{"cancelled": True}`.
    """
    import asyncio

    _validate_input(workflow, input_data)

    builder = GraphBuilder(
        workflow, trace=trace, on_event=on_event, invocations=invocations
    )

    initial_state = _build_initial_state(input_data)
    config = {"configurable": {"thread_id": thread_id or "default"}}

    loop = asyncio.new_event_loop()
    checkpointer: AsyncSqliteSaver | None = None
    try:
        checkpointer = loop.run_until_complete(_open_checkpointer())
        graph = builder.build(checkpointer=checkpointer)
        result = loop.run_until_complete(
            _invoke_with_cancel(graph, initial_state, config, cancel_event)
        )
    finally:
        if checkpointer is not None:
            loop.run_until_complete(checkpointer.conn.close())
        loop.close()

    if result is None:
        return {"cancelled": True}

    if "__interrupt__" in result:
        interrupt_obj = result["__interrupt__"][0]
        return {
            "paused": True,
            "interrupt_value": interrupt_obj.value,
            "events": builder._trace,
        }

    return _extract_result(builder, result)


async def _pending_interrupt_ids(graph: Any, config: dict[str, Any]) -> list[str]:
    """Ids of all interrupts currently pending on this thread (empty if none)."""
    snapshot = await graph.aget_state(config)
    return [
        intr.id
        for task in snapshot.tasks
        for intr in (task.interrupts or ())
    ]


def resume_workflow(
    workflow: Workflow,
    thread_id: str,
    human_input: Any,
    trace: list | None = None,
    on_event: Callable[[RunEvent], None] | None = None,
    invocations: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Resume a paused workflow with human-provided input.

    Rebuilds the same graph (same node structure) and invokes it with
    `Command(resume=...)` so LangGraph continues from the checkpoint.
    Returns the same shape as `run_workflow_sync` (including its
    `{"cancelled": True}` behavior when `cancel_event` is set mid-run).
    """
    import asyncio

    builder = GraphBuilder(
        workflow, trace=trace, on_event=on_event, invocations=invocations
    )

    config = {"configurable": {"thread_id": thread_id}}

    # Resume through the explicit interrupt-id map form when exactly one
    # interrupt is pending. A bare `Command(resume=value)` is unsafe for two
    # values in langgraph 1.2.x: `{}` is misread as an empty interrupt-id map
    # and the interrupt silently re-fires, and `None` hits an UnboundLocalError
    # in langgraph/pregel/_loop.py (resume_is_map). The map form handles any
    # value uniformly; with multiple pending interrupts we fall back to the
    # bare form so LangGraph raises its clear "specify the interrupt id" error.

    loop = asyncio.new_event_loop()
    checkpointer: AsyncSqliteSaver | None = None
    try:
        checkpointer = loop.run_until_complete(_open_checkpointer())
        graph = builder.build(checkpointer=checkpointer)
        interrupt_ids = loop.run_until_complete(
            _pending_interrupt_ids(graph, config)
        )
        if len(interrupt_ids) == 1:
            resume_value: Any = {interrupt_ids[0]: human_input}
        else:
            resume_value = human_input
        result = loop.run_until_complete(
            _invoke_with_cancel(graph, Command(resume=resume_value), config, cancel_event)
        )
    finally:
        if checkpointer is not None:
            loop.run_until_complete(checkpointer.conn.close())
        loop.close()

    if result is None:
        return {"cancelled": True}

    if "__interrupt__" in result:
        interrupt_obj = result["__interrupt__"][0]
        return {
            "paused": True,
            "interrupt_value": interrupt_obj.value,
            "events": builder._trace,
        }

    return _extract_result(builder, result)
