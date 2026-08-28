"""Tests for #3: validate run input against workflow.state_schema."""
import pytest

from app.engine.runner import run_workflow_sync
from schema.models import Workflow, Node, Edge, StateSchema, StateField, StateFieldType


def _wf_with_schema(fields):
    """Minimal start -> end workflow with a state_schema."""
    return Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="end"),
        ],
        state_schema=StateSchema(fields=fields),
    )


def test_no_schema_accepts_anything():
    wf = _wf_with_schema([])
    out = run_workflow_sync(wf, {"anything": 123})
    assert out["data"]["anything"] == 123


def test_required_field_missing_raises():
    wf = _wf_with_schema([StateField(name="score", type=StateFieldType.NUMBER, required=True)])
    with pytest.raises(ValueError, match="Required input field 'score'"):
        run_workflow_sync(wf, {})


def test_required_field_present_passes():
    wf = _wf_with_schema([StateField(name="score", type=StateFieldType.NUMBER, required=True)])
    out = run_workflow_sync(wf, {"score": 85})
    assert out["data"]["score"] == 85


def test_optional_field_absent_is_fine():
    wf = _wf_with_schema([StateField(name="name", type=StateFieldType.STRING, required=False)])
    out = run_workflow_sync(wf, {})
    assert "name" not in out["data"]


def test_type_mismatch_string_vs_number():
    wf = _wf_with_schema([StateField(name="score", type=StateFieldType.NUMBER)])
    with pytest.raises(ValueError, match="expects number"):
        run_workflow_sync(wf, {"score": "not_a_number"})


def test_type_mismatch_number_vs_string():
    wf = _wf_with_schema([StateField(name="name", type=StateFieldType.STRING)])
    with pytest.raises(ValueError, match="expects string"):
        run_workflow_sync(wf, {"name": 42})


def test_type_mismatch_boolean():
    wf = _wf_with_schema([StateField(name="flag", type=StateFieldType.BOOLEAN)])
    with pytest.raises(ValueError, match="expects boolean"):
        run_workflow_sync(wf, {"flag": "yes"})


def test_type_mismatch_array():
    wf = _wf_with_schema([StateField(name="items", type=StateFieldType.ARRAY)])
    with pytest.raises(ValueError, match="expects array"):
        run_workflow_sync(wf, {"items": "not_a_list"})


def test_type_mismatch_object():
    wf = _wf_with_schema([StateField(name="meta", type=StateFieldType.OBJECT)])
    with pytest.raises(ValueError, match="expects object"):
        run_workflow_sync(wf, {"meta": [1, 2]})


def test_multiple_fields_all_validated():
    wf = _wf_with_schema([
        StateField(name="a", type=StateFieldType.STRING),
        StateField(name="b", type=StateFieldType.NUMBER, required=True),
    ])
    with pytest.raises(ValueError, match="Required input field 'b'"):
        run_workflow_sync(wf, {"a": "ok"})
