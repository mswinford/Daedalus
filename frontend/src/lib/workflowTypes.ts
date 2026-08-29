// TypeScript mirror of backend/schema/models.py (the JSON persistence contract).
// Keep these in sync when the Pydantic models change.

export type NodeType =
  | 'start'
  | 'end'
  | 'agent'
  | 'conditional'
  | 'transform'
  | 'human_in_loop'
  | 'custom_function'

export type EdgeType = 'static' | 'conditional' | 'error'

export type ConditionType = 'json_path' | 'regex' | 'llm'

export interface ConditionConfig {
  type: ConditionType
  expression: string
  description?: string | null
}

export interface RetryConfig {
  enabled: boolean
  max_retries: number
  backoff_base: number
  retry_on: string[]
}

export interface StartNodeConfig {
  input_fields: string[]
}

export interface EndNodeConfig {
  output_fields: string[]
}

export interface AgentNodeConfig {
  model_id: string
  system_prompt: string
  temperature?: number | null
  tool_ids: string[]
  max_iterations: number
  retry?: RetryConfig | null
}

export interface ConditionalNodeConfig {
  conditions: ConditionConfig[]
  default_branch?: string | null
}

export interface FieldMapping {
  source: string
  target: string
  transform?: string | null
}

export type TransformMode = 'template' | 'mapping' | 'custom_function'

export interface TransformNodeConfig {
  mode: TransformMode
  template?: string | null
  field_mappings?: FieldMapping[] | null
  custom_function_id?: string | null
  output_field: string
  retry?: RetryConfig | null
}

export interface HumanInputField {
  name: string
  label: string
  type: 'text' | 'textarea' | 'select' | 'boolean'
  required: boolean
  options?: string[] | null
}

export interface HumanInLoopNodeConfig {
  input_fields: HumanInputField[]
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
  retry?: RetryConfig | null
}

export type NodeConfig =
  | StartNodeConfig
  | EndNodeConfig
  | AgentNodeConfig
  | ConditionalNodeConfig
  | TransformNodeConfig
  | HumanInLoopNodeConfig
  | CustomFunctionNodeConfig

// ─── Discriminated node union (narrows `config` from `type`) ────────────────

interface BaseNode {
  id: string
  position: { x: number; y: number }
}

export interface StartNode extends BaseNode { type: 'start'; config: StartNodeConfig }
export interface EndNode extends BaseNode { type: 'end'; config: EndNodeConfig }
export interface AgentNode extends BaseNode { type: 'agent'; config: AgentNodeConfig }
export interface ConditionalNode extends BaseNode { type: 'conditional'; config: ConditionalNodeConfig }
export interface TransformNode extends BaseNode { type: 'transform'; config: TransformNodeConfig }
export interface HumanInLoopNode extends BaseNode { type: 'human_in_loop'; config: HumanInLoopNodeConfig }
export interface CustomFunctionNode extends BaseNode { type: 'custom_function'; config: CustomFunctionNodeConfig }

export type WorkflowNode =
  | StartNode
  | EndNode
  | AgentNode
  | ConditionalNode
  | TransformNode
  | HumanInLoopNode
  | CustomFunctionNode

export interface WorkflowEdge {
  id: string
  source_node_id: string
  source_handle: string
  target_node_id: string
  type: EdgeType
  condition?: ConditionConfig | null
}

export interface ModelConfig {
  id: string
  name: string
  provider: 'openai_compatible' | 'anthropic'
  model: string
  base_url?: string | null
  api_key_ref?: string | null
  default_temperature: number
  track_cost: boolean
  pricing?: Record<string, number> | null
}

export type ToolImplementationType = 'builtin' | 'custom_function' | 'http'

export interface JsonSchemaParam {
  type: 'string' | 'number' | 'boolean' | 'array' | 'object'
  description?: string | null
  required: boolean
  enum?: string[] | null
}

export interface ToolDefinition {
  id: string
  name: string
  description: string
  parameters: Record<string, JsonSchemaParam>
  implementation: { type: ToolImplementationType; config: Record<string, unknown> }
}

// A single row in the tool parameter editor (keyed by param name elsewhere).
export interface ToolParamRow {
  key: string
  value: JsonSchemaParam
}

export interface StateField {
  name: string
  type: 'string' | 'number' | 'boolean' | 'array' | 'object'
  description?: string | null
  required: boolean
  items_type?: string | null
}

export interface WorkflowDoc {
  id: string
  name: string
  description?: string | null
  schema_version: number
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  tools: ToolDefinition[]
  models: ModelConfig[]
  state_schema?: { fields: StateField[] } | null
}

// Human-readable labels + accent colors for the palette / node cards.
export const NODE_META: Record<NodeType, { label: string; color: string }> = {
  start: { label: 'Start', color: '#10b981' },
  end: { label: 'End', color: '#f43f5e' },
  agent: { label: 'Agent', color: '#6366f1' },
  conditional: { label: 'Conditional', color: '#f59e0b' },
  transform: { label: 'Transform', color: '#0ea5e9' },
  human_in_loop: { label: 'Human in loop', color: '#a855f7' },
  custom_function: { label: 'Custom Function', color: '#14b8a6' },
}

export const ALL_NODE_TYPES: NodeType[] = [
  'start',
  'end',
  'agent',
  'conditional',
  'transform',
  'human_in_loop',
  'custom_function',
]

// Returns a sensible empty config for a newly created node of the given type.
export function defaultConfig(type: NodeType): NodeConfig {
  switch (type) {
    case 'start':
      return { input_fields: [] }
    case 'end':
      return { output_fields: [] }
    case 'agent':
      return { model_id: '', system_prompt: '', temperature: null, tool_ids: [], max_iterations: 5 }
    case 'conditional':
      return { conditions: [], default_branch: null }
    case 'transform':
      return { mode: 'template', template: '', field_mappings: null, custom_function_id: null, output_field: '' }
    case 'human_in_loop':
      return { input_fields: [], approval_required: true, approval_message: null, timeout_seconds: null, output_fields: [] }
    case 'custom_function':
      return { code: '', timeout_seconds: 10, input_fields: [], output_fields: [] }
  }
}
