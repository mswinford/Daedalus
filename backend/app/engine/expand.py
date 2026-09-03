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

Live refs: pool entries with track_latest are re-resolved from the registry at
run start (newest published version within the same major as the stamped one)
and swapped into a run-scoped copy; the saved workflow JSON is never mutated.
"""
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from schema.capability import semver_key
from schema.models import (
    AgentNodeConfig,
    AgentSkill,
    Edge,
    InvokeExitNodeConfig,
    InvokeNodeConfig,
    ModelConfig,
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
    """Resolve capability refs for a run and expand.

    Returns (expanded_workflow, invocations, pins, notices). Pins map capability
    name → resolved version; stored on the run so resume/restart re-expand
    identically. Live-tracked entries (track_latest) are re-resolved from the
    registry at run start — newest published version within the same major as
    the stamped one — and swapped into a run-scoped copy of the workflow; the
    saved JSON is never mutated. A tracked workflow-kind stamp swaps the whole
    graph (before invoke expansion, so invokes inside the swapped-in graph are
    expanded too); tracked agent nodes and skills get their inlined content
    re-projected into the pools by id; tracked pool entries are replaced in
    place. Every tracked entry gets a pin recorded (resolved or fallback), so
    resume fetches exactly what the run started with. Notices are human-readable
    lines for entries that resolved away from their stamp or fell back to the
    inlined copy. A workflow without invoke nodes and without tracked entries
    passes through untouched.
    """
    pins = dict(pins or {})
    notices: list[str] = []

    # Workflow-kind live ref — wholesale graph swap; must precede invoke
    # expansion so invokes inside the swapped-in graph get expanded too.
    if workflow.track_latest and workflow.source_capability and workflow.source_version:
        from app.capability_client import CapabilityClient

        client = client or CapabilityClient()
        workflow = _track_workflow(workflow, pins, client, notices)

    has_invoke = any(n.type == "invoke" for n in workflow.nodes)
    tracked = _tracked_entries(workflow)
    if not has_invoke and not tracked:
        return workflow, {}, pins, notices

    from app.capability_client import CapabilityClient

    client = client or CapabilityClient()
    invocations: dict[str, InvocationInfo] = {}

    # Invoke expansion runs before entry tracking so an explicit invoke version
    # wins over tracking when the same capability is used both ways; the
    # expanded result is already a deep copy that tracking may mutate.
    if has_invoke:
        def resolve(name: str, version: str) -> tuple[str, dict]:
            use = client.use(name, pins.get(name) or version)
            pins[name] = use["version"]
            return use["version"], use

        result = expand_workflow(workflow, resolve)
        workflow = result.workflow
        invocations = result.invocations
    elif tracked:
        workflow = workflow.model_copy(deep=True)

    if tracked:
        _apply_tracking(workflow, tracked, pins, client, notices)

    return workflow, invocations, pins, notices


def _tracked_entries(workflow: Workflow):
    """Tracking targets on a workflow, as (kind, where, skill_name, target):
    ('pool', pool_attr, None, entry) | ('agent', node_id, None, agent_config) |
    ('skill', node_id, skill_name, skill_entry)."""
    # Composites first, pools last: a pool entry that is both individually
    # tracked and projected by a composite swap keeps the individual resolution
    # (the explicit local opt-in wins over the inherited binding).
    out = []
    for node in workflow.nodes:
        if node.type != "agent" or not isinstance(node.config, AgentNodeConfig):
            continue
        cfg = node.config
        if cfg.track_latest and cfg.source_capability and cfg.source_version:
            out.append(("agent", node.id, None, cfg))
        for skill in cfg.skills:
            if (skill.name is not None and skill.track_latest
                    and skill.source_capability and skill.source_version):
                out.append(("skill", node.id, skill.name, skill))
    for attr in ("tools", "models", "prompts"):
        for entry in getattr(workflow, attr):
            if entry.track_latest and entry.source_capability and entry.source_version:
                out.append(("pool", attr, None, entry))
    return out


def _major(version: str) -> str:
    return version.split(".", 1)[0]


def _tracking_target(versions: list[dict], current: str) -> str | None:
    """Newest published version strictly newer than `current` within the same
    major, or None. Major jumps are skipped by design (a breaking change must
    go through the explicit upgrade flow)."""
    major = _major(current)
    candidates = [
        v["version"] for v in versions
        if v.get("stage") == "published" and _major(v["version"]) == major
    ]
    if not candidates:
        return None
    best = max(candidates, key=semver_key)
    return best if semver_key(best) > semver_key(current) else None


def _resolve_fresh(name: str, current: str, pinned: str | None, pins: dict,
                   client, notices: list[str], inline: bool = False) -> dict | None:
    """Resolve a tracked capability for a fresh run. Returns the registry use
    response, or None when falling back to the inlined copy — a pin + notice are
    recorded either way. A pre-existing pin (resume/restart, or a sibling invoke
    node) fetches exactly that version and fails loudly if it is missing."""
    from app.capability_client import CapabilityFetchError

    if pinned is not None:
        return client.use(name, pinned, inline=inline)
    try:
        versions = client.list_versions(name)
    except CapabilityFetchError as exc:
        pins[name] = current
        notices.append(f"{name}: registry unavailable ({exc}); using inlined {current}")
        return None
    target = _tracking_target(versions, current)
    if target is None:
        pins[name] = current
        newest = max(
            (v["version"] for v in versions if v.get("stage") == "published"),
            key=semver_key,
            default=None,
        )
        if newest is not None and _major(newest) != _major(current):
            notices.append(
                f"{name}: newest published {newest} is a major jump; keeping inlined {current}"
            )
        return None
    try:
        use = client.use(name, target, inline=inline)
    except CapabilityFetchError as exc:
        pins[name] = current
        notices.append(f"{name}: could not fetch {target} ({exc}); using inlined {current}")
        return None
    pins[name] = use["version"]
    notices.append(f"{name}: tracked {current} -> {use['version']}")
    return use


def _track_workflow(workflow: Workflow, pins, client, notices) -> Workflow:
    """Wholesale graph swap for a tracked workflow-kind stamp. Returns the
    swapped-in workflow (local id preserved — the run belongs to the saved
    workflow's identity) or the original on fallback."""
    name, current = workflow.source_capability, workflow.source_version
    use = _resolve_fresh(name, current, pins.get(name), pins, client, notices)
    if use is None:
        return workflow
    artifact = use.get("artifact")
    if isinstance(artifact, dict) and artifact.get("workflow_ref"):
        # Published as a workflow_ref — the graph lives in git, nothing to swap.
        notices.append(f"{name}: published artifact uses workflow_ref; keeping saved copy")
        return workflow
    fresh = Workflow.model_validate(artifact)
    fresh.id = workflow.id
    return fresh


def _apply_tracking(workflow, tracked, pins, client, notices) -> None:
    """Swap fresh registry content into the run-scoped workflow for tracked entries.

    `workflow` must be a run-scoped copy (never the saved one). Every tracked
    entry gets a pin recorded — resolved or fallback — so resume/restart fetches
    exactly what this run started with. Composite swaps (agent, skill) project
    into the pools by id; an individual tracking pass on the same pool entry
    runs afterwards and wins over projected content (explicit local opt-in).
    """
    for kind, where, skill_name, target in tracked:
        name, current = target.source_capability, target.source_version
        use = _resolve_fresh(
            name, current, pins.get(name), pins, client, notices,
            inline=(kind in ("skill", "agent")),
        )
        if use is None:
            continue
        if kind == "pool":
            pool = getattr(workflow, where)
            idx = next(i for i, e in enumerate(pool) if e.id == target.id)
            _swap_entry(pool, idx, use.get("artifact"))
        elif kind == "agent":
            node = next(n for n in workflow.nodes if n.id == where)
            _swap_agent(workflow, node, use)
        else:  # skill
            node = next(n for n in workflow.nodes if n.id == where)
            skills = node.config.skills
            idx = next(i for i, s in enumerate(skills) if s.name == skill_name)
            _swap_skill(workflow, skills, idx, use)


def _swap_entry(pool: list, idx: int, artifact: dict | None) -> None:
    """Replace pool[idx]'s content with the registry artifact, keeping id and
    provenance fields so references by id stay valid."""
    local = pool[idx]
    data = local.model_dump()
    for key, value in (artifact or {}).items():
        if key in ("id", "source_capability", "source_version", "track_latest"):
            continue
        data[key] = value
    pool[idx] = type(local).model_validate(data)


def _upsert_tool(pool: list[ToolDefinition], tool: dict, cap_name: str, version: str) -> str:
    """Upsert a tool definition into the pool by id — existing entries get their
    content replaced in place, new ones are appended stamped with the composite's
    origin. Returns the pool id."""
    for i, e in enumerate(pool):
        if e.id == tool.get("id"):
            _swap_entry(pool, i, tool)
            return e.id
    pool.append(ToolDefinition.model_validate(
        {**tool, "source_capability": cap_name, "source_version": version}))
    return tool["id"]


def _upsert_model(pool: list[ModelConfig], model: dict | None, cap_name: str,
                  version: str) -> str | None:
    """Upsert a model profile into the pool by id; returns the pool id (or None
    when the artifact carries no model)."""
    if not model or "id" not in model:
        return None
    for i, e in enumerate(pool):
        if e.id == model["id"]:
            _swap_entry(pool, i, model)
            return e.id
    pool.append(ModelConfig.model_validate(
        {**model, "source_capability": cap_name, "source_version": version}))
    return model["id"]


def _swap_skill(workflow: Workflow, skills: list[AgentSkill], idx: int, use: dict) -> None:
    """Project an inlined skill artifact ({name, prompt, tools}) onto a skill
    attachment: nested tools upserted into the pool by id, prompt + tool_ids
    replaced; name, stamps and track flag kept."""
    art = use.get("artifact") or {}
    entry = skills[idx]
    ids = [_upsert_tool(workflow.tools, t, entry.source_capability, use["version"])
           for t in art.get("tools", [])]
    data = entry.model_dump()
    data["prompt"] = art.get("prompt") or ""
    data["tool_ids"] = ids
    skills[idx] = AgentSkill.model_validate(data)


def _swap_agent(workflow: Workflow, node: Node, use: dict) -> None:
    """Project an inlined agent artifact ({model, prompt, tools, skills}) onto
    an agent node: model + tools upserted into the pools by id, skills matched
    by name (local-only skills kept, new ones added), system_prompt replaced.
    model_id is only re-pointed when it dangles (e.g. the user deleted the
    imported profile) — a deliberate user choice is never overridden."""
    art = use.get("artifact") or {}
    cap, version = node.config.source_capability, use["version"]
    cfg = node.config
    model_id = _upsert_model(workflow.models, art.get("model"), cap, version)
    cfg.tool_ids = [_upsert_tool(workflow.tools, t, cap, version)
                    for t in art.get("tools", [])]
    cfg.system_prompt = art.get("prompt") or ""
    cfg.skills = _project_skills(cfg.skills, art.get("skills", []), cap, version, workflow.tools)
    if model_id and cfg.model_id not in {m.id for m in workflow.models}:
        cfg.model_id = model_id


def _project_skills(local: list[AgentSkill], art_skills: list[dict], cap: str,
                    version: str, tool_pool: list[ToolDefinition]) -> list[AgentSkill]:
    """Match artifact skills onto local attachments by name; local-only skills
    are kept untouched, new ones appended stamped with the agent's origin."""
    out = []
    matched = set()
    for s in local:
        art = next((a for a in art_skills if a.get("name") == s.name), None)
        if art is None:
            out.append(s)
            continue
        matched.add(s.name)
        ids = [_upsert_tool(tool_pool, t, cap, version) for t in art.get("tools", [])]
        data = s.model_dump()
        data["prompt"] = art.get("prompt") or ""
        data["tool_ids"] = ids
        out.append(AgentSkill.model_validate(data))
    for a in art_skills:
        if a.get("name") not in matched:
            out.append(AgentSkill(
                name=a["name"], prompt=a.get("prompt") or "",
                tool_ids=[_upsert_tool(tool_pool, t, cap, version)
                          for t in a.get("tools", [])],
                source_capability=cap, source_version=version,
            ))
    return out


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
            if e.type == "error":
                # The entry gate (the invoke node itself) keeps a copy so its own
                # failures (input validation) are caught too; region failures
                # arrive re-keyed at the exit gate, which owns the original.
                edges.append(e.model_copy(deep=True))
            e.source_node_id = exit_id  # keep id/handle/type/condition; new source is the gate

        # ── parent-side catch: when the invoke node owns a type='error' edge in
        #    the parent graph, inner nodes without their own error edge get a
        #    synthetic one to the exit gate, so a region failure is re-keyed at
        #    the gate and routed to the parent's handler. Without a parent edge,
        #    failures propagate and fail the run at the failing (prefixed) node. ──
        if any(e.type == "error" for e in old_outgoing):
            has_error_edge = {e.source_node_id for e in new_edges if e.type == "error"}
            for sn in sub_nodes:
                if sn.type in ("start", "end") or id_map[sn.id] in has_error_edge:
                    continue
                new_edges.append(Edge(
                    id=_unique_id(f"{node.id}-err-{sn.id}"),
                    source_node_id=id_map[sn.id],
                    source_handle="error",
                    target_node_id=exit_id,
                    type="error",
                ))

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
