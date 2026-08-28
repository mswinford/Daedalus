"""Execute workflows using LangGraph."""
from typing import Any

from schema.models import Workflow
from app.engine.builder import GraphBuilder


def run_workflow_sync(workflow: Workflow, input_data: dict[str, Any]) -> dict[str, Any]:
    """Execute a workflow synchronously.

    For Phase 1, this is a blocking call. In Phase 2, we'll make this async
    with WebSocket streaming.
    """
    import asyncio

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
        "node_outputs": result.get("_node_outputs", {}),
    }
