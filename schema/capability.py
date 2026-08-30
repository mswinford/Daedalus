"""
AI Forge — Capability Manifest Schema

Pydantic models defining the Capability Package contract: identity + kind spec
+ interface + governance + versioning. Single source of truth shared by the
registry service and AI Forge (see docs/capability-registry-plan.md).

`kind` says what a capability *is* (semantic); `interface` says how it is
*invoked* (protocol). The two are orthogonal. Only `tool` and `workflow`
require an explicit interface in R1 — other kinds inherit their contract from
the spec until they become invokable in R2.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import ModelConfig, ToolDefinition, Workflow


# ─── Enums ───────────────────────────────────────────────────────────────────

class CapabilityKind(str, Enum):
    TOOL = "tool"
    PROMPT = "prompt"
    MODEL_PROFILE = "model_profile"
    SKILL = "skill"
    AGENT = "agent"
    WORKFLOW = "workflow"
    # Roadmap kinds — in the enum for forward compatibility, no spec built yet.
    POLICY = "policy"
    KNOWLEDGE = "knowledge"
    CONNECTOR = "connector"
    EVAL_SUITE = "eval_suite"


class InterfaceType(str, Enum):
    AI_FORGE_WORKFLOW = "ai_forge_workflow"
    MCP = "mcp"
    HTTP = "http"


class LifecycleStage(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class SecurityStatus(str, Enum):
    UNREVIEWED = "unreviewed"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    FLAGGED = "flagged"


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


# ─── Validation helpers ──────────────────────────────────────────────────────

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _validate_name(value: str) -> str:
    if not _NAME_RE.match(value):
        raise ValueError(
            f"capability name must be 'owner/name' (e.g. 'finance/invoice-analyzer'), got {value!r}"
        )
    return value


def _validate_semver(value: str, allow_latest: bool = False) -> str:
    if allow_latest and value == "latest":
        return value
    if not _SEMVER_RE.match(value):
        raise ValueError(f"invalid semver version {value!r} (expected MAJOR.MINOR.PATCH with optional -prerelease/+build)")
    return value


def semver_key(version: str) -> tuple:
    """Sortable key implementing semver precedence (prerelease < release)."""
    m = _SEMVER_RE.match(version)
    if not m:
        raise ValueError(f"invalid semver version {version!r}")
    core = (int(m.group("major")), int(m.group("minor")), int(m.group("patch")))
    pre = m.group("pre")
    if pre is None:
        return (*core, 1, ())
    parts = tuple(
        (0, int(ident), "") if ident.isdigit() else (1, 0, ident)
        for ident in pre.split(".")
    )
    return (*core, 0, parts)


# ─── Shared reference ────────────────────────────────────────────────────────

class CapabilityRef(BaseModel):
    """Reference to another capability by name@version."""
    name: str = Field(..., description="Capability name, 'owner/name'")
    version: str = Field("latest", description="Semver or 'latest'")

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        return _validate_semver(v, allow_latest=True)


# ─── Per-kind specs (discriminated by `kind`) ────────────────────────────────

class ToolSpec(BaseModel):
    """kind=tool — wraps an existing ToolDefinition."""
    kind: Literal["tool"] = "tool"
    tool: ToolDefinition


class PromptSpec(BaseModel):
    """kind=prompt — a prompt template with {{var}} placeholders."""
    kind: Literal["prompt"] = "prompt"
    text: str
    variables: list[str] = Field(default_factory=list)
    role: Literal["system", "user", "assistant"] = "system"


class ModelProfileSpec(BaseModel):
    """kind=model_profile — wraps an existing ModelConfig."""
    kind: Literal["model_profile"] = "model_profile"
    model: ModelConfig
    notes: Optional[str] = None


class SkillSpec(BaseModel):
    """kind=skill (composite) — instructions + tool refs; no model, no graph.

    Attached to an agent node via its `skills[]` field and folded into that
    agent's system prompt + tools at runtime.
    """
    kind: Literal["skill"] = "skill"
    prompt: Optional[str] = None
    prompt_ref: Optional[CapabilityRef] = None
    tools: list[CapabilityRef] = Field(default_factory=list)


class AgentSpec(BaseModel):
    """kind=agent (composite) — a single self-directed unit; NO embedded graph.

    An agent or skill that needs a real multi-step graph should be published
    as a `workflow` kind instead.
    """
    kind: Literal["agent"] = "agent"
    model_profile: CapabilityRef
    prompt: Optional[str] = None
    prompt_ref: Optional[CapabilityRef] = None
    tools: list[CapabilityRef] = Field(default_factory=list)
    skills: list[CapabilityRef] = Field(default_factory=list)


class WorkflowSpec(BaseModel):
    """kind=workflow — the only kind that carries a graph."""
    kind: Literal["workflow"] = "workflow"
    workflow: Optional[Workflow] = None
    workflow_ref: Optional[str] = None

    @model_validator(mode="after")
    def _require_payload(self) -> WorkflowSpec:
        if self.workflow is None and not self.workflow_ref:
            raise ValueError("workflow spec requires an embedded 'workflow' or a 'workflow_ref'")
        return self


KindSpec = Union[ToolSpec, PromptSpec, ModelProfileSpec, SkillSpec, AgentSpec, WorkflowSpec]


# ─── Universal blocks ────────────────────────────────────────────────────────

class CapabilityInterface(BaseModel):
    """How the capability is invoked. Kind-aware shape (see plan §4)."""
    type: InterfaceType
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    invocation: dict[str, Any] = Field(
        default_factory=dict, description="Protocol-specific call details"
    )


class CapabilityGovernance(BaseModel):
    """METADATA ONLY in R1 — no enforcement (that is the R3 gateway's job)."""
    owner: str
    data_classification: DataClassification = DataClassification.INTERNAL
    human_approval_required: bool = False
    security_status: SecurityStatus = SecurityStatus.UNREVIEWED
    allowed_consumers: list[str] = Field(default_factory=list)


class CapabilitySemantics(BaseModel):
    """Semantic metadata for agent discovery (lights up in R3)."""
    purpose: Optional[str] = None
    use_when: list[str] = Field(default_factory=list)
    avoid_when: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)


class CapabilityEvaluationRef(BaseModel):
    """Scores computed by the runtime (lights up in R2)."""
    suite_id: Optional[str] = None
    last_scored_at: Optional[float] = None
    score: Optional[float] = None


# ─── Manifest ────────────────────────────────────────────────────────────────

class CapabilityManifest(BaseModel):
    """The central contract: identity + kind spec + interface + governance."""

    # Identity
    name: str = Field(..., description="Capability name, 'owner/name'")
    version: str = Field(..., description="Semver — versions are immutable")
    description: str
    tags: list[str] = Field(default_factory=list)

    # What it is + its payload
    kind: CapabilityKind
    spec: Annotated[KindSpec, Field(discriminator="kind")]
    interface: Optional[CapabilityInterface] = Field(
        None,
        description="Required for tool & workflow kinds; others inherit from spec until invokable (R2)",
    )

    # Composition (metadata only in R1; resolved in R2)
    dependencies: list[CapabilityRef] = Field(default_factory=list)
    models_required: list[str] = Field(default_factory=list)
    secrets_required: list[str] = Field(default_factory=list)

    governance: CapabilityGovernance
    stage: LifecycleStage = LifecycleStage.DRAFT
    breaking_changes: Optional[str] = Field(
        None, description="Human note on what changed incompatibly (guidance only in R1)"
    )

    created_at: Optional[float] = Field(
        None, description="Set by the registry at publish time if omitted"
    )
    updated_at: Optional[float] = None
    published_at: Optional[float] = None
    source_repo: Optional[str] = None
    source_commit: Optional[str] = None
    semantics: Optional[CapabilitySemantics] = None
    evaluation: Optional[CapabilityEvaluationRef] = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("version")
    @classmethod
    def _check_version(cls, v: str) -> str:
        return _validate_semver(v)

    @model_validator(mode="after")
    def _check_consistency(self) -> CapabilityManifest:
        if self.spec.kind != self.kind.value:
            raise ValueError(
                f"spec kind {self.spec.kind!r} does not match manifest kind {self.kind.value!r}"
            )
        if self.kind in (CapabilityKind.TOOL, CapabilityKind.WORKFLOW) and self.interface is None:
            raise ValueError(f"interface is required for {self.kind.value} capabilities")
        return self
