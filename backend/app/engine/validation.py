"""Static validation of a workflow definition (no execution, no LLM calls).

Mirrors the structural rules the GraphBuilder applies at build time so the
editor's "Validate" button can surface problems before a run is attempted.
"""
import re
from typing import Optional

from pydantic import BaseModel

from schema.models import Workflow

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")


class ValidationIssue(BaseModel):
    level: str  # "error" | "warning"
    code: str
    message: str
    node_id: Optional[str] = None
    edge_id: Optional[str] = None


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    @property
    def issues(self) -> list[ValidationIssue]:
        return self.errors + self.warnings


def _find_cycle(out_edges: dict, node_ids: set) -> Optional[list]:
    """Return one cycle (list of node ids) if the graph has a directed cycle."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}
    parent: dict = {}

    def dfs(u):
        color[u] = GRAY
        for e in out_edges.get(u, []):
            v = e.target_node_id
            if v not in color:
                continue
            if color[v] == GRAY:
                cycle = [v, u]
                cur = u
                while cur != v and cur in parent:
                    cur = parent[cur]
                    cycle.append(cur)
                return list(reversed(cycle))
            if color[v] == WHITE:
                parent[v] = u
                found = dfs(v)
                if found:
                    return found
        color[u] = BLACK
        return None

    for nid in node_ids:
        if color[nid] == WHITE:
            found = dfs(nid)
            if found:
                return found
    return None


def validate_workflow(workflow: Workflow) -> ValidationResult:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    nodes = workflow.nodes
    edges = workflow.edges

    # --- index nodes, detect duplicate ids ---
    nodes_by_id: dict = {}
    seen_node_ids: set = set()
    for n in nodes:
        if n.id in seen_node_ids:
            errors.append(ValidationIssue(
                level="error", code="E_DUPLICATE_NODE_ID",
                message=f"Duplicate node id '{n.id}'", node_id=n.id,
            ))
        seen_node_ids.add(n.id)
        nodes_by_id[n.id] = n

    # --- index edges, detect duplicate ids + dangling endpoints ---
    seen_edge_ids: set = set()
    for e in edges:
        if e.id in seen_edge_ids:
            errors.append(ValidationIssue(
                level="error", code="E_DUPLICATE_EDGE_ID",
                message=f"Duplicate edge id '{e.id}'", edge_id=e.id,
            ))
        seen_edge_ids.add(e.id)
        if e.source_node_id not in nodes_by_id:
            errors.append(ValidationIssue(
                level="error", code="E_EDGE_SOURCE_MISSING",
                message=f"Edge '{e.id}' points to unknown source node '{e.source_node_id}'",
                edge_id=e.id,
            ))
        if e.target_node_id not in nodes_by_id:
            errors.append(ValidationIssue(
                level="error", code="E_EDGE_TARGET_MISSING",
                message=f"Edge '{e.id}' points to unknown target node '{e.target_node_id}'",
                edge_id=e.id,
            ))

    if not nodes:
        errors.append(ValidationIssue(level="error", code="E_NO_NODES",
                                      message="Workflow has no nodes"))
        return ValidationResult(valid=False, errors=errors, warnings=warnings)

    # --- start / end presence ---
    start_nodes = [n for n in nodes if n.type == "start"]
    end_nodes = [n for n in nodes if n.type == "end"]
    if not start_nodes:
        warnings.append(ValidationIssue(
            level="warning", code="W_NO_START",
            message="No start node; execution will begin at the first non-end node",
        ))
    elif len(start_nodes) > 1:
        warnings.append(ValidationIssue(
            level="warning", code="W_MULTIPLE_STARTS",
            message=f"Workflow has {len(start_nodes)} start nodes; expected exactly one",
        ))
    if not end_nodes:
        warnings.append(ValidationIssue(
            level="warning", code="W_NO_END",
            message="No end node; the workflow may never terminate cleanly",
        ))

    # --- outgoing edges index ---
    out_edges: dict = {}
    for e in edges:
        out_edges.setdefault(e.source_node_id, []).append(e)

    if start_nodes and not out_edges.get(start_nodes[0].id):
        errors.append(ValidationIssue(
            level="error", code="E_START_NO_OUTGOING",
            message=f"Start node '{start_nodes[0].id}' has no outgoing edges",
            node_id=start_nodes[0].id,
        ))

    model_ids = {m.id for m in workflow.models}
    tool_ids = {t.id for t in workflow.tools}
    prompt_ids = {p.id for p in workflow.prompts}

    # --- per-node config checks ---
    for n in nodes:
        cfg = n.config

        if n.type == "agent":
            if cfg.model_id not in model_ids:
                errors.append(ValidationIssue(
                    level="error", code="E_AGENT_MODEL_MISSING",
                    message=f"Agent '{n.id}' references unknown model '{cfg.model_id}'",
                    node_id=n.id,
                ))
            for tid in cfg.tool_ids:
                if tid not in tool_ids:
                    errors.append(ValidationIssue(
                        level="error", code="E_AGENT_TOOL_MISSING",
                        message=f"Agent '{n.id}' references unknown tool '{tid}'",
                        node_id=n.id,
                    ))
            if cfg.prompt_ref is not None and cfg.prompt_ref not in prompt_ids:
                errors.append(ValidationIssue(
                    level="error", code="E_AGENT_PROMPT_MISSING",
                    message=f"Agent '{n.id}' references unknown prompt '{cfg.prompt_ref}'",
                    node_id=n.id,
                ))
            for skill in cfg.skills:
                for tid in skill.tool_ids:
                    if tid not in tool_ids:
                        errors.append(ValidationIssue(
                            level="error", code="E_AGENT_TOOL_MISSING",
                            message=f"Skill '{skill.name or 'unnamed'}' on agent '{n.id}' "
                                    f"references unknown tool '{tid}'",
                            node_id=n.id,
                        ))

        elif n.type == "conditional":
            cond_edges = out_edges.get(n.id, [])
            branch_edges = [e for e in cond_edges if e.source_handle != "default" and e.type != "error"]
            handles = {e.source_handle for e in cond_edges}

            if len(branch_edges) != len(cfg.conditions):
                warnings.append(ValidationIssue(
                    level="warning", code="W_CONDITIONAL_BRANCH_MISMATCH",
                    message=f"Conditional '{n.id}' has {len(cfg.conditions)} conditions but "
                            f"{len(branch_edges)} branch edges; they must match positionally",
                    node_id=n.id,
                ))

            preferred = cfg.default_branch
            if preferred:
                if preferred not in handles:
                    errors.append(ValidationIssue(
                        level="error", code="E_CONDITIONAL_BAD_DEFAULT",
                        message=f"Conditional '{n.id}' default_branch '{preferred}' "
                                f"matches no outgoing handle",
                        node_id=n.id,
                    ))
            elif "default" not in handles:
                errors.append(ValidationIssue(
                    level="error", code="E_CONDITIONAL_NO_FALLBACK",
                    message=f"Conditional '{n.id}' has no default branch; "
                            f"it will fail if no condition matches",
                    node_id=n.id,
                ))

            for cond in cfg.conditions:
                if cond.type.value == "llm":
                    warnings.append(ValidationIssue(
                        level="warning", code="W_LLM_CONDITION",
                        message=f"Conditional '{n.id}' uses an LLM condition (not supported yet)",
                        node_id=n.id,
                    ))

        elif n.type == "transform":
            if cfg.mode == "custom_function":
                ref_id = cfg.custom_function_id
                if not ref_id:
                    errors.append(ValidationIssue(
                        level="error", code="E_TRANSFORM_FUNCTION_MISSING",
                        message=f"Transform '{n.id}' uses custom_function mode but has no custom_function_id",
                        node_id=n.id,
                    ))
                else:
                    ref = nodes_by_id.get(ref_id)
                    if not ref:
                        errors.append(ValidationIssue(
                            level="error", code="E_TRANSFORM_FUNCTION_MISSING",
                            message=f"Transform '{n.id}' references unknown node '{ref_id}'",
                            node_id=n.id,
                        ))
                    elif ref.type != "custom_function":
                        errors.append(ValidationIssue(
                            level="error", code="E_TRANSFORM_FUNCTION_TYPE",
                            message=f"Transform '{n.id}' references node '{ref_id}' which is not a custom_function (it's {ref.type})",
                            node_id=n.id,
                        ))

        elif n.type == "human_in_loop":
            cfg = n.config
            if not cfg.output_fields:
                warnings.append(ValidationIssue(
                    level="error", code="E_HIL_NO_OUTPUTS",
                    message=f"Human-in-loop '{n.id}' has no output_fields defined",
                    node_id=n.id,
                ))
            for f in cfg.input_fields:
                if not f.name:
                    warnings.append(ValidationIssue(
                        level="error", code="E_HIL_FIELD_NO_NAME",
                        message=f"Human-in-loop '{n.id}' has an input field with no name",
                        node_id=n.id,
                    ))
                    break

        elif n.type == "invoke":
            cfg = n.config
            parts = cfg.capability.split("/")
            if len(parts) != 2 or not all(parts):
                errors.append(ValidationIssue(
                    level="error", code="E_INVOKE_BAD_CAPABILITY",
                    message=f"Invoke '{n.id}' capability must be 'owner/name', "
                            f"got '{cfg.capability}'",
                    node_id=n.id,
                ))
            if cfg.version != "latest" and not _SEMVER_RE.match(cfg.version):
                errors.append(ValidationIssue(
                    level="error", code="E_INVOKE_BAD_VERSION",
                    message=f"Invoke '{n.id}' version must be 'latest' or a semver "
                            f"string, got '{cfg.version}'",
                    node_id=n.id,
                ))
            if not cfg.output_field:
                errors.append(ValidationIssue(
                    level="error", code="E_INVOKE_NO_OUTPUT_FIELD",
                    message=f"Invoke '{n.id}' has an empty output_field",
                    node_id=n.id,
                ))
            seen_targets: set = set()
            for m in cfg.input_mapping:
                if not m.target:
                    errors.append(ValidationIssue(
                        level="error", code="E_INVOKE_MAPPING_NO_TARGET",
                        message=f"Invoke '{n.id}' has an input mapping with no target",
                        node_id=n.id,
                    ))
                    continue
                if m.target in seen_targets:
                    warnings.append(ValidationIssue(
                        level="warning", code="W_INVOKE_DUPLICATE_MAPPING",
                        message=f"Invoke '{n.id}' maps target '{m.target}' more than "
                                f"once; only the last mapping takes effect",
                        node_id=n.id,
                    ))
                seen_targets.add(m.target)
                if not m.transform and not m.source:
                    errors.append(ValidationIssue(
                        level="error", code="E_INVOKE_MAPPING_EMPTY",
                        message=f"Invoke '{n.id}' input mapping for '{m.target}' has "
                                f"neither a source path nor a transform",
                        node_id=n.id,
                    ))

        elif n.type == "invoke_exit":
            errors.append(ValidationIssue(
                level="error", code="E_INVOKE_EXIT_SAVED",
                message=f"Node '{n.id}' is an invoke exit gate — a runtime-only node "
                        f"that must not be saved in a workflow",
                node_id=n.id,
            ))

        if n.type not in ("start", "end") and not out_edges.get(n.id):
            warnings.append(ValidationIssue(
                level="warning", code="W_DEAD_END",
                message=f"Node '{n.id}' has no outgoing edges; the workflow may terminate here",
                node_id=n.id,
            ))

    # --- error edges ---
    for e in edges:
        if e.type != "error":
            continue
        src = nodes_by_id.get(e.source_node_id)
        if src is None:
            continue  # already reported as E_EDGE_SOURCE_MISSING
        if src.type == "start":
            errors.append(ValidationIssue(
                level="error", code="E_ERROR_EDGE_FROM_START",
                message=f"Error edge '{e.id}': the start node cannot fail",
                edge_id=e.id,
            ))
        elif not src.error_handling:
            warnings.append(ValidationIssue(
                level="warning", code="W_ERROR_EDGE_NO_OPTIN",
                message=f"Error edge '{e.id}' leaves '{src.id}', which has error handling "
                        f"disabled; the edge will never be taken",
                edge_id=e.id,
            ))
        if not any(o for o in out_edges.get(src.id, []) if o.type != "error"):
            errors.append(ValidationIssue(
                level="error", code="E_ERROR_EDGE_NO_FALLBACK",
                message=f"Error edge '{e.id}': node '{src.id}' has no success path; "
                        f"it also needs a default outgoing edge",
                edge_id=e.id,
            ))

    error_edge_counts: dict = {}
    for e in edges:
        if e.type == "error":
            error_edge_counts[e.source_node_id] = error_edge_counts.get(e.source_node_id, 0) + 1
    for src_id, count in error_edge_counts.items():
        if count > 1:
            errors.append(ValidationIssue(
                level="error", code="E_MULTIPLE_ERROR_EDGES",
                message=f"Node '{src_id}' has {count} error edges; only one is allowed per node",
                node_id=src_id,
            ))

    # --- reachability from entry point ---
    entry = (
        start_nodes[0].id if start_nodes
        else next((n.id for n in nodes if n.type != "end"), None)
    )
    if entry:
        visited: set = set()
        stack = [entry]
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            for e in out_edges.get(cur, []):
                if e.target_node_id not in visited:
                    stack.append(e.target_node_id)
        for n in nodes:
            if n.id not in visited:
                warnings.append(ValidationIssue(
                    level="warning", code="W_UNREACHABLE_NODE",
                    message=f"Node '{n.id}' is not reachable from the entry point",
                    node_id=n.id,
                ))

    # --- cycle detection ---
    cycle = _find_cycle(out_edges, seen_node_ids)
    if cycle:
        warnings.append(ValidationIssue(
            level="warning", code="W_CYCLE_DETECTED",
            message=(
                f"Loop through nodes: {' -> '.join(cycle)} — cycles are allowed "
                f"(loops), but every run is bounded by the per-run step cap; a loop "
                f"that never exits fails the run with an iteration_limit event."
            ),
        ))

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)
