"""Tests for the workflow schema_version loader hook (R11)."""
import json

import pytest

from app.config import Settings
from app.persistence import workflows as wf_module
from app.persistence.workflows import WorkflowStore, load_workflow
from schema.models import Workflow


def _v1_dict() -> dict:
    return {
        "id": "wf-1",
        "name": "Test WF",
        "description": None,
        "schema_version": 1,
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "config": {}},
            {"id": "end", "type": "end", "position": {"x": 200, "y": 0}, "config": {}},
        ],
        "edges": [
            {
                "id": "e1",
                "source_node_id": "start",
                "source_handle": "default",
                "target_node_id": "end",
            },
        ],
        "tools": [],
        "models": [],
    }


def test_v1_dict_loads_unchanged():
    data = _v1_dict()
    wf = load_workflow(data)
    assert wf == Workflow.model_validate(data)
    assert wf.schema_version == 1


def test_missing_schema_version_defaults_to_1():
    data = _v1_dict()
    del data["schema_version"]
    wf = load_workflow(data)
    assert wf.schema_version == 1


def test_future_version_raises_with_version_in_message():
    data = _v1_dict()
    data["schema_version"] = 99
    with pytest.raises(ValueError, match="99"):
        load_workflow(data)


@pytest.mark.parametrize("bad", ["one", None, 0, -3, 2.5])
def test_invalid_version_raises(bad):
    data = _v1_dict()
    data["schema_version"] = bad
    with pytest.raises(ValueError):
        load_workflow(data)


def test_store_get_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(wf_module, "get_settings", lambda: Settings(workflows_dir=tmp_path))
    store = WorkflowStore()
    original = Workflow.model_validate(_v1_dict())
    store.save(original)
    loaded = store.get("wf-1")
    assert loaded == original


def test_save_stamps_current_schema_version(tmp_path, monkeypatch):
    monkeypatch.setattr(wf_module, "get_settings", lambda: Settings(workflows_dir=tmp_path))
    store = WorkflowStore()
    original = Workflow.model_validate(_v1_dict())
    original.schema_version = 1
    store.save(original)
    raw = json.loads((tmp_path / "wf-1.json").read_text())
    assert raw["schema_version"] == wf_module.CURRENT_SCHEMA_VERSION


def test_store_get_rejects_future_version(tmp_path, monkeypatch):
    monkeypatch.setattr(wf_module, "get_settings", lambda: Settings(workflows_dir=tmp_path))
    store = WorkflowStore()
    data = _v1_dict()
    data["schema_version"] = 99
    (tmp_path / "wf-1.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="99"):
        store.get("wf-1")
