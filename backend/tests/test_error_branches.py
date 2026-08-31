"""Tests for error-branch routing (type='error' edges) and their validation rules."""
import pytest

from app.engine.runner import run_workflow_sync
from app.engine.validation import validate_workflow
from schema.models import Edge, HumanInputField, HumanInLoopNodeConfig, Node, Workflow


def _error_wf(failing: bool = True, with_error_edge: bool = True, error_handling: bool = True):
    """start -> cf (raises when failing) -> ok_end | err_end -> end."""
    nodes = [
        Node(id="start", type="start", config={}),
        Node(
            id="cf",
            type="custom_function",
            config={"code": 'raise ValueError("boom")' if failing else 'result["output"] = "OK"'},
            error_handling=error_handling,
        ),
        Node(id="ok_end", type="transform",
             config={"mode": "template", "template": "OK", "output_field": "output"}),
        Node(id="err_end", type="transform",
             config={"mode": "template", "template": "ERR", "output_field": "output"}),
        Node(id="end1", type="end", config={}),
    ]
    edges = [
        Edge(id="s", source_node_id="start", source_handle="default", target_node_id="cf"),
        Edge(id="o", source_node_id="ok_end", source_handle="default", target_node_id="end1"),
        Edge(id="r", source_node_id="err_end", source_handle="default", target_node_id="end1"),
    ]
    if with_error_edge:
        edges += [
            Edge(id="d", source_node_id="cf", source_handle="default", target_node_id="ok_end"),
            Edge(id="e", source_node_id="cf", source_handle="error", target_node_id="err_end", type="error"),
        ]
    else:
        edges.append(Edge(id="d", source_node_id="cf", source_handle="default", target_node_id="ok_end"))
    return Workflow(id="wf-err", name="err", nodes=nodes, edges=edges)


def test_failure_routes_to_error_edge():
    out = run_workflow_sync(_error_wf(failing=True), {})
    assert out["output"] == "ERR"


def test_success_ignores_error_edge():
    out = run_workflow_sync(_error_wf(failing=False), {})
    assert out["output"] == "OK"


def test_failure_without_error_edge_fails_run():
    with pytest.raises(Exception, match="boom"):
        run_workflow_sync(_error_wf(failing=True, with_error_edge=False), {})


def test_graph_interrupt_not_treated_as_failure():
    """A human_in_loop pause must not be routed down the error edge."""
    wf = _error_wf(failing=False)
    # Config object (not dict) — a minimal dict mis-parses as EndNodeConfig (smart-union).
    wf.nodes.insert(2, Node(id="hil", type="human_in_loop", error_handling=True,
                            config=HumanInLoopNodeConfig(
                                input_fields=[HumanInputField(name="note", label="note", type="text")])))
    # start -> hil -> cf: rewire edges
    wf.edges = [
        Edge(id="s", source_node_id="start", source_handle="default", target_node_id="hil"),
        Edge(id="h", source_node_id="hil", source_handle="default", target_node_id="cf"),
        Edge(id="e_hil", source_node_id="hil", source_handle="error", target_node_id="err_end", type="error"),
        Edge(id="d", source_node_id="cf", source_handle="default", target_node_id="ok_end"),
        Edge(id="e", source_node_id="cf", source_handle="error", target_node_id="err_end", type="error"),
        Edge(id="o", source_node_id="ok_end", source_handle="default", target_node_id="end1"),
        Edge(id="r", source_node_id="err_end", source_handle="default", target_node_id="end1"),
    ]
    out = run_workflow_sync(wf, {})
    assert out.get("paused") is True


def test_error_edge_multiple_per_source_is_error():
    wf = _error_wf()
    wf.edges.append(Edge(id="e2", source_node_id="cf", source_handle="error",
                         target_node_id="ok_end", type="error"))
    result = validate_workflow(wf)
    assert not result.valid
    assert any(i.code == "E_MULTIPLE_ERROR_EDGES" for i in result.issues)


def test_error_edge_without_optin_is_warning():
    wf = _error_wf(error_handling=False)
    result = validate_workflow(wf)
    assert any(i.code == "W_ERROR_EDGE_NO_OPTIN" and i.level == "warning" for i in result.issues)


def test_error_edge_from_start_is_error():
    wf = Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="end1", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="end1"),
            Edge(id="e", source_node_id="start", source_handle="error", target_node_id="end1", type="error"),
        ],
    )
    result = validate_workflow(wf)
    assert any(i.code == "E_ERROR_EDGE_FROM_START" for i in result.issues)


def test_error_edge_without_fallback_is_error():
    wf = _error_wf()
    # Drop the default (success) edge: a failure would have nowhere to fall through.
    wf.edges = [e for e in wf.edges if not (e.source_node_id == "cf" and e.type != "error")]
    result = validate_workflow(wf)
    assert any(i.code == "E_ERROR_EDGE_NO_FALLBACK" for i in result.issues)


def test_valid_error_branch_config_has_no_issues():
    result = validate_workflow(_error_wf())
    assert result.valid, [i.message for i in result.issues]
