"""Custom function node handler: executes user Python code in the sandbox."""
import asyncio

from schema.models import Node, CustomFunctionNodeConfig
from app.sandbox.runner import run_sandboxed
from app.engine.nodes.base import AgentState, NodeContext


class CustomFunctionHandler:
    def build(self, node: Node, ctx: NodeContext):
        config: CustomFunctionNodeConfig = node.config

        async def custom_func(state: AgentState) -> AgentState:
            result = await asyncio.to_thread(
                run_sandboxed, config.code, dict(state), config.timeout_seconds
            )

            if "error" in result:
                raise RuntimeError(f"Custom function '{node.id}' failed: {result['error']}")

            # Write back the declared output fields into `data` so downstream
            # nodes can address them (e.g. $.data.grade). Undeclared keys stay
            # only in _node_outputs.
            new_data = {**state.get("data", {})}
            for field in config.output_fields:
                if field in result:
                    new_data[field] = result[field]

            return {
                "output": str(result),
                "data": new_data,
                "_node_outputs": {
                    **state.get("_node_outputs", {}),
                    node.id: result,
                },
            }

        return custom_func
