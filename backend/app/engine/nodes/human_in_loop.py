"""Human-in-loop node handler: pauses execution until a human provides input."""
import time

from langgraph.types import interrupt

from schema.models import Node, HumanInLoopNodeConfig
from app.engine.nodes.base import AgentState, NodeContext


class HumanInLoopHandler:
    def build(self, node: Node, ctx: NodeContext):
        config: HumanInLoopNodeConfig = node.config

        async def human_func(state: AgentState) -> AgentState:
            payload = {
                "node_id": node.id,
                # Carried so a restarted process can rebuild the run record
                # from the checkpoint alone (see recover_paused_runs).
                "workflow_id": ctx.workflow.id,
                "message": config.approval_message or "Please provide input",
                "fields": [f.model_dump() for f in config.input_fields],
                "approval_required": config.approval_required,
                "timeout_seconds": config.timeout_seconds,
                "requested_at": time.time(),
            }
            response = interrupt(payload)

            # Rejection: if approval is required and the human explicitly rejected, fail the run.
            if config.approval_required and isinstance(response, dict) and response.get("approved") is False:
                raise RuntimeError(f"Human rejected at node '{node.id}'")

            new_data = {**state.get("data", {})}
            output_fields = config.output_fields or []
            if isinstance(response, dict):
                if len(output_fields) == 1:
                    key = output_fields[0]
                    if key in response:
                        new_data[key] = response[key]
                    elif len(config.input_fields) == 1:
                        new_data[key] = response.get(config.input_fields[0].name)
                    else:
                        new_data[key] = response
                elif output_fields:
                    for i, key in enumerate(output_fields):
                        if key in response:
                            new_data[key] = response[key]
                        elif i < len(config.input_fields):
                            new_data[key] = response.get(config.input_fields[i].name)
                else:
                    new_data.update(response)
            elif output_fields:
                new_data[output_fields[0]] = response

            return {
                "output": str(response),
                "data": new_data,
                "_node_outputs": {
                    **state.get("_node_outputs", {}),
                    node.id: {"response": response},
                },
            }
        return human_func
