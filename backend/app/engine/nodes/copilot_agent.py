"""Copilot agent node handler: delegates an atomic agentic step to the
GitHub Copilot SDK runtime (planning, tool calls, file edits). The node
never pauses mid-step — it is one super-step from the graph's point of view.
"""
import os
import time
from dataclasses import asdict

from schema.models import Node, CopilotAgentNodeConfig, RunEvent
from app.engine.copilot import create_copilot_runtime
from app.engine.nodes.base import AgentState, NodeContext, _render_template


class CopilotAgentHandler:
    def __init__(self, runtime_factory=None):
        # Injectable for tests; None → resolved at build time so the module-level
        # create_copilot_runtime can be monkeypatched after import.
        self._runtime_factory = runtime_factory

    def build(self, node: Node, ctx: NodeContext):
        config: CopilotAgentNodeConfig = node.config
        factory = self._runtime_factory or create_copilot_runtime

        async def run(state: AgentState) -> AgentState:
            task = _render_template(config.task, state)
            working_dir = _resolve_workdir(config, ctx, node.id)
            os.makedirs(working_dir, exist_ok=True)

            github_token = None
            if config.auth_ref:
                from app.secrets import get_secret
                github_token = get_secret(config.auth_ref)
                if not github_token:
                    raise ValueError(
                        f"Copilot node '{node.id}': secret '{config.auth_ref}' not found"
                    )

            def on_event(kind: str, payload: dict) -> None:
                ctx._emit(RunEvent(type=kind, node_id=node.id, timestamp=time.time(), data=payload))

            result = await factory().run_task(
                task=task,
                model=config.model,
                working_dir=working_dir,
                permission_policy=config.permission_policy,
                timeout_seconds=config.timeout_seconds,
                github_token=github_token,
                on_event=on_event,
            )

            outputs = {
                "final_message": result.final_message,
                "model": result.model,
                "tool_calls": [asdict(tc) for tc in result.tool_calls],
                "tokens_input": result.tokens_input,
                "tokens_output": result.tokens_output,
                "cost_usd": result.cost_usd,
                "working_dir": working_dir,
            }

            fields = config.output_fields or ["final_message"]
            data = dict(state.get("data", {}))
            for f in fields:
                if f in outputs:
                    data[f] = outputs[f]

            return {
                "output": result.final_message,
                "data": data,
                "_node_outputs": {
                    **state.get("_node_outputs", {}),
                    node.id: outputs,
                },
            }

        return run


def _resolve_workdir(config: CopilotAgentNodeConfig, ctx: NodeContext, node_id: str) -> str:
    if config.working_dir and config.working_dir != "scratch":
        return config.working_dir  # absoluteness enforced by validation
    from app.config import get_settings
    run_id = getattr(ctx, "run_id", None) or "default"
    return os.path.join(str(get_settings().data_dir), "runs", run_id, f"copilot-{node_id}")
