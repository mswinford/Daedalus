"""Build-time expansion of invoke nodes (C3 frame-swap design).

expand_workflow() rewrites a workflow containing 'invoke' nodes into an
equivalent flat workflow: each workflow-kind invocation is spliced in with
prefixed node ids and a synthetic invoke_exit gate, so the LangGraph runtime
sees one ordinary graph in the root namespace. Tool-kind invocations stay in
place; their execution info is recorded for the invoke handler.

Resolution is injected — (name, version) -> (resolved_version, use_response) —
so tests stub it and production wires the registry client. Resolved versions
are pinned per run by prepare_workflow_for_run(), so every rebuild (resume,
restart recovery) produces an identical structure that matches checkpoints.
"""
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from schema.models import (
    Edge,
    InvokeExitNodeConfig,
    InvokeNodeConfig,
    Node,
    NodePosition,
    ToolDefinition,
    Workflow,
)


class ExpansionError(Exception):
    """An invoke node could not be expanded (bad ref, cycle, depth, ...)."""


Resolver = Callable[[str, str], tuple[str, dict]]  # (name, version) -> (resolved_version, use_response)


@dataclass
class InvocationInfo:
    kind: str  # "tool" | "workflow"
    capability: str
    version: str  # resolved semver
    required_inputs: list[str] = field(default_factory=list)
    inner_node_ids: set[str] = field(default_factory=set)  # prefixed ids incl. exit gate (workflow kind)
    tool: ToolDefinition | None = None


@dataclass
class ExpansionResult:
    workflow: Workflow
    invocations: dict[str, InvocationInfo]


def expand_workflow(workflow: Workflow, resolve: Resolver, max_depth: int = 5) -> ExpansionResult:
    """Pure build-time transform. `resolve` must be deterministic for a run's pins."""
    nodes, edges, models, invocations = _expand_all(workflow, resolve, [], 0, max_depth)
    expanded = workflow.model_copy(deep=True)
    expanded.nodes = nodes
    expanded.edges = edges
    expanded.models = models
    return ExpansionResult(workflow=expanded, invocations=invocations)


def prepare_workflow_for_run(
    workflow: Workflow,
    pins: dict[str, str] | None = None,
    client=None,
):
    """Resolve invoke refs for a run and expand.

    Returns (expanded_workflow, invocations, pins). Pins map capability name →
    resolved version; stored on the run so resume/restart re-expand identically.
    A workflow without invoke nodes passes through untouched.
    """
    if not any(n.type == "invoke" for n in workflow.nodes):
        return workflow, {}, dict(pins or {})
    from app.capability_client import CapabilityClient

    pins = dict(pins or {})
    client = client or CapabilityClient()

    def resolve(name: str, version: str) -> tuple[str, dict]:
        use = client.use(name, pins.get(name) or version)
        pins[name] = use["version"]
        return use["version"], use

    result = expand_workflow(workflow, resolve)
    return result.workflow, result.invocations, pins


def _entry_node(sub: Workflow) -> Node | None:
    """The sub-workflow's entry node: its start node, else first non-end (builder fallback)."""
    for n in sub.nodes:
        if n.type == "start":
            return n
    for n in sub.nodes:
        if n.type != "end":
            return n
    return None


def _expand_all(
    workflow: Workflow, resolve: Resolver, path: list[str], depth: int, max_depth: int
):
    nodes = [n.model_copy(deep=True) for n in workflow.nodes]
    edges = [e.model_copy(deep=True) for e in workflow.edges]
    models = [m.model_copy(deep=True) for m in workflow.models]
    invocations: dict[str, InvocationInfo] = {}

    taken_ids = {n.id for n in nodes} | {e.id for e in edges}
    counter = 0

    def _unique_id(base: str) -> str:
        nonlocal counter
        candidate = base
        while candidate in taken_ids:
            counter += 1
            candidate = f"{base}-{counter}"
        taken_ids.add(candidate)
        return candidate

    for node in list(nodes):
        if node.type != "invoke":
            continue
        cfg: InvokeNodeConfig = node.config

        if cfg.capability in path:
            raise ExpansionError(
                f"capability cycle: {' -> '.join(path + [cfg.capability])}"
            )
        if depth > max_depth:
            raise ExpansionError(
                f"invoke nesting exceeds max depth {max_depth}: "
                f"{' -> '.join(path + [cfg.capability])}"
            )

        resolved, use = resolve(cfg.capability, cfg.version)
        kind = use.get("kind")
        artifact = use.get("artifact")

        if kind == "tool":
            invocations[node.id] = InvocationInfo(
                kind="tool",
                capability=cfg.capability,
                version=resolved,
                tool=ToolDefinition.model_validate(artifact),
            )
            continue

        if kind != "workflow":
            raise ExpansionError(
                f"{cfg.capability}@{resolved} has kind '{kind}'; only 'tool' and 'workflow' are invokable"
            )
        if isinstance(artifact, dict) and artifact.get("workflow_ref"):
            raise ExpansionError(
                f"{cfg.capability}@{resolved} is a workflow_ref artifact; "
                f"only embedded workflows can be invoked"
            )

        sub = Workflow.model_validate(artifact)
        sub_nodes, sub_edges, sub_models, sub_invocations = _expand_all(
            sub, resolve, path + [cfg.capability], depth + 1, max_depth
        )

        end_nodes = [n for n in sub_nodes if n.type == "end"]
        entry = _entry_node(sub)
        if entry is None or not end_nodes:
            raise ExpansionError(
                f"{cfg.capability}@{resolved} has no entry or end node; cannot be invoked"
            )

        prefix = f"{node.id}__"
        exit_id = f"{node.id}__exit"
        taken_ids.add(exit_id)

        # ── merge sub models (suffix-on-clash, mirrors the R1 inliner) ──
        model_map: dict[str, str] = {}
        taken_model_ids = {m.id for m in models}
        for sm in sub_models:
            sm = sm.model_copy(deep=True)
            orig_id = sm.id
            if sm.id in taken_model_ids:
                new_id, k = f"{sm.id}-{node.id}", 2
                while new_id in taken_model_ids:
                    new_id = f"{sm.id}-{node.id}-{k}"
                    k += 1
                sm.id = new_id
            model_map[orig_id] = sm.id
            taken_model_ids.add(sm.id)
            models.append(sm)

        # ── prefix inner nodes; rewrite refs that point inside the region ──
        id_map = {n.id: prefix + n.id for n in sub_nodes}
        new_nodes: list[Node] = []
        for sn in sub_nodes:
            sn = sn.model_copy(deep=True)
            sn.id = id_map[sn.id]
            if sn.type == "agent" and getattr(sn.config, "model_id", None) in model_map:
                sn.config.model_id = model_map[sn.config.model_id]
            elif sn.type == "invoke_exit":
                sn.config.invoke_id = id_map.get(sn.config.invoke_id, sn.config.invoke_id)
            new_nodes.append(sn)

        # ── prefix inner edges, mirroring the builder's start/end treatment:
        #    start/end nodes are not graph nodes (START/END stand in), so edges
        #    out of the sub's start node become splice-in edges from the invoke
        #    node, and edges into end nodes retarget at the exit gate. ──
        end_ids = {n.id for n in sub_nodes if n.type == "end"}

        def _retarget(tid: str) -> str:
            return exit_id if tid in end_ids else id_map[tid]

        new_edges = []
        for se in sub_edges:
            if entry.type == "start" and se.source_node_id == entry.id:
                continue  # replaced by splice-in below
            se = se.model_copy(deep=True)
            se.id = _unique_id(prefix + se.id)
            se.source_node_id = id_map[se.source_node_id]
            se.target_node_id = _retarget(se.target_node_id)
            new_edges.append(se)

        # ── splice: invoke → sub entry; exit gate → invoke's old targets ──
        old_outgoing = [e for e in edges if e.source_node_id == node.id]
        edges[:] = [e for e in edges if e.source_node_id != node.id]

        if entry.type == "start":
            entry_edges = [e for e in sub_edges if e.source_node_id == entry.id]
            if not entry_edges:
                raise ExpansionError(
                    f"{cfg.capability}@{resolved} has no outgoing edges from its start node; cannot be invoked"
                )
            splice_in = [
                Edge(
                    id=_unique_id(f"{node.id}-entry-{i}"),
                    source_node_id=node.id,
                    source_handle=e.source_handle,
                    target_node_id=_retarget(e.target_node_id),
                )
                for i, e in enumerate(entry_edges)
            ]
        else:
            splice_in = [
                Edge(
                    id=_unique_id(f"{node.id}-entry"),
                    source_node_id=node.id,
                    source_handle="default",
                    target_node_id=id_map[entry.id],
                )
            ]

        for e in old_outgoing:
            e.source_node_id = exit_id  # keep id/handle/type/condition; new source is the gate

        edges.extend(splice_in)
        edges.extend(new_edges)
        edges.extend(old_outgoing)

        exit_node = Node(
            id=exit_id,
            type="invoke_exit",
            config=InvokeExitNodeConfig(
                invoke_id=node.id,
                output_field=cfg.output_field,
                set_output=cfg.set_output,
            ),
            position=NodePosition(x=node.position.x + 400, y=node.position.y),
        )
        nodes.extend(new_nodes)
        nodes.append(exit_node)

        # ── required inputs: declared state_schema ∪ start input_fields ──
        required = [f.name for f in sub.state_schema.fields if f.required] if sub.state_schema else []
        if entry.type == "start" and getattr(entry.config, "input_fields", None):
            for name in entry.config.input_fields:
                if name not in required:
                    required.append(name)

        invocations[node.id] = InvocationInfo(
            kind="workflow",
            capability=cfg.capability,
            version=resolved,
            required_inputs=required,
            inner_node_ids={id_map[n.id] for n in sub_nodes} | {exit_id},
        )

        # Nested invocations were keyed by unprefixed ids; re-key through the prefix.
        for inner_id, info in sub_invocations.items():
            new_key = id_map[inner_id]
            invocations[new_key] = replace(
                info, inner_node_ids={prefix + i for i in info.inner_node_ids}
            )

    return nodes, edges, models, invocations
