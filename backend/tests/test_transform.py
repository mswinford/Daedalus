"""Tests for #1 data-flow: custom_function write-back + transform nested reads."""
from app.engine.runner import run_workflow_sync
from schema.models import Workflow, Node, Edge


def _wf(cf_code, cf_output_fields, tf_config):
    """start -> custom_function -> transform -> end."""
    return Workflow(
        id="wf", name="wf",
        nodes=[
            Node(id="start", type="start", config={}),
            Node(id="cf", type="custom_function",
                 config={"code": cf_code, "output_fields": cf_output_fields}),
            Node(id="tf", type="transform", config=tf_config),
            Node(id="end", type="end", config={}),
        ],
        edges=[
            Edge(id="s", source_node_id="start", source_handle="default", target_node_id="cf"),
            Edge(id="c", source_node_id="cf", source_handle="default", target_node_id="tf"),
            Edge(id="t", source_node_id="tf", source_handle="default", target_node_id="end"),
        ],
    )


def test_custom_function_writes_declared_output_fields_to_data():
    code = 'result["grade"] = "B"\nresult["scratch"] = 123'
    out = run_workflow_sync(
        _wf(code, ["grade"], {"mode": "template", "template": "x", "output_field": "out"}), {}
    )
    assert out["data"]["grade"] == "B"
    assert "scratch" not in out["data"]


def test_custom_function_preserves_existing_data():
    code = 'result["grade"] = "A"'
    out = run_workflow_sync(
        _wf(code, ["grade"], {"mode": "template", "template": "x", "output_field": "out"}),
        {"score": 55},
    )
    assert out["data"]["grade"] == "A"
    assert out["data"]["score"] == 55


def test_transform_template_resolves_nested_paths():
    code = 'result["user"] = {"name": "Ada"}'
    out = run_workflow_sync(
        _wf(code, ["user"],
            {"mode": "template", "template": "Hi {{data.user.name}}!", "output_field": "greeting"}),
        {},
    )
    assert out["output"] == "Hi Ada!"
    assert out["data"]["greeting"] == "Hi Ada!"


def test_transform_mapping_resolves_dotted_paths():
    code = 'result["profile"] = {"city": "Oslo"}'
    out = run_workflow_sync(
        _wf(code, ["profile"], {
            "mode": "mapping",
            "field_mappings": [{"source": "data.profile.city", "target": "city"}],
            "output_field": "mapped",
        }),
        {},
    )
    assert "'city': 'Oslo'" in out["output"]


def test_transform_template_missing_path_renders_empty():
    out = run_workflow_sync(
        _wf('result["x"] = 1', ["x"],
            {"mode": "template", "template": "[{{data.nope}}]", "output_field": "out"}),
        {},
    )
    assert out["output"] == "[]"
