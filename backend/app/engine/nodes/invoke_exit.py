"""Invoke exit gate: closes an expanded capability region.

Restores the parent's stashed channels, folds the sub-workflow's final data
into data[output_field] (and optionally the output string), and records the
canonical result under _node_outputs[invoke_id].

Error-aware by construction: if a region failure reached this gate (Phase 4
synthetic error edges), the _error_info marker is re-keyed from the inner node
to the invoke node so the parent's error edge can catch it. Dormant until then.
"""
from typing import Any

from schema.models import InvokeExitNodeConfig
from app.engine.nodes.base import AgentState, NodeContext
from app.engine.nodes.invoke import _FRAME_KEYS


class InvokeExitHandler:
    def build(self, node, ctx: NodeContext):
        cfg: InvokeExitNodeConfig = node.config
        invoke_id = cfg.invoke_id
        info = ctx.invocations.get(invoke_id)

        async def run(state: AgentState) -> AgentState:
            stash_all = state.get("_invoke_stash") or {}
            stashed = stash_all.get(invoke_id, {})
            sub_data = state.get("data") or {}
            sub_output = state.get("output") or ""

            parent_data = stashed.get("data") or {}
            # Inner entries carry prefixed ids (inv__cf, …) so they cannot
            # collide with the parent's own keys — merge them back in.
            inner_outputs = state.get("_node_outputs") or {}
            inner_messages = state.get("messages_by_node") or {}
            out: dict[str, Any] = {
                "data": {**parent_data, cfg.output_field: sub_data},
                "output": sub_output if (cfg.set_output and sub_output) else stashed.get("output", ""),
                "messages_by_node": {**stashed.get("messages_by_node", {}), **inner_messages},
                "_node_outputs": {
                    **stashed.get("_node_outputs", {}),
                    **inner_outputs,
                    invoke_id: {"data": sub_data, "output": sub_output},
                },
                "error": stashed.get("error", ""),
                "_invoke_stash": {k: v for k, v in stash_all.items() if k != invoke_id},
            }

            marker = state.get("_error_info") or {}
            failed = marker.get("node_id")
            if info is not None and failed in info.inner_node_ids:
                out["_error_info"] = {"node_id": invoke_id, "error": marker.get("error", "")}

            return out

        return run
