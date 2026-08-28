"""End-to-end tests for conditional routing through the LangGraph engine."""
import pytest

from app.engine.conditions import ConditionError
from app.engine.runner import run_workflow_sync
from schema.models import ConditionalNodeConfig, Edge, Node, Workflow


def _cond_wf(default_branch):
    """Conditional node routing on $.data.score (95 -> pass, 40 -> fail)."""
    return Workflow(
        id="wf-cond", name="cond",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="cond", type="conditional", config={
                "conditions": [
                    {"type": "json_path", "expression": "$.data.score >= 80"},
                    {"type": "json_path", "expression": "$.data.score < 80"},
                ],
                "default_branch": default_branch,
            }),
            Node(id="pass_node", type="transform",
                 config={"mode": "template", "template": "PASSED", "output_field": "output"}),
            Node(id="fail_node", type="transform",
                 config={"mode": "template", "template": "FAILED", "output_field": "output"}),
            Node(id="end1", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="cond"),
            Edge(id="b0", source_node_id="cond", source_handle="high", target_node_id="pass_node"),
            Edge(id="b1", source_node_id="cond", source_handle="low", target_node_id="fail_node"),
            Edge(id="p", source_node_id="pass_node", source_handle="default", target_node_id="end1"),
            Edge(id="f", source_node_id="fail_node", source_handle="default", target_node_id="end1"),
        ],
    )


def test_conditional_first_branch_matches():
    out = run_workflow_sync(_cond_wf(None), {"score": 95})
    assert out["output"] == "PASSED"


def test_conditional_second_branch_matches():
    out = run_workflow_sync(_cond_wf(None), {"score": 40})
    assert out["output"] == "FAILED"


def test_conditional_falls_back_to_default_branch():
    # Only condition is score > 100 (never true for 50); default_branch routes to fail.
    wf = _cond_wf("low")
    wf.nodes[1].config = ConditionalNodeConfig(
        conditions=[{"type": "json_path", "expression": "$.data.score > 100"}],
        default_branch="low",
    )
    out = run_workflow_sync(wf, {"score": 50})
    assert out["output"] == "FAILED"


def test_edge_level_regex_condition():
    wf = Workflow(
        id="wf-re", name="re",
        nodes=[
            Node(id="n1", type="custom_function",
                 config={"code": 'result["output"] = "error: disk full"'}),
            Node(id="ok_end", type="transform",
                 config={"mode": "template", "template": "OK", "output_field": "output"}),
            Node(id="err_end", type="transform",
                 config={"mode": "template", "template": "ERR", "output_field": "output"}),
            Node(id="end1", type="end", config={}),
        ],
        edges=[
            Edge(id="e_err", source_node_id="n1", source_handle="err", target_node_id="err_end",
                 type="conditional", condition={"type": "regex", "expression": "error"}),
            Edge(id="e_def", source_node_id="n1", source_handle="default", target_node_id="ok_end"),
            Edge(id="o", source_node_id="ok_end", source_handle="default", target_node_id="end1"),
            Edge(id="r", source_node_id="err_end", source_handle="default", target_node_id="end1"),
        ],
    )
    out = run_workflow_sync(wf, {})
    assert out["output"] == "ERR"


def test_no_match_and_no_default_raises():
    wf = _cond_wf(None)
    wf.nodes[1].config = ConditionalNodeConfig(
        conditions=[{"type": "json_path", "expression": "$.data.score > 100"}],
        default_branch=None,
    )
    # Drop the low branch so no fallback edge exists.
    wf.edges = [e for e in wf.edges if e.id != "b1" and e.id != "f"]
    with pytest.raises(ConditionError):
        run_workflow_sync(wf, {"score": 50})
