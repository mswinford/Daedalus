"""Transform node handler: templates, field mappings, or delegated custom functions."""
import asyncio

from schema.models import Node, TransformNodeConfig, CustomFunctionNodeConfig
from app.engine.conditions import _resolve_path
from app.sandbox.runner import run_sandboxed
from app.engine.nodes.base import AgentState, NodeContext, _render_template


class TransformHandler:
    def build(self, node: Node, ctx: NodeContext):
        config: TransformNodeConfig = node.config

        async def transform_func(state: AgentState) -> AgentState:
            if config.mode == "custom_function" and config.custom_function_id:
                ref = ctx._nodes_by_id.get(config.custom_function_id)
                if not ref or ref.type != "custom_function":
                    raise ValueError(
                        f"Transform '{node.id}' references unknown or non-custom_function "
                        f"node '{config.custom_function_id}'"
                    )
                ref_config: CustomFunctionNodeConfig = ref.config
                result = await asyncio.to_thread(
                    run_sandboxed, ref_config.code, dict(state), ref_config.timeout_seconds
                )
                if "error" in result:
                    raise RuntimeError(
                        f"Transform '{node.id}' custom function failed: {result['error']}"
                    )

                new_data = {**state.get("data", {}), config.output_field: result}
                return {
                    "output": str(result),
                    "data": new_data,
                    "_node_outputs": {
                        **state.get("_node_outputs", {}),
                        node.id: result,
                    },
                }

            output = ""
            if config.mode == "template" and config.template:
                output = _render_template(config.template, state)
            elif config.mode == "mapping" and config.field_mappings:
                result = {}
                for mapping in config.field_mappings:
                    value = _resolve_path(state, mapping.source)
                    result[mapping.target] = "" if value is None else value
                output = str(result)

            new_data = {**state.get("data", {}), config.output_field: output}

            return {
                "output": output,
                "data": new_data,
                "_node_outputs": {
                    **state.get("_node_outputs", {}),
                    node.id: {config.output_field: output},
                },
            }

        return transform_func
