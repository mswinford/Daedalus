"""Tests for static workflow validation (engine + API endpoint)."""
import json

from app.engine.validation import validate_workflow
from schema.models import ConditionalNodeConfig, Edge, ModelConfig, Node, Workflow


def _valid_wf() -> Workflow:
    return Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="cond", type="conditional", config={
                "conditions": [
                    {"type": "json_path", "expression": "$.data.score >= 80"},
                    {"type": "json_path", "expression": "$.data.score < 80"},
                ],
                "default_branch": "low",
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


def _codes(result, level=None):
    issues = result.errors if level == "error" else (
        result.warnings if level == "warning" else result.issues)
    return {i.code for i in issues}


def test_valid_workflow_has_no_errors():
    result = validate_workflow(_valid_wf())
    assert result.valid is True
    assert result.errors == []


def test_empty_workflow_is_invalid():
    result = validate_workflow(Workflow(id="wf", name="wf"))
    assert result.valid is False
    assert "E_NO_NODES" in _codes(result, "error")


def test_duplicate_node_id():
    wf = _valid_wf()
    wf.nodes.append(Node(id="start", type="end", config={}))
    result = validate_workflow(wf)
    assert "E_DUPLICATE_NODE_ID" in _codes(result, "error")


def test_dangling_edge_target():
    wf = _valid_wf()
    wf.edges.append(Edge(id="x", source_node_id="start", source_handle="default",
                         target_node_id="ghost"))
    result = validate_workflow(wf)
    assert "E_EDGE_TARGET_MISSING" in _codes(result, "error")


def test_dangling_edge_source():
    wf = _valid_wf()
    wf.edges.append(Edge(id="x", source_node_id="ghost", source_handle="default",
                         target_node_id="end1"))
    result = validate_workflow(wf)
    assert "E_EDGE_SOURCE_MISSING" in _codes(result, "error")


def test_start_with_no_outgoing_edges():
    wf = Workflow(
        id="wf", name="wf",
        nodes=[Node(id="start", type="start", config={})],
        edges=[],
    )
    result = validate_workflow(wf)
    assert "E_START_NO_OUTGOING" in _codes(result, "error")


def test_conditional_default_branch_matches_no_handle():
    wf = _valid_wf()
    # default_branch points to a handle that has no outgoing edge.
    wf.nodes[1].config = ConditionalNodeConfig(
        conditions=[
            {"type": "json_path", "expression": "$.data.score >= 80"},
            {"type": "json_path", "expression": "$.data.score < 80"},
        ],
        default_branch="nonexistent",
    )
    result = validate_workflow(wf)
    assert "E_CONDITIONAL_BAD_DEFAULT" in _codes(result, "error")


def test_conditional_with_no_fallback():
    wf = _valid_wf()
    # No default_branch and no 'default' handle among outgoing edges.
    wf.nodes[1].config = ConditionalNodeConfig(
        conditions=[
            {"type": "json_path", "expression": "$.data.score >= 80"},
            {"type": "json_path", "expression": "$.data.score < 80"},
        ],
        default_branch=None,
    )
    result = validate_workflow(wf)
    assert "E_CONDITIONAL_NO_FALLBACK" in _codes(result, "error")


def test_conditional_branch_count_mismatch():
    wf = _valid_wf()
    # 3 conditions but only 2 branch edges.
    wf.nodes[1].config = ConditionalNodeConfig(
        conditions=[
            {"type": "json_path", "expression": "$.data.score >= 90"},
            {"type": "json_path", "expression": "$.data.score >= 80"},
            {"type": "json_path", "expression": "$.data.score < 80"},
        ],
        default_branch="low",
    )
    result = validate_workflow(wf)
    assert "W_CONDITIONAL_BRANCH_MISMATCH" in _codes(result, "warning")


def test_agent_unknown_model():
    wf = Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="a1", type="agent", config={
                "model_id": "missing-model", "system_prompt": "hi",
            }),
            Node(id="end1", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="a1"),
            Edge(id="e", source_node_id="a1", source_handle="default", target_node_id="end1"),
        ],
    )
    result = validate_workflow(wf)
    assert "E_AGENT_MODEL_MISSING" in _codes(result, "error")


def test_agent_unknown_tool():
    wf = Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="a1", type="agent", config={
                "model_id": "m1", "system_prompt": "hi", "tool_ids": ["t-missing"],
            }),
            Node(id="end1", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="a1"),
            Edge(id="e", source_node_id="a1", source_handle="default", target_node_id="end1"),
        ],
        models=[ModelConfig(id="m1", name="m1", provider="openai_compatible", model="llama")],
    )
    result = validate_workflow(wf)
    assert "E_AGENT_TOOL_MISSING" in _codes(result, "error")


def test_agent_unknown_prompt_ref():
    wf = Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="a1", type="agent", config={
                "model_id": "m1", "system_prompt": "hi", "prompt_ref": "p-missing",
            }),
            Node(id="end1", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="a1"),
            Edge(id="e", source_node_id="a1", source_handle="default", target_node_id="end1"),
        ],
        models=[ModelConfig(id="m1", name="m1", provider="openai_compatible", model="llama")],
    )
    result = validate_workflow(wf)
    assert "E_AGENT_PROMPT_MISSING" in _codes(result, "error")


def test_agent_skill_unknown_tool():
    wf = Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="a1", type="agent", config={
                "model_id": "m1", "system_prompt": "hi",
                "skills": [{"name": "s1", "prompt": "do things", "tool_ids": ["t-missing"]}],
            }),
            Node(id="end1", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="a1"),
            Edge(id="e", source_node_id="a1", source_handle="default", target_node_id="end1"),
        ],
        models=[ModelConfig(id="m1", name="m1", provider="openai_compatible", model="llama")],
    )
    result = validate_workflow(wf)
    assert "E_AGENT_TOOL_MISSING" in _codes(result, "error")


def test_cycle_detection():
    wf = Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="a", type="transform",
                 config={"mode": "template", "template": "x", "output_field": "o"}),
            Node(id="b", type="transform",
                 config={"mode": "template", "template": "y", "output_field": "o"}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="a"),
            Edge(id="ab", source_node_id="a", source_handle="default", target_node_id="b"),
            Edge(id="ba", source_node_id="b", source_handle="default", target_node_id="a"),
        ],
    )
    result = validate_workflow(wf)
    assert "W_CYCLE_DETECTED" in _codes(result, "warning")


def test_unreachable_node():
    wf = _valid_wf()
    # Add an orphan node not connected to anything.
    wf.nodes.append(Node(id="orphan", type="transform",
                         config={"mode": "template", "template": "z", "output_field": "o"}))
    result = validate_workflow(wf)
    assert "W_UNREACHABLE_NODE" in _codes(result, "warning")


def test_no_end_node_warning():
    wf = Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="a", type="transform",
                 config={"mode": "template", "template": "x", "output_field": "o"}),
        ],
        edges=[Edge(id="s", source_node_id="start", source_handle="default", target_node_id="a")],
    )
    result = validate_workflow(wf)
    assert "W_NO_END" in _codes(result, "warning")


def test_api_validate_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import workflows as wf_api

    # Point the API at an isolated workflows dir.
    monkeypatch.setattr(wf_api.settings, "workflows_dir", tmp_path)
    wf = _valid_wf()
    (tmp_path / f"{wf.id}.json").write_text(wf.model_dump_json())

    client = TestClient(app)
    resp = client.post(f"/api/workflows/{wf.id}/validate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["errors"] == []


def test_api_validate_endpoint_404(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.api import workflows as wf_api

    monkeypatch.setattr(wf_api.settings, "workflows_dir", tmp_path)
    client = TestClient(app)
    resp = client.post("/api/workflows/does-not-exist/validate")
    assert resp.status_code == 404
