"""Invoke exit gate: closes an expanded capability region.

Restores the parent's stashed channels, folds the sub-workflow's final data
into data[output_field] (and optionally the output string), and records the
canonical result under _node_outputs[invoke_id].

Error-aware by construction: if a region failure reached this gate (via the
synthetic type='error' edges expand() adds when the invoke node has a parent
error edge), the _error_info marker is re-keyed to THIS gate's id — the
builder's router only takes an error edge when the marker names the node just
run — and no output is written, since the sub produced no result.
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

            marker = state.get("_error_info") or {}
            failed = marker.get("node_id")
            region_failed = info is not None and failed in info.inner_node_ids

            parent_data = stashed.get("data") or {}
            # Inner entries carry prefixed ids (inv__cf, …) so they cannot
            # collide with the parent's own keys — merge them back in.
            inner_outputs = state.get("_node_outputs") or {}
            inner_messages = state.get("messages_by_node") or {}
            node_outputs = {**stashed.get("_node_outputs", {}), **inner_outputs}
            if not region_failed:
                node_outputs[invoke_id] = {"data": sub_data, "output": sub_output}
            out: dict[str, Any] = {
                "data": parent_data if region_failed else {**parent_data, cfg.output_field: sub_data},
                "output": stashed.get("output", "") if region_failed
                else (sub_output if (cfg.set_output and sub_output) else stashed.get("output", "")),
                "messages_by_node": {**stashed.get("messages_by_node", {}), **inner_messages},
                "_node_outputs": node_outputs,
                "error": stashed.get("error", ""),
                "_invoke_stash": {k: v for k, v in stash_all.items() if k != invoke_id},
            }

            if region_failed:
                # Re-key to this gate's id so its router takes the parent's
                # (re-sourced) error edge; the prefixed id still groups under
                # the invoke row in the run panel.
                out["_error_info"] = {"node_id": node.id, "error": marker.get("error", "")}

            return out

        return run
