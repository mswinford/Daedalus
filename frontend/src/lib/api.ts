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

export interface WorkflowRun {
  id: string
  workflow_id: string
  status: string
  input_data: Record<string, any>
  output_data?: Record<string, any>
  error?: string
  started_at?: number
  completed_at?: number
  events: any[]
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

export const workflowsApi = {
  list: () => api.get<WorkflowSummary[]>('/workflows').then(r => r.data),
  get: (id: string) => api.get<Workflow>(`/workflows/${id}`).then(r => r.data),
  create: (data: Partial<Workflow>) => api.post<Workflow>('/workflows', data).then(r => r.data),
  update: (id: string, data: Workflow) => api.put<Workflow>(`/workflows/${id}`, data).then(r => r.data),
  delete: (id: string) => api.delete(`/workflows/${id}`),
  run: (id: string, input: Record<string, any> = {}) =>
    api.post<WorkflowRun>(`/workflows/${id}/run`, input).then(r => r.data),
  validate: (id: string, body?: Workflow) =>
    api.post<ValidationResult>(`/workflows/${id}/validate`, body ?? null).then(r => r.data),
}
