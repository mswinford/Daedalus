"""Tests for bounded loops: cycles are legal and capped by the step limit."""
import pytest

from app.engine.runner import IterationLimitExceeded, MAX_SUPER_STEPS, resume_workflow, run_workflow_sync
from schema.models import Edge, HumanInLoopNodeConfig, Node, Workflow


def _loop_wf(exit_when_ge: int) -> Workflow:
    """start → cf (increment data.n) → cond; n < exit_when_ge loops back."""
    return Workflow(
        id="wf-loop", name="loop",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="cf", type="custom_function",
                 config={"code": 'result["n"] = state.get("data", {}).get("n", 0) + 1',
                         "output_fields": ["n"]}),
            Node(id="cond", type="conditional", config={
                "conditions": [{"type": "json_path",
                                "expression": f"$.data.n < {exit_when_ge}"}],
                "default_branch": "done",
            }),
            Node(id="done", type="transform",
                 config={"mode": "template", "template": "DONE", "output_field": "output"}),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="e1", source_node_id="start", source_handle="default", target_node_id="cf"),
            Edge(id="e2", source_node_id="cf", source_handle="default", target_node_id="cond"),
            Edge(id="e3", source_node_id="cond", source_handle="loop", target_node_id="cf"),
            Edge(id="e4", source_node_id="cond", source_handle="done", target_node_id="done"),
            Edge(id="e5", source_node_id="done", source_handle="default", target_node_id="end"),
        ],
    )


def test_finite_loop_completes():
    out = run_workflow_sync(_loop_wf(3), {})
    assert out["data"]["n"] == 3
    assert out["output"] == "DONE"


def test_infinite_loop_hits_step_cap():
    with pytest.raises(IterationLimitExceeded):
        run_workflow_sync(_loop_wf(10**9), {}, thread_id="t-cap")


def test_hil_pause_resume_mid_loop_keeps_counting():
    """A loop that pauses at human_in_loop each pass resumes and finishes."""
    wf = _loop_wf(3)
    hil = Node(id="hil", type="human_in_loop",
               config=HumanInLoopNodeConfig(fields=[]))
    wf.nodes.insert(2, hil)
    wf.edges[1] = Edge(id="e2", source_node_id="cf", source_handle="default", target_node_id="hil")
    wf.edges.insert(2, Edge(id="e6", source_node_id="hil", source_handle="default", target_node_id="cond"))

    paused = run_workflow_sync(wf, {}, thread_id="t-loop-hil")
    assert paused.get("paused") is True

    # The HIL sits inside the loop, so it pauses once per pass until the
    # exit condition holds — resume in a loop.
    out = resume_workflow(wf, "t-loop-hil", {"ok": True})
    resumes = 1
    while out.get("paused"):
        out = resume_workflow(wf, "t-loop-hil", {"ok": True})
        resumes += 1
    assert resumes == 3  # n=1, n=2, n=3 each hit the HIL before cond exits

    assert out["data"]["n"] == 3
    assert out["output"] == "DONE"


def test_cap_is_a_sane_constant():
    assert MAX_SUPER_STEPS >= 100
