"""Tests for capability provenance stamping (source_capability / source_version) on tools and models."""
import json

from app.config import Settings
from app.persistence import workflows as wf_module
from app.persistence.workflows import WorkflowStore, load_workflow
from schema.models import ModelConfig, ToolDefinition


def _stamped_dict() -> dict:
    return {
        "id": "wf-prov",
        "name": "Prov WF",
        "description": None,
        "schema_version": 1,
        "nodes": [
            {"id": "start", "type": "start", "position": {"x": 0, "y": 0}, "config": {}},
            {"id": "end", "type": "end", "position": {"x": 200, "y": 0}, "config": {}},
        ],
        "edges": [],
        "tools": [
            {
                "id": "t1",
                "name": "search",
                "description": "Search things",
                "parameters": {},
                "implementation": {"type": "builtin", "config": {}},
                "source_capability": "acme/search-tool",
                "source_version": "1.2.3",
            }
        ],
        "models": [
            {
                "id": "m1",
                "name": "Local Llama",
                "provider": "openai_compatible",
                "model": "llama-3",
                "base_url": "http://localhost:11434/v1",
                "source_capability": "acme/llama-profile",
                "source_version": "0.9.0",
            }
        ],
    }


def test_stamped_entries_survive_load():
    wf = load_workflow(_stamped_dict())
    assert wf.tools[0].source_capability == "acme/search-tool"
    assert wf.tools[0].source_version == "1.2.3"
    assert wf.models[0].source_capability == "acme/llama-profile"
    assert wf.models[0].source_version == "0.9.0"


def test_stamped_entries_survive_save_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(wf_module, "get_settings", lambda: Settings(workflows_dir=tmp_path))
    store = WorkflowStore()
    original = load_workflow(_stamped_dict())
    store.save(original)
    loaded = store.get("wf-prov")
    assert loaded.tools[0].source_capability == "acme/search-tool"
    assert loaded.tools[0].source_version == "1.2.3"
    assert loaded.models[0].source_capability == "acme/llama-profile"
    assert loaded.models[0].source_version == "0.9.0"
    raw = json.loads((tmp_path / "wf-prov.json").read_text())
    assert raw["tools"][0]["source_capability"] == "acme/search-tool"
    assert raw["tools"][0]["source_version"] == "1.2.3"
    assert raw["models"][0]["source_capability"] == "acme/llama-profile"
    assert raw["models"][0]["source_version"] == "0.9.0"


def test_legacy_entries_without_provenance_load_as_none():
    data = _stamped_dict()
    for entry in data["tools"] + data["models"]:
        del entry["source_capability"]
        del entry["source_version"]
    wf = load_workflow(data)
    assert wf.tools[0].source_capability is None
    assert wf.tools[0].source_version is None
    assert wf.models[0].source_capability is None
    assert wf.models[0].source_version is None


def test_models_accept_provenance_fields():
    tool = ToolDefinition(
        id="t", name="t", description="", parameters={},
        implementation={"type": "builtin", "config": {}},
        source_capability="ns/t", source_version="2.0.0",
    )
    assert tool.source_capability == "ns/t"
    assert tool.source_version == "2.0.0"

    model = ModelConfig(
        id="m", name="m", provider="openai_compatible", model="x",
        source_capability="ns/m", source_version="1.0.0",
    )
    assert model.source_capability == "ns/m"
    assert model.source_version == "1.0.0"
