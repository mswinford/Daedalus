"""
AI Forge — Workflow Schema

Pydantic models that define the structure of a workflow.
These are the single source of truth for the JSON format persisted to disk.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


# ─── State Schema ────────────────────────────────────────────────────────────

class StateFieldType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class StateField(BaseModel):
    """A single field in the workflow's shared state."""
    name: str = Field(..., description="Field name, used by nodes to read/write state")
    type: StateFieldType = Field(..., description="JSON type of the field")
    description: Optional[str] = Field(None, description="Human-readable description")
    required: bool = Field(False, description="Whether the field must be present")
    items_type: Optional[StateFieldType] = Field(
        None, description="If type is 'array', the type of array items"
    )


class StateSchema(BaseModel):
    """The shared state that flows through the workflow."""
    fields: list[StateField] = Field(
        default_factory=list, description="All fields in the shared state"
    )


# ─── Model Config ────────────────────────────────────────────────────────────

class ModelProvider(str, Enum):
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"


class ModelConfig(BaseModel):
    """A model definition that agents can reference."""
    id: str = Field(..., description="Unique identifier for this model config")
    name: str = Field(..., description="Human-readable name, e.g. 'Local Llama'")
    provider: ModelProvider = Field(..., description="Provider type")
    model: str = Field(..., description="Model name/identifier")
    base_url: Optional[str] = Field(
        None, description="Base URL for the API (required for openai_compatible)"
    )
    api_key_ref: Optional[str] = Field(
        None, description="Reference to a secret key (name in secrets store)"
    )
    default_temperature: float = Field(0.7, ge=0, le=2)
    track_cost: bool = Field(False, description="Whether to track token costs for this model")
    pricing: Optional[dict[str, float]] = Field(
        None,
        description="Cost per 1M tokens, e.g. {'input': 0.0, 'output': 0.0}. Local models = $0."
    )


# ─── Tool Definition ────────────────────────────────────────────────────────

class ToolImplementationType(str, Enum):
    BUILTIN = "builtin"
    CUSTOM_FUNCTION = "custom_function"
    HTTP = "http"


class JsonSchemaParam(BaseModel):
    """A parameter in a tool's JSON Schema."""
    type: StateFieldType
    description: Optional[str] = None
    required: bool = False
    enum: Optional[list[str]] = None


class ToolDefinition(BaseModel):
    """A tool available to agents. Defined at workflow level, selected per-agent."""
    id: str = Field(..., description="Unique identifier")
    name: str = Field(..., description="Tool name, shown to the LLM")
    description: str = Field(..., description="Description the LLM uses to decide when to call it")
    parameters: dict[str, JsonSchemaParam] = Field(
        default_factory=dict, description="JSON Schema parameters for the tool"
    )
    implementation: ToolImplementation = Field(..., description="How the tool is implemented")


class ToolImplementation(BaseModel):
    type: ToolImplementationType
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Implementation-specific config (code for custom_function, url for http, etc.)"
    )


# ─── Retry Config ────────────────────────────────────────────────────────────

class RetryConfig(BaseModel):
    """Retry configuration for a node."""
    enabled: bool = Field(False, description="Enable retries for this node")
    max_retries: int = Field(3, ge=0, le=10, description="Maximum number of retry attempts")
    backoff_base: float = Field(1.0, gt=0, description="Base delay in seconds, doubled each retry")
    retry_on: list[str] = Field(
        default_factory=lambda: ["rate_limit", "timeout", "server_error"],
        description="Error types to retry on"
    )


# ─── Node Configs ────────────────────────────────────────────────────────────

class StartNodeConfig(BaseModel):
    """Configuration for the Start node."""
    input_fields: list[str] = Field(
        default_factory=list,
        description="Fields that the workflow accepts as input"
    )


class EndNodeConfig(BaseModel):
    """Configuration for the End node."""
    output_fields: list[str] = Field(
        default_factory=list,
        description="Fields that the workflow produces as output"
    )


class AgentNodeConfig(BaseModel):
    """Configuration for an Agent node."""
    model_id: str = Field(..., description="Reference to a ModelConfig")
    system_prompt: str = Field(..., description="System prompt for the agent")
    temperature: Optional[float] = Field(None, ge=0, le=2, description="Override model temperature")
    tool_ids: list[str] = Field(
        default_factory=list, description="References to ToolDefinitions this agent can use"
    )
    max_iterations: int = Field(10, ge=1, le=100, description="Maximum agent iterations")
    retry: Optional[RetryConfig] = Field(None, description="Retry configuration")


class ConditionType(str, Enum):
    JSON_PATH = "json_path"
    REGEX = "regex"
    LLM = "llm"


class ConditionConfig(BaseModel):
    """Configuration for a conditional edge or Conditional node."""
    type: ConditionType
    expression: str = Field(..., description="The condition expression (syntax depends on type)")
    description: Optional[str] = None


class ConditionalNodeConfig(BaseModel):
    """Configuration for a Conditional node."""
    conditions: list[ConditionConfig] = Field(
        ..., description="Conditions that determine which branch to take"
    )
    default_branch: Optional[str] = Field(
        None, description="Default branch name if no condition matches"
    )


class FieldMapping(BaseModel):
    """Maps an input state field to an output field."""
    source: str = Field(..., description="Source state field name")
    target: str = Field(..., description="Target output field name")
    transform: Optional[str] = Field(
        None, description="Optional transform expression (template string)"
    )


class TransformNodeConfig(BaseModel):
    """Configuration for a Transform node."""
    mode: Literal["template", "mapping", "custom_function"] = Field(
        ..., description="Transformation mode"
    )
    template: Optional[str] = Field(
        None, description="Template string for 'template' mode, e.g. 'Hello {{name}}'"
    )
    field_mappings: Optional[list[FieldMapping]] = Field(
        None, description="Field mappings for 'mapping' mode"
    )
    custom_function_id: Optional[str] = Field(
        None, description="Reference to a CustomFunction node for 'custom_function' mode"
    )
    output_field: str = Field(..., description="Name of the output state field to write")
    retry: Optional[RetryConfig] = Field(None)


class HumanInputField(BaseModel):
    """A field the human is asked to provide."""
    name: str = Field(..., description="Field name")
    label: str = Field(..., description="Human-readable label")
    type: Literal["text", "textarea", "select", "boolean"] = Field(..., description="Input type")
    required: bool = Field(False)
    options: Optional[list[str]] = Field(None, description="For 'select' type")


class HumanInLoopNodeConfig(BaseModel):
    """Configuration for a Human-in-the-Loop node."""
    input_fields: list[HumanInputField] = Field(
        default_factory=list, description="Fields the human is asked to provide"
    )
    approval_required: bool = Field(
        False, description="If true, the human must approve/reject before continuing"
    )
    approval_message: Optional[str] = Field(
        None, description="Message shown to the human for approval"
    )
    timeout_seconds: Optional[int] = Field(
        None, ge=0, description="Timeout in seconds. None = wait indefinitely."
    )
    output_fields: list[str] = Field(
        default_factory=list, description="State fields to write with human input"
    )


class CustomFunctionNodeConfig(BaseModel):
    """Configuration for a Custom Function node."""
    code: str = Field(..., description="Python code to execute (sandboxed)")
    timeout_seconds: int = Field(30, ge=1, le=300, description="Execution timeout")
    input_fields: list[str] = Field(
        default_factory=list, description="State fields the function reads"
    )
    output_fields: list[str] = Field(
        default_factory=list, description="State fields the function writes"
    )
    retry: Optional[RetryConfig] = Field(None)


# ─── Node Types ──────────────────────────────────────────────────────────────

NodeType = Literal[
    "start",
    "end",
    "agent",
    "conditional",
    "transform",
    "human_in_loop",
    "custom_function",
]

NodeConfig = Union[
    StartNodeConfig,
    EndNodeConfig,
    AgentNodeConfig,
    ConditionalNodeConfig,
    TransformNodeConfig,
    HumanInLoopNodeConfig,
    CustomFunctionNodeConfig,
]


class NodePosition(BaseModel):
    x: float = 0
    y: float = 0


class Node(BaseModel):
    """A node in the workflow graph."""
    id: str = Field(..., description="Unique node identifier")
    type: NodeType = Field(..., description="Node type")
    position: NodePosition = Field(default_factory=NodePosition, description="Position on canvas")
    config: NodeConfig = Field(..., description="Node-specific configuration")


# ─── Edge Types ──────────────────────────────────────────────────────────────

EdgeType = Literal["static", "conditional", "error"]


class Edge(BaseModel):
    """A connection between two nodes."""
    id: str = Field(..., description="Unique edge identifier")
    source_node_id: str = Field(..., description="Source node id")
    source_handle: str = Field(
        ..., description="Source handle name: 'default', 'error', or branch name"
    )
    target_node_id: str = Field(..., description="Target node id")
    type: EdgeType = Field("static", description="Edge type")
    condition: Optional[ConditionConfig] = Field(
        None, description="Condition for conditional edges"
    )


# ─── Workflow ────────────────────────────────────────────────────────────────

class Workflow(BaseModel):
    """Top-level workflow definition."""
    id: str = Field(..., description="Unique workflow identifier")
    name: str = Field(..., description="Human-readable name")
    description: Optional[str] = Field(None, description="Workflow description")
    schema_version: int = Field(1, description="Schema version for migrations")
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    tools: list[ToolDefinition] = Field(default_factory=list)
    models: list[ModelConfig] = Field(default_factory=list)
    state_schema: Optional[StateSchema] = Field(None, description="Explicit state schema (auto-inferred if not set)")


# ─── Run Types ───────────────────────────────────────────────────────────────

class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"  # Human-in-loop waiting


class RunEvent(BaseModel):
    """An event emitted during workflow execution."""
    timestamp: float = Field(..., description="Unix timestamp")
    type: Literal[
        "run_start",
        "run_end",
        "node_start",
        "node_end",
        "node_error",
        "llm_call",
        "llm_token",
        "tool_call",
        "tool_result",
        "human_request",
        "human_respond",
        "retry",
    ]
    node_id: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)


class WorkflowRun(BaseModel):
    """A single execution of a workflow."""
    id: str = Field(..., description="Unique run identifier")
    workflow_id: str = Field(..., description="Reference to the workflow")
    status: RunStatus = RunStatus.PENDING
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    events: list[RunEvent] = Field(default_factory=list)
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    estimated_cost_usd: float = 0.0
