// Hand-written layer on top of the generated schema types.
// Field shapes come from ./workflowTypes.generated.ts (regenerate with
// `npm run generate:types` after changing the Pydantic models). This file only
// keeps what the generator cannot express or where the frontend needs stricter
// typing than the backend schema:
//   - per-type node config interfaces + the discriminated node union (quicktype
//     flattens the 7-way `config` anyOf into one permissive `Config` interface),
//   - local overrides that re-tighten optionality the editor relies on.

import type {
  AgentSkill as GenAgentSkill,
  ConditionConfig as GenConditionConfig,
  Edge as GenEdge,
  FieldMapping as GenFieldMapping,
  HumanInputField as GenHumanInputField,
  Mode as GenMode,
  ModelConfig as GenModelConfig,
  NodeType as GenNodeType,
  RetryConfig as GenRetryConfig,
  ToolImplementationType as GenToolImplementationType,
  JSONSchemaParam as GenJSONSchemaParam,
  Workflow as GenWorkflow,
} from './workflowTypes.generated'

// ─── Re-exports: generated types under their existing frontend names ─────────

export type {
  ConditionType,
  EdgeType,
  NodeType,
  ToolImplementationType,
} from './workflowTypes.generated'

export type {
  ConditionConfig,
  FieldMapping,
  HumanInputField,
  ModelConfig,
  PromptDefinition,
  RetryConfig,
  StateField,
} from './workflowTypes.generated'

export type { Edge as WorkflowEdge } from './workflowTypes.generated'
export type { JSONSchemaParam as JsonSchemaParam } from './workflowTypes.generated'
export type { Mode as TransformMode } from './workflowTypes.generated'

// Override: the schema makes parameters and implementation.config optional; the
// editor always has both present.
export interface ToolDefinition {
  id: string
  name: string
  description: string
  parameters: Record<string, GenJSONSchemaParam>
  implementation: { type: GenToolImplementationType; config: Record<string, unknown> }
  source_capability?: string | null
  source_version?: string | null
  track_latest?: boolean
}

// ─── Per-type node configs (flattened into `Config` in the generated file) ──

export interface StartNodeConfig {
  input_fields: string[]
}

export interface EndNodeConfig {
  output_fields: string[]
}

// Override: the schema leaves tool_ids optional; the editor assumes it is present.
export interface AgentSkill extends GenAgentSkill {
  tool_ids: string[]
}

export interface AgentNodeConfig {
  model_id: string
  system_prompt: string
  temperature?: number | null
  tool_ids: string[]
  max_iterations: number
  retry?: GenRetryConfig | null
  prompt_ref?: string | null
  skills?: AgentSkill[]
  source_capability?: string | null
  source_version?: string | null
  track_latest?: boolean
}

export interface ConditionalNodeConfig {
  conditions: GenConditionConfig[]
  default_branch?: string | null
}

export interface TransformNodeConfig {
  mode: GenMode
  template?: string | null
  field_mappings?: GenFieldMapping[] | null
  custom_function_id?: string | null
  output_field: string
  retry?: GenRetryConfig | null
}

export interface HumanInLoopNodeConfig {
  input_fields: GenHumanInputField[]
  approval_required: boolean
  approval_message?: string | null
  timeout_seconds?: number | null
  output_fields: string[]
}

export interface CustomFunctionNodeConfig {
  code: string
  timeout_seconds: number
  input_fields: string[]
  output_fields: string[]
  retry?: GenRetryConfig | null
}

export interface InvokeNodeConfig {
  capability: string
  version: string
  input_mapping: GenFieldMapping[]
  output_field: string
  set_output: boolean
}

// Runtime-only synthetic gate created by expand(); never persisted to workflow JSON.
export interface InvokeExitNodeConfig {
  invoke_id: string
  output_field: string
  set_output: boolean
}

export type NodeConfig =
  | StartNodeConfig
  | EndNodeConfig
  | AgentNodeConfig
  | ConditionalNodeConfig
  | TransformNodeConfig
  | HumanInLoopNodeConfig
  | CustomFunctionNodeConfig
  | InvokeNodeConfig
  | InvokeExitNodeConfig

// ─── Discriminated node union (narrows `config` from `type`) ────────────────

interface BaseNode {
  id: string
  position: { x: number; y: number }
  error_handling?: boolean
}

export interface StartNode extends BaseNode {
  type: 'start'
  config: StartNodeConfig
}
export interface EndNode extends BaseNode {
  type: 'end'
  config: EndNodeConfig
}
export interface AgentNode extends BaseNode {
  type: 'agent'
  config: AgentNodeConfig
}
export interface ConditionalNode extends BaseNode {
  type: 'conditional'
  config: ConditionalNodeConfig
}
export interface TransformNode extends BaseNode {
  type: 'transform'
  config: TransformNodeConfig
}
export interface HumanInLoopNode extends BaseNode {
  type: 'human_in_loop'
  config: HumanInLoopNodeConfig
}
export interface CustomFunctionNode extends BaseNode {
  type: 'custom_function'
  config: CustomFunctionNodeConfig
}
export interface InvokeNode extends BaseNode {
  type: 'invoke'
  config: InvokeNodeConfig
}
// Runtime-only (see InvokeExitNodeConfig); kept in the union for exhaustiveness.
export interface InvokeExitNode extends BaseNode {
  type: 'invoke_exit'
  config: InvokeExitNodeConfig
}

export type WorkflowNode =
  | StartNode
  | EndNode
  | AgentNode
  | ConditionalNode
  | TransformNode
  | HumanInLoopNode
  | CustomFunctionNode
  | InvokeNode
  | InvokeExitNode

// Override: the schema makes the collections optional (Pydantic defaults), but a
// loaded workflow always carries them. The intersection keeps the generated
// fields while re-tightening these to required.
export type WorkflowDoc = GenWorkflow & {
  nodes: WorkflowNode[]
  edges: GenEdge[]
  tools: ToolDefinition[]
  models: GenModelConfig[]
  schema_version: number
}

// ─── Purely-frontend helpers (no backend counterpart) ───────────────────────

export const NODE_META: Record<GenNodeType, { label: string; color: string }> = {
  start: { label: 'Start', color: '#22c55e' },
  end: { label: 'End', color: '#ef4444' },
  agent: { label: 'Agent', color: '#8b5cf6' },
  conditional: { label: 'Conditional', color: '#f59e0b' },
  transform: { label: 'Transform', color: '#06b6d4' },
  human_in_loop: { label: 'Human Input', color: '#3b82f6' },
  custom_function: { label: 'Custom Function', color: '#ec4899' },
  invoke: { label: 'Invoke', color: '#f97316' },
  invoke_exit: { label: 'Invoke Exit', color: '#fb923c' },
}

export const ALL_NODE_TYPES: GenNodeType[] = [
  'start',
  'end',
  'agent',
  'conditional',
  'transform',
  'human_in_loop',
  'custom_function',
  'invoke',
]

/** Palette grouping — the order users build a workflow in. */
export const PALETTE_GROUPS: Array<{ label: string; types: GenNodeType[] }> = [
  { label: 'Structure', types: ['start', 'end'] },
  { label: 'Compute', types: ['agent', 'transform', 'custom_function'] },
  { label: 'Control flow', types: ['conditional', 'invoke'] },
  { label: 'Human', types: ['human_in_loop'] },
]

export function defaultConfig(type: GenNodeType): NodeConfig {
  switch (type) {
    case 'start':
      return { input_fields: [] }
    case 'end':
      return { output_fields: [] }
    case 'agent':
      return { model_id: '', system_prompt: '', tool_ids: [], max_iterations: 10 }
    case 'conditional':
      return { conditions: [] }
    case 'transform':
      return { mode: 'template', template: '', output_field: 'result' }
    case 'human_in_loop':
      return { input_fields: [], approval_required: false, output_fields: [] }
    case 'custom_function':
      return { code: '', timeout_seconds: 30, input_fields: [], output_fields: [] }
    case 'invoke':
      return { capability: '', version: 'latest', input_mapping: [], output_field: 'result', set_output: false }
    case 'invoke_exit':
      return { invoke_id: '', output_field: 'result', set_output: false }
  }
}

export interface ToolParamRow {
  key: string
  value: GenJSONSchemaParam
}
