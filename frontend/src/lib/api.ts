import axios from 'axios'

import {
  type WorkflowNode,
  type WorkflowEdge,
  type ToolDefinition,
  type ModelConfig,
  type StateField,
} from './workflowTypes'

const api = axios.create({
  baseURL: '/api',
})

export interface WorkflowSummary {
  id: string
  name: string
  description?: string
}

export interface Workflow {
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

export type RunEventType =
  | 'run_start'
  | 'run_end'
  | 'node_start'
  | 'node_end'
  | 'node_error'
  | 'llm_call'
  | 'llm_token'
  | 'tool_call'
  | 'tool_result'
  | 'human_request'
  | 'human_respond'
  | 'retry'

export interface RunEvent {
  timestamp: number
  type: RunEventType
  node_id?: string | null
  data: Record<string, any>
  /** Monotonic per-run sequence number (added by the streaming backend). */
  seq?: number
}

export interface RunStartResponse {
  run_id: string
}

export interface WorkflowRun {
  id: string
  workflow_id: string
  status: string
  input_data: Record<string, any>
  output_data?: Record<string, any>
  error?: string
  started_at?: number
  completed_at?: number
  events: RunEvent[]
  total_tokens_input: number
  total_tokens_output: number
  estimated_cost_usd: number
}

export interface ValidationIssue {
  level: 'error' | 'warning'
  code: string
  message: string
  node_id?: string
  edge_id?: string
}

export interface ValidationResult {
  valid: boolean
  errors: ValidationIssue[]
  warnings: ValidationIssue[]
}

export interface SecretInfo {
  name: string
  source: 'env' | 'file'
  set: boolean
}

export const secretsApi = {
  list: () => api.get<SecretInfo[]>('/secrets').then(r => r.data),
  upsert: (name: string, value: string) =>
    api.put('/secrets', { name, value }).then(r => r.data),
  remove: (name: string) => api.delete(`/secrets/${name}`),
}

export const workflowsApi = {
  list: () => api.get<WorkflowSummary[]>('/workflows').then(r => r.data),
  get: (id: string) => api.get<Workflow>(`/workflows/${id}`).then(r => r.data),
  create: (data: Partial<Workflow>) => api.post<Workflow>('/workflows', data).then(r => r.data),
  update: (id: string, data: Workflow) => api.put<Workflow>(`/workflows/${id}`, data).then(r => r.data),
  delete: (id: string) => api.delete(`/workflows/${id}`),
  run: (id: string, input: Record<string, any> = {}) =>
    api.post<RunStartResponse>(`/workflows/${id}/run`, input).then(r => r.data),
  getRun: (runId: string) => api.get<WorkflowRun>(`/runs/${runId}`).then(r => r.data),
  validate: (id: string, body?: Workflow) =>
    api.post<ValidationResult>(`/workflows/${id}/validate`, body ?? null).then(r => r.data),
}

/**
 * Subscribe to a run's live event stream over WebSocket. The server replays any
 * events already emitted, then streams new ones until the run finishes. Returns a
 * `close()` function; `onClose` fires when the socket closes (run done or dropped).
 */
export function streamRunEvents(
  runId: string,
  onEvent: (ev: RunEvent) => void,
  onClose?: () => void,
): () => void {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${window.location.host}/api/runs/${runId}/events`)
  ws.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data) as RunEvent)
    } catch {
      // ignore malformed frames
    }
  }
  ws.onclose = () => onClose?.()
  return () => ws.close()
}
