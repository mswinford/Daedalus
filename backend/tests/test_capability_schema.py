"""Tests for the Capability Manifest schema (schema/capability.py)."""
import pytest
from pydantic import ValidationError

from schema.capability import (
    AgentSpec,
    CapabilityGovernance,
    CapabilityInterface,
    CapabilityKind,
    CapabilityManifest,
    CapabilityRef,
    InterfaceType,
    ModelProfileSpec,
    PromptSpec,
    SkillSpec,
    ToolSpec,
    WorkflowSpec,
    semver_key,
)
from schema.models import ModelConfig, ToolDefinition, Workflow


def _tool_definition() -> ToolDefinition:
    return ToolDefinition(
        id="t1", name="lookup", description="Look up a record",
        parameters={"id": {"type": "string", "required": True}},
        implementation={"type": "builtin", "config": {"function": "lookup"}},
    )


def _model_config() -> ModelConfig:
    return ModelConfig(
        id="m1", name="Local Llama", provider="openai_compatible",
        model="llama3", base_url="http://localhost:8080/v1",
    )


def _workflow() -> Workflow:
    return Workflow(id="wf1", name="demo")


def _manifest(kind, spec, **overrides) -> dict:
    base = {
        "name": "finance/invoice-analyzer",
        "version": "1.0.0",
        "description": "Analyzes invoices",
        "kind": kind,
        "spec": spec,
        "governance": {"owner": "finance"},
        "created_at": 1700000000.0,
    }
    base.update(overrides)
    return base


def _interface() -> dict:
    return {"type": "daedalus_workflow", "input_schema": {}, "output_schema": {}}


# ─── Parsing per kind ────────────────────────────────────────────────────────

def test_tool_manifest_parses():
    m = CapabilityManifest(**_manifest(
        "tool", {"kind": "tool", "tool": _tool_definition()}, interface=_interface(),
    ))
    assert m.kind is CapabilityKind.TOOL
    assert isinstance(m.spec, ToolSpec)
    assert m.spec.tool.name == "lookup"


def test_prompt_manifest_parses_without_interface():
    m = CapabilityManifest(**_manifest(
        "prompt", {"kind": "prompt", "text": "You are {{role}}."},
    ))
    assert isinstance(m.spec, PromptSpec)
    assert m.spec.role == "system"
    assert m.interface is None


def test_model_profile_manifest_parses():
    m = CapabilityManifest(**_manifest(
        "model_profile", {"kind": "model_profile", "model": _model_config()},
    ))
    assert isinstance(m.spec, ModelProfileSpec)
    assert m.spec.model.id == "m1"


def test_skill_manifest_parses():
    m = CapabilityManifest(**_manifest(
        "skill", {
            "kind": "skill",
            "prompt": "Extract line items.",
            "tools": [{"name": "finance/lookup", "version": "latest"}],
        },
    ))
    assert isinstance(m.spec, SkillSpec)
    assert m.spec.tools[0].version == "latest"


def test_agent_manifest_parses():
    m = CapabilityManifest(**_manifest(
        "agent", {
            "kind": "agent",
            "model_profile": {"name": "platform/local-llama"},
            "prompt": "You are an invoice agent.",
            "skills": [{"name": "finance/line-items"}],
        },
    ))
    assert isinstance(m.spec, AgentSpec)
    assert m.spec.model_profile.name == "platform/local-llama"


def test_workflow_manifest_parses_with_embedded_graph():
    m = CapabilityManifest(**_manifest(
        "workflow", {"kind": "workflow", "workflow": _workflow()}, interface=_interface(),
    ))
    assert isinstance(m.spec, WorkflowSpec)
    assert m.spec.workflow.id == "wf1"


def test_workflow_spec_ref_only_parses():
    m = CapabilityManifest(**_manifest(
        "workflow", {"kind": "workflow", "workflow_ref": "finance/invoice-analyzer"},
        interface=_interface(),
    ))
    assert m.spec.workflow is None
    assert m.spec.workflow_ref == "finance/invoice-analyzer"


# ─── Discrimination & consistency ────────────────────────────────────────────

def test_spec_kind_mismatch_rejected():
    with pytest.raises(ValidationError, match="does not match manifest kind"):
        CapabilityManifest(**_manifest("tool", {"kind": "prompt", "text": "x"}))


def test_spec_missing_kind_rejected():
    with pytest.raises(ValidationError):
        CapabilityManifest(**_manifest("prompt", {"text": "x"}))


def test_interface_required_for_tool():
    with pytest.raises(ValidationError, match="interface is required"):
        CapabilityManifest(**_manifest("tool", {"kind": "tool", "tool": _tool_definition()}))


def test_interface_required_for_workflow():
    with pytest.raises(ValidationError, match="interface is required"):
        CapabilityManifest(**_manifest(
            "workflow", {"kind": "workflow", "workflow": _workflow()},
        ))


def test_roadmap_kind_has_no_spec():
    with pytest.raises(ValidationError):
        CapabilityManifest(**_manifest("policy", {"kind": "policy"}))


def test_workflow_spec_requires_payload():
    with pytest.raises(ValidationError, match="requires an embedded 'workflow'"):
        WorkflowSpec(kind="workflow")


# ─── Identity validation ─────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["finance/invoice-analyzer", "a/b-c.d_e"])
def test_valid_names(name):
    m = CapabilityManifest(**_manifest("prompt", {"kind": "prompt", "text": "x"}, name=name))
    assert m.name == name


@pytest.mark.parametrize("name", ["no-slash", "/leading", "trail/", "a/b/c", "-bad/name"])
def test_invalid_names(name):
    with pytest.raises(ValidationError, match="owner/name"):
        CapabilityManifest(**_manifest("prompt", {"kind": "prompt", "text": "x"}, name=name))


@pytest.mark.parametrize("version", ["1.0.0", "0.1.2", "2.10.0-rc.1", "1.0.0+build5"])
def test_valid_versions(version):
    m = CapabilityManifest(**_manifest("prompt", {"kind": "prompt", "text": "x"}, version=version))
    assert m.version == version


@pytest.mark.parametrize("version", ["1", "1.2", "v1.2.3", "latest", "1.2.3.4", "1.02.3"])
def test_invalid_versions(version):
    with pytest.raises(ValidationError, match="semver"):
        CapabilityManifest(**_manifest("prompt", {"kind": "prompt", "text": "x"}, version=version))


def test_ref_allows_latest_and_semver():
    assert CapabilityRef(name="a/b").version == "latest"
    assert CapabilityRef(name="a/b", version="1.2.3").version == "1.2.3"
    with pytest.raises(ValidationError):
        CapabilityRef(name="a/b", version="nope")
    with pytest.raises(ValidationError, match="owner/name"):
        CapabilityRef(name="noslash")


# ─── semver_key ordering ─────────────────────────────────────────────────────

def test_semver_key_precedence():
    versions = ["1.0.0", "1.0.1", "2.0.0", "1.10.0", "1.9.0"]
    assert sorted(versions, key=semver_key) == ["1.0.0", "1.0.1", "1.9.0", "1.10.0", "2.0.0"]


def test_semver_key_prerelease_orders_before_release():
    versions = ["1.0.0", "1.0.0-rc.1", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta.2"]
    assert sorted(versions, key=semver_key) == [
        "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta.2", "1.0.0-rc.1", "1.0.0",
    ]


def test_semver_key_numeric_prerelease_identifiers_compare_numerically():
    assert semver_key("1.0.0-2") < semver_key("1.0.0-10")


def test_semver_key_rejects_invalid():
    with pytest.raises(ValueError):
        semver_key("not-a-version")


# ─── Evaluation block ────────────────────────────────────────────────────────

def test_evaluation_with_full_stats_round_trips():
    m = CapabilityManifest(**_manifest(
        "prompt", {"kind": "prompt", "text": "x"},
        evaluation={
            "score": 0.97,
            "last_scored_at": 1700000123.0,
            "stats": {
                "runs_total": 120,
                "runs_failed": 4,
                "duration_ms_p50": 350.5,
                "duration_ms_p95": 980.25,
                "avg_cost_usd": 0.0042,
            },
        },
    ))
    assert m.evaluation.score == 0.97
    assert m.evaluation.stats.runs_total == 120
    assert m.evaluation.stats.runs_failed == 4
    assert m.evaluation.stats.duration_ms_p50 == 350.5
    assert m.evaluation.stats.duration_ms_p95 == 980.25
    assert m.evaluation.stats.avg_cost_usd == 0.0042
    assert CapabilityManifest.model_validate(m.model_dump(mode="json")) == m


def test_evaluation_score_only_still_validates():
    m = CapabilityManifest(**_manifest(
        "prompt", {"kind": "prompt", "text": "x"},
        evaluation={"score": 0.9, "last_scored_at": 1700000000.0},
    ))
    assert m.evaluation.score == 0.9
    assert m.evaluation.stats is None


def test_manifest_without_evaluation_still_validates():
    m = CapabilityManifest(**_manifest("prompt", {"kind": "prompt", "text": "x"}))
    assert m.evaluation is None


# ─── Round-trip ──────────────────────────────────────────────────────────────

def test_json_round_trip():
    data = _manifest(
        "workflow", {"kind": "workflow", "workflow": _workflow()}, interface=_interface(),
        tags=["finance"],
        dependencies=[{"name": "platform/local-llama", "version": "1.0.0"}],
        secrets_required=["OPENAI_API_KEY"],
    )
    m = CapabilityManifest(**data)
    assert m.model_validate(m.model_dump(mode="json")) == m
