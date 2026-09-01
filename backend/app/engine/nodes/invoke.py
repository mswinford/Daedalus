"""Invoke node handler — entry gate for an expanded capability region.

Tool kind: the resolved tool is executed in place (no frame).
Workflow kind: validates the mapped inputs against the sub-workflow's declared
required inputs, then swaps every state channel to a fresh call frame holding
only the mapped inputs, stashing the parent's channels under
_invoke_stash[invoke_id]. The exit gate (invoke_exit) restores them.
"""
from typing import Any

from schema.models import InvokeNodeConfig
from app.engine.conditions import _resolve_path
from app.engine.tools import execute_tool
from app.engine.nodes.base import AgentState, NodeContext, _render_template

# Channels swapped into the frame; everything else in state is untouched by
# inner nodes (LangGraph only writes channels a node returns).
_FRAME_KEYS = ("data", "output", "messages_by_node", "_node_outputs", "error")


def _mapped_inputs(cfg: InvokeNodeConfig, state: AgentState) -> dict[str, Any]:
    """Resolve each mapping: target (sub field) ← source path or rendered transform."""
    values: dict[str, Any] = {}
    for m in cfg.input_mapping:
        if m.transform:
            values[m.target] = _render_template(m.transform, state)
        else:
            values[m.target] = _resolve_path(state, m.source)
    return values


class InvokeHandler:
    def build(self, node, ctx: NodeContext):
        info = ctx.invocations[node.id]
        if info.kind == "tool":
            return self._build_tool(node, info.tool)
        return self._build_entry(node, info)

    def _build_tool(self, node, tool):
        cfg: InvokeNodeConfig = node.config

        async def run(state: AgentState) -> AgentState:
            args = _mapped_inputs(cfg, state)
            result = await execute_tool(tool, args, state)
            return {
                "data": {**state.get("data", {}), cfg.output_field: result},
                "_node_outputs": {**state.get("_node_outputs", {}), node.id: {"result": result}},
            }

        return run

    def _build_entry(self, node, info):
        cfg: InvokeNodeConfig = node.config

        async def run(state: AgentState) -> AgentState:
            frame = _mapped_inputs(cfg, state)
            missing = [f for f in info.required_inputs if frame.get(f) is None]
            if missing:
                raise ValueError(
                    f"invoke '{node.id}' ({info.capability}@{info.version}) "
                    f"is missing required input(s): {', '.join(missing)}"
                )

            stash = dict(state.get("_invoke_stash") or {})
            stash[node.id] = {k: state.get(k) for k in _FRAME_KEYS}
            return {
                "data": frame,
                "output": "",
                "messages_by_node": {},
                "_node_outputs": {},
                "error": "",
                "_invoke_stash": stash,
            }

        return run
