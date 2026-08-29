"""Execute workflows using LangGraph."""
from typing import Any, Callable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from schema.models import Workflow, StateFieldType, RunEvent
from app.engine.builder import GraphBuilder

# Shared checkpointer so paused runs can be resumed within the same process.
_CHECKPOINTER = MemorySaver()


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
    reserved = {"messages_by_node", "output", "error", "data", "_node_outputs"}
    initial_state: dict[str, Any] = {
        "messages_by_node": {},
        "output": "",
        "error": "",
        "data": {},
        "_node_outputs": {},
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
    """
    import asyncio

    _validate_input(workflow, input_data)

    builder = GraphBuilder(workflow, trace=trace, on_event=on_event)
    graph = builder.build(checkpointer=_CHECKPOINTER)

    initial_state = _build_initial_state(input_data)
    config = {"configurable": {"thread_id": thread_id or "default"}}

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(graph.ainvoke(initial_state, config=config))
    finally:
        loop.close()

    if "__interrupt__" in result:
        interrupt_obj = result["__interrupt__"][0]
        return {
            "paused": True,
            "interrupt_value": interrupt_obj.value,
            "events": builder._trace,
        }

    return _extract_result(builder, result)


def _pending_interrupt_ids(graph: Any, config: dict[str, Any]) -> list[str]:
    """Ids of all interrupts currently pending on this thread (empty if none)."""
    snapshot = graph.get_state(config)
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
) -> dict[str, Any]:
    """Resume a paused workflow with human-provided input.

    Rebuilds the same graph (same node structure) and invokes it with
    `Command(resume=...)` so LangGraph continues from the checkpoint.
    Returns the same shape as `run_workflow_sync`.
    """
    import asyncio

    builder = GraphBuilder(workflow, trace=trace, on_event=on_event)
    graph = builder.build(checkpointer=_CHECKPOINTER)

    config = {"configurable": {"thread_id": thread_id}}

    # Resume through the explicit interrupt-id map form when exactly one
    # interrupt is pending. A bare `Command(resume=value)` is unsafe for two
    # values in langgraph 1.2.x: `{}` is misread as an empty interrupt-id map
    # and the interrupt silently re-fires, and `None` hits an UnboundLocalError
    # in langgraph/pregel/_loop.py (resume_is_map). The map form handles any
    # value uniformly; with multiple pending interrupts we fall back to the
    # bare form so LangGraph raises its clear "specify the interrupt id" error.
    interrupt_ids = _pending_interrupt_ids(graph, config)
    if len(interrupt_ids) == 1:
        resume_value: Any = {interrupt_ids[0]: human_input}
    else:
        resume_value = human_input

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            graph.ainvoke(Command(resume=resume_value), config=config)
        )
    finally:
        loop.close()

    if "__interrupt__" in result:
        interrupt_obj = result["__interrupt__"][0]
        return {
            "paused": True,
            "interrupt_value": interrupt_obj.value,
            "events": builder._trace,
        }

    return _extract_result(builder, result)
