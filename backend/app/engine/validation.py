"""Static validation of a workflow definition (no execution, no LLM calls).

Mirrors the structural rules the GraphBuilder applies at build time so the
editor's "Validate" button can surface problems before a run is attempted.
"""
from typing import Optional

from pydantic import BaseModel

from schema.models import Workflow


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

        elif n.type == "conditional":
            cond_edges = out_edges.get(n.id, [])
            branch_edges = [e for e in cond_edges if e.source_handle != "default"]
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

        elif n.type == "human_in_loop":
            warnings.append(ValidationIssue(
                level="warning", code="W_HUMAN_NOT_SUPPORTED",
                message=f"Human-in-loop '{n.id}' is not implemented until Phase 3",
                node_id=n.id,
            ))

        if n.type not in ("start", "end") and not out_edges.get(n.id):
            warnings.append(ValidationIssue(
                level="warning", code="W_DEAD_END",
                message=f"Node '{n.id}' has no outgoing edges; the workflow may terminate here",
                node_id=n.id,
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
            message=f"Cycle detected through nodes: {' -> '.join(cycle)}",
        ))

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)
