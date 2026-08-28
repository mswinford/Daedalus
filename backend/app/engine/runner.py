"""Execute workflows using LangGraph."""
from typing import Any

from schema.models import Workflow, StateFieldType
from app.engine.builder import GraphBuilder


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


def run_workflow_sync(workflow: Workflow, input_data: dict[str, Any]) -> dict[str, Any]:
    """Execute a workflow synchronously.

    For Phase 1, this is a blocking call. In Phase 2, we'll make this async
    with WebSocket streaming.
    """
    import asyncio

    _validate_input(workflow, input_data)

    builder = GraphBuilder(workflow)
    graph = builder.build()

    # Build initial state from input data. Reserved keys map directly to state
    # channels; anything else is collected under `data` so it stays addressable
    # by conditions (e.g. $.data.score) and nodes.
    reserved = {"messages", "output", "error", "data", "_node_outputs"}
    initial_state = {
        "messages": [],
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

    # Run the graph
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(graph.ainvoke(initial_state))
    finally:
        loop.close()

    # Extract output
    return {
        "output": result.get("output", ""),
        "messages": result.get("messages", []),
        "data": result.get("data", {}),
        "node_outputs": result.get("_node_outputs", {}),
    }
