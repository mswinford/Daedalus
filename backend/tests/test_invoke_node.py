"""Tests for invoke nodes: build-time expansion, frame-swap gates, tool kind,
pins/resume semantics, and validation rules (C3 design)."""
import pytest

from app.engine.expand import ExpansionError, expand_workflow, prepare_workflow_for_run
from app.engine.runner import resume_workflow, run_workflow_sync
from app.engine.validation import validate_workflow
from schema.models import (
    AgentNodeConfig,
    Edge,
    FieldMapping,
    HumanInputField,
    HumanInLoopNodeConfig,
    InvokeExitNodeConfig,
    InvokeNodeConfig,
    JsonSchemaParam,
    ModelConfig,
    ModelProvider,
    Node,
    StateField,
    StateFieldType,
    StateSchema,
    ToolDefinition,
    ToolImplementation,
    ToolImplementationType,
    Workflow,
)


# ─── fixtures ────────────────────────────────────────────────────────────────

def _sub_wf(required: bool = True, hil: bool = False, output: str | None = None) -> Workflow:
    """start → [cf y=x+1] → [hil] → [transform output] → end."""
    nodes = [Node(id="start", type="start", config={})]
    edges = []
    prev = "start"
    if not hil and output is None:
        nodes.append(Node(
            id="cf", type="custom_function",
            config={"code": 'result["y"] = state["data"]["x"] + 1', "output_fields": ["y"]},
        ))
        edges.append(Edge(id="e1", source_node_id=prev, source_handle="default", target_node_id="cf"))
        prev = "cf"
    if hil:
        nodes.append(Node(
            id="hil", type="human_in_loop",
            config=HumanInLoopNodeConfig(
                input_fields=[HumanInputField(name="note", label="Note", type="text")],
                output_fields=["note"],
            ),
        ))
        edges.append(Edge(id="e-hil", source_node_id=prev, source_handle="default", target_node_id="hil"))
        prev = "hil"
    if output is not None:
        nodes.append(Node(
            id="t", type="transform",
            config={"mode": "template", "template": output, "output_field": "output"},
        ))
        edges.append(Edge(id="e-t", source_node_id=prev, source_handle="default", target_node_id="t"))
        prev = "t"
    nodes.append(Node(id="end", type="end", config={}))
    edges.append(Edge(id="e-end", source_node_id=prev, source_handle="default", target_node_id="end"))
    return Workflow(
        id="wf-sub", name="sub",
        state_schema=StateSchema(fields=[StateField(name="x", type=StateFieldType.NUMBER, required=required)])
        if required else None,
        nodes=nodes, edges=edges,
    )


def _parent_wf(mapping=None, output_field: str = "sub", set_output: bool = False,
               capability: str = "acme/sub") -> Workflow:
    return Workflow(
        id="wf-parent", name="parent",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="inv", type="invoke", config=InvokeNodeConfig(
                capability=capability, version="latest",
                input_mapping=mapping if mapping is not None else [
                    FieldMapping(source="data.x", target="x"),
                ],
                output_field=output_field, set_output=set_output,
            )),
            Node(id="t", type="transform",
                 config={"mode": "template", "template": "{{data.x}}|{{data.sub.y}}",
                         "output_field": "output"}),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="inv"),
            Edge(id="i", source_node_id="inv", source_handle="default", target_node_id="t"),
            Edge(id="e", source_node_id="t", source_handle="default", target_node_id="end"),
        ],
    )


def _resolve(sub: Workflow | None = None, tool: ToolDefinition | None = None):
    def resolve(name: str, version: str) -> tuple[str, dict]:
        if tool is not None and name == "acme/tool":
            return "1.0.0", {"kind": "tool", "artifact": tool.model_dump(), "version": "1.0.0"}
        return "1.0.0", {"kind": "workflow", "artifact": (sub or _sub_wf()).model_dump(), "version": "1.0.0"}
    return resolve


def _echo_tool() -> ToolDefinition:
    return ToolDefinition(
        id="t-echo", name="echo", description="Echoes its message argument",
        parameters={"message": JsonSchemaParam(type=StateFieldType.STRING, required=True)},
        implementation=ToolImplementation(type=ToolImplementationType.BUILTIN, config={"function": "echo"}),
    )


# ─── expand(): structure ─────────────────────────────────────────────────────

def test_expand_splices_region_with_prefix_ids():
    expanded = expand_workflow(_parent_wf(), _resolve()).workflow
    ids = {n.id for n in expanded.nodes}
    assert {"inv", "inv__start", "inv__cf", "inv__end", "inv__exit"} <= ids
    by_src = {}
    for e in expanded.edges:
        by_src.setdefault(e.source_node_id, []).append(e.target_node_id)
    assert by_src["start"] == ["inv"]
    assert by_src["inv"] == ["inv__cf"]             # entry gate → sub's first real node
    assert by_src["inv__cf"] == ["inv__exit"]       # sub's end-target retargeted at the gate
    assert by_src["inv__exit"] == ["t"]             # old outgoing re-sourced


def test_expand_records_invocation_info():
    result = expand_workflow(_parent_wf(), _resolve())
    info = result.invocations["inv"]
    assert info.kind == "workflow"
    assert info.capability == "acme/sub"
    assert info.version == "1.0.0"
    assert info.required_inputs == ["x"]
    assert "inv__cf" in info.inner_node_ids and "inv__exit" in info.inner_node_ids


def test_expand_tool_kind_stays_in_place():
    wf = _parent_wf(capability="acme/tool")
    result = expand_workflow(wf, _resolve(tool=_echo_tool()))
    assert {n.id for n in result.workflow.nodes} == {n.id for n in wf.nodes}
    info = result.invocations["inv"]
    assert info.kind == "tool"
    assert info.tool.name == "echo"


def test_expand_merges_models_with_suffix_on_clash():
    parent = _parent_wf()
    m = ModelConfig(id="m1", name="M", provider=ModelProvider.OPENAI_COMPATIBLE,
                    model="x", base_url="http://localhost")
    parent.models = [m]
    sub = _sub_wf(required=False)
    sub.models = [m.model_copy(deep=True)]
    sub.nodes.append(Node(id="agent", type="agent",
                          config=AgentNodeConfig(model_id="m1", system_prompt="hi")))
    expanded = expand_workflow(parent, _resolve(sub=sub)).workflow
    model_ids = {mm.id for mm in expanded.models}
    assert model_ids == {"m1", "m1-inv"}
    agent = next(n for n in expanded.nodes if n.type == "agent")
    assert agent.config.model_id == "m1-inv"


def test_expand_cycle_raises():
    wf = _parent_wf()
    with pytest.raises(ExpansionError, match="cycle"):
        expand_workflow(wf, lambda name, version: ("1.0.0", {
            "kind": "workflow", "artifact": wf.model_dump(), "version": "1.0.0"}))


def test_expand_depth_guard_raises():
    # parent → acme/a → acme/b → acme/c (plain sub): distinct names so the
    # cycle guard stays quiet and only the depth guard can fire.
    leaf = _sub_wf(required=False)

    def resolve(name, version):
        if name == "acme/c":
            return "1.0.0", {"kind": "workflow", "artifact": leaf.model_dump(), "version": "1.0.0"}
        nxt = {"acme/a": "acme/b", "acme/b": "acme/c"}[name]
        return "1.0.0", {"kind": "workflow", "artifact": _invoke_wf(capability=nxt).model_dump(),
                         "version": "1.0.0"}

    with pytest.raises(ExpansionError, match="max depth"):
        expand_workflow(_parent_wf(capability="acme/a"), resolve, max_depth=1)


def test_expand_non_invokable_kind_raises():
    with pytest.raises(ExpansionError, match="only 'tool' and 'workflow'"):
        expand_workflow(_parent_wf(), lambda name, version: ("1.0.0", {
            "kind": "agent", "artifact": {}, "version": "1.0.0"}))


def test_prepare_passthrough_without_invoke_nodes():
    wf = _sub_wf(required=False)
    expanded, invocations, pins = prepare_workflow_for_run(wf)
    assert expanded is wf and invocations == {} and pins == {}


# ─── execution: workflow kind frame swap ─────────────────────────────────────

def test_workflow_kind_frame_swap():
    result = expand_workflow(_parent_wf(), _resolve())
    out = run_workflow_sync(result.workflow, {"x": 5}, invocations=result.invocations)
    assert out["data"]["sub"] == {"x": 5, "y": 6}   # sub's final data in output_field
    assert out["data"]["x"] == 5                    # parent data untouched
    assert out["output"] == "5|6"                   # parent template sees both
    assert out["node_outputs"]["inv"]["data"] == {"x": 5, "y": 6}


def test_workflow_kind_missing_required_input_fails():
    wf = _parent_wf(mapping=[])  # sub declares required 'x', nothing mapped
    result = expand_workflow(wf, _resolve())
    with pytest.raises(ValueError, match="missing required input"):
        run_workflow_sync(result.workflow, {"x": 5}, invocations=result.invocations)


def test_set_output_copies_sub_output():
    sub = _sub_wf(required=False, output="SUBOUT")

    def make(set_output: bool) -> Workflow:
        # No trailing transform: a later parent node writing `output` would
        # legitimately override the copied sub output.
        return Workflow(
            id="wf-parent", name="parent",
            nodes=[
                Node(id="start", type="start", config={}),
                Node(id="inv", type="invoke", config=InvokeNodeConfig(
                    capability="acme/sub", version="latest",
                    input_mapping=[], set_output=set_output,
                )),
                Node(id="end", type="end", config={}),
            ],
            edges=[
                Edge(id="s", source_node_id="start", source_handle="default", target_node_id="inv"),
                Edge(id="e", source_node_id="inv", source_handle="default", target_node_id="end"),
            ],
        )

    result = expand_workflow(make(True), _resolve(sub=sub))
    out = run_workflow_sync(result.workflow, {}, invocations=result.invocations)
    assert out["output"] == "SUBOUT"

    result2 = expand_workflow(make(False), _resolve(sub=sub))
    out2 = run_workflow_sync(result2.workflow, {}, invocations=result2.invocations)
    assert out2["output"] == ""  # parent output restored from stash


def test_nested_invocations_compose():
    inner = _sub_wf(required=True)
    mid = Workflow(
        id="wf-mid", name="mid",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="inv", type="invoke", config=InvokeNodeConfig(
                capability="acme/inner", version="latest",
                input_mapping=[FieldMapping(source="data.x", target="x")],
                output_field="inner_out",
            )),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="inv"),
            Edge(id="e", source_node_id="inv", source_handle="default", target_node_id="end"),
        ],
    )

    def resolve(name, version):
        if name == "acme/inner":
            return "1.0.0", {"kind": "workflow", "artifact": inner.model_dump(), "version": "1.0.0"}
        return "2.0.0", {"kind": "workflow", "artifact": mid.model_dump(), "version": "2.0.0"}

    parent = _parent_wf(output_field="mid_out")
    result = expand_workflow(parent, resolve)
    out = run_workflow_sync(result.workflow, {"x": 1}, invocations=result.invocations)
    assert out["data"]["mid_out"]["inner_out"] == {"x": 1, "y": 2}


# ─── execution: tool kind ────────────────────────────────────────────────────

def test_tool_kind_executes_in_place():
    wf = _parent_wf(mapping=[FieldMapping(source="data.msg", target="message")],
                    output_field="result", capability="acme/tool")
    result = expand_workflow(wf, _resolve(tool=_echo_tool()))
    out = run_workflow_sync(result.workflow, {"msg": "hello"}, invocations=result.invocations)
    assert out["data"]["result"] == "hello"  # default output_field
    assert out["node_outputs"]["inv"]["result"] == "hello"


# ─── HIL inside an expanded region ───────────────────────────────────────────

def test_hil_in_region_pauses_and_resumes():
    sub = _sub_wf(required=True, hil=True)
    result = expand_workflow(_parent_wf(), _resolve(sub=sub))
    paused = run_workflow_sync(result.workflow, {"x": 1}, thread_id="t-hil",
                               invocations=result.invocations)
    assert paused.get("paused") is True

    out = resume_workflow(result.workflow, "t-hil", {"note": "hi"},
                          invocations=result.invocations)
    assert out["data"]["sub"] == {"x": 1, "note": "hi"}
    assert out["data"]["x"] == 1  # parent frame restored intact


# ─── parent-side sub-error catch (Phase 4) ───────────────────────────────────

def _failing_sub() -> Workflow:
    wf = _sub_wf(required=True)
    for n in wf.nodes:
        if n.id == "cf":
            n.config = {"code": 'raise ValueError("boom")', "output_fields": ["y"]}
    return wf


def _bare_parent(output_field: str = "sub") -> Workflow:
    """start → inv → end (no trailing transform)."""
    return Workflow(
        id="wf-parent", name="parent",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="inv", type="invoke", config=InvokeNodeConfig(
                capability="acme/sub", version="latest",
                input_mapping=[FieldMapping(source="data.x", target="x")],
                output_field=output_field,
            )),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="inv"),
            Edge(id="e", source_node_id="inv", source_handle="default", target_node_id="end"),
        ],
    )


def _parent_wf_catch(mapping=None) -> Workflow:
    """_parent_wf plus a catch node wired to the invoke's error handle."""
    wf = _parent_wf(mapping=mapping)
    wf.nodes[1].error_handling = True
    wf.nodes.append(Node(id="catch", type="custom_function",
                         config={"code": 'result["caught"] = True', "output_fields": ["caught"]}))
    wf.edges.append(Edge(id="ie", source_node_id="inv", source_handle="error",
                         target_node_id="catch", type="error"))
    wf.edges.append(Edge(id="c", source_node_id="catch", source_handle="default",
                         target_node_id="end"))
    return wf


def test_inner_failure_routes_to_parent_catch():
    result = expand_workflow(_parent_wf_catch(), _resolve(sub=_failing_sub()))
    out = run_workflow_sync(result.workflow, {"x": 5}, invocations=result.invocations)
    assert out["data"]["caught"] is True          # parent's error handler ran
    assert out["data"]["x"] == 5                  # parent frame restored intact
    assert "sub" not in out["data"]               # no result was produced
    assert "t" not in out["node_outputs"]         # default path never ran


def test_inner_failure_without_parent_edge_fails_run():
    result = expand_workflow(_parent_wf(), _resolve(sub=_failing_sub()))
    with pytest.raises(Exception, match="boom"):
        run_workflow_sync(result.workflow, {"x": 5}, invocations=result.invocations)


def test_authored_internal_error_edge_contains_failure():
    sub = _failing_sub()
    for n in sub.nodes:
        if n.id == "cf":
            n.error_handling = True
    sub.nodes.append(Node(id="h", type="custom_function",
                          config={"code": 'result["handled"] = True', "output_fields": ["handled"]}))
    sub.edges.append(Edge(id="e-err", source_node_id="cf", source_handle="error",
                          target_node_id="h", type="error"))
    sub.edges.append(Edge(id="e-h", source_node_id="h", source_handle="default",
                          target_node_id="end"))

    result = expand_workflow(_bare_parent(), _resolve(sub=sub))
    out = run_workflow_sync(result.workflow, {"x": 5}, invocations=result.invocations)
    assert out["data"]["sub"] == {"x": 5, "handled": True}  # region completed normally


def test_entry_gate_validation_failure_caught_by_parent():
    result = expand_workflow(_parent_wf_catch(mapping=[]), _resolve())
    out = run_workflow_sync(result.workflow, {"x": 5}, invocations=result.invocations)
    assert out["data"]["caught"] is True
    assert out["data"]["x"] == 5


def test_nested_region_contained_error_completes_outer():
    inner = _failing_sub()
    mid = Workflow(
        id="wf-mid", name="mid",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="inv", type="invoke", error_handling=True, config=InvokeNodeConfig(
                capability="acme/inner", version="latest",
                input_mapping=[FieldMapping(source="data.x", target="x")],
                output_field="inner_out",
            )),
            Node(id="h", type="custom_function",
                 config={"code": 'result["handled"] = True', "output_fields": ["handled"]}),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="inv"),
            Edge(id="d", source_node_id="inv", source_handle="default", target_node_id="end"),
            Edge(id="e", source_node_id="inv", source_handle="error", target_node_id="h", type="error"),
            Edge(id="h", source_node_id="h", source_handle="default", target_node_id="end"),
        ],
    )

    def resolve(name, version):
        if name == "acme/inner":
            return "1.0.0", {"kind": "workflow", "artifact": inner.model_dump(), "version": "1.0.0"}
        return "2.0.0", {"kind": "workflow", "artifact": mid.model_dump(), "version": "2.0.0"}

    parent = _bare_parent(output_field="mid_out")
    result = expand_workflow(parent, resolve)
    out = run_workflow_sync(result.workflow, {"x": 1}, invocations=result.invocations)
    assert out["data"]["mid_out"] == {"x": 1, "handled": True}


def test_nested_uncaught_failure_fails_at_innermost():
    inner = _failing_sub()
    mid = Workflow(
        id="wf-mid", name="mid",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="inv", type="invoke", config=InvokeNodeConfig(
                capability="acme/inner", version="latest",
                input_mapping=[FieldMapping(source="data.x", target="x")],
                output_field="inner_out",
            )),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="inv"),
            Edge(id="e", source_node_id="inv", source_handle="default", target_node_id="end"),
        ],
    )

    def resolve(name, version):
        if name == "acme/inner":
            return "1.0.0", {"kind": "workflow", "artifact": inner.model_dump(), "version": "1.0.0"}
        return "2.0.0", {"kind": "workflow", "artifact": mid.model_dump(), "version": "2.0.0"}

    parent = _bare_parent(output_field="mid_out")
    result = expand_workflow(parent, resolve)
    with pytest.raises(Exception, match="boom"):
        run_workflow_sync(result.workflow, {"x": 1}, invocations=result.invocations)


# ─── pins ────────────────────────────────────────────────────────────────────

def test_prepare_pins_resolved_versions():
    wf = _parent_wf()
    class StubClient:
        def use(self, name, version):
            assert version == "latest"
            return {"version": "3.2.1", "kind": "workflow",
                    "artifact": _sub_wf().model_dump()}
    _, _, pins = prepare_workflow_for_run(wf, client=StubClient())
    assert pins == {"acme/sub": "3.2.1"}

    wf2 = _parent_wf()
    wf2.nodes[1].config.version = "1.0.0"
    class PinnedClient:
        def use(self, name, version):
            # the registry serves exactly the (pinned) version that was requested
            return {"version": version, "kind": "workflow",
                    "artifact": _sub_wf().model_dump()}
    _, _, pins2 = prepare_workflow_for_run(wf2, pins={"acme/sub": "9.9.9"}, client=PinnedClient())
    # stored pin wins over the node's requested version
    assert pins2 == {"acme/sub": "9.9.9"}


# ─── validation ──────────────────────────────────────────────────────────────

def _invoke_wf(**cfg_over):
    cfg = dict(capability="acme/sub", version="latest")
    cfg.update(cfg_over)
    return Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="inv", type="invoke", config=InvokeNodeConfig(**cfg)),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="inv"),
            Edge(id="e", source_node_id="inv", source_handle="default", target_node_id="end"),
        ],
    )


def test_validation_bad_capability_format():
    result = validate_workflow(_invoke_wf(capability="no-slash"))
    assert any(i.code == "E_INVOKE_BAD_CAPABILITY" for i in result.issues)


def test_validation_bad_version():
    result = validate_workflow(_invoke_wf(version="1.0"))
    assert any(i.code == "E_INVOKE_BAD_VERSION" for i in result.issues)


def test_validation_semver_and_latest_accepted():
    assert validate_workflow(_invoke_wf()).valid
    assert validate_workflow(_invoke_wf(version="1.2.3-rc.1")).valid


def test_validation_mapping_without_source_or_transform():
    wf = _invoke_wf(input_mapping=[FieldMapping(source="", target="x")])
    result = validate_workflow(wf)
    assert any(i.code == "E_INVOKE_MAPPING_EMPTY" for i in result.issues)


def test_validation_duplicate_mapping_target_is_warning():
    wf = _invoke_wf(input_mapping=[
        FieldMapping(source="data.a", target="x"),
        FieldMapping(source="data.b", target="x"),
    ])
    result = validate_workflow(wf)
    assert any(i.code == "W_INVOKE_DUPLICATE_MAPPING" and i.level == "warning" for i in result.issues)


def test_validation_saved_exit_gate_is_error():
    wf = _invoke_wf()
    wf.nodes.append(Node(id="gate", type="invoke_exit",
                         config=InvokeExitNodeConfig(invoke_id="inv")))
    result = validate_workflow(wf)
    assert any(i.code == "E_INVOKE_EXIT_SAVED" for i in result.issues)
