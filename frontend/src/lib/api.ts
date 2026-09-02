import axios from 'axios'

import {
  type RunEvent as GenRunEvent,
  type WorkflowRun as GenWorkflowRun,
} from './workflowTypes.generated'
import type { WorkflowDoc as Workflow } from './workflowTypes'

const api = axios.create({
  baseURL: '/api',
})

/** Extract a human-readable message from an API error (axios or plain). */
export function apiErrorMessage(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const detail = e.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d: any) => (typeof d === 'string' ? d : d?.msg))
        .filter((m): m is string => typeof m === 'string' && m.length > 0)
      if (msgs.length > 0) return msgs.join('; ')
    }
    return e.message
  }
  if (e instanceof Error && e.message) return e.message
  return 'Something went wrong'
}

export interface WorkflowSummary {
  id: string
  name: string
  description?: string
}

// Workflow doc as persisted by the backend (generated types, collections re-tightened to required).
export type { WorkflowDoc as Workflow } from './workflowTypes'

export type { EventType as RunEventType } from './workflowTypes.generated'

// Generated run event + frontend-only fields: `seq` is added by the streaming
// backend, and `data` is read without null-checks in the run panel.
export type RunEvent = GenRunEvent & {
  /** Monotonic per-run sequence number (added by the streaming backend). */
  seq?: number
  data: Record<string, any>
}

export interface RunStartResponse {
  run_id: string
}

// Not in the Pydantic schemas — runtime-only shapes returned by /runs endpoints.
export interface HumanInterruptField {
  name: string
  label: string
  type: 'text' | 'number' | 'boolean' | 'select'
  required: boolean
  options?: string[] | null
}

export interface HumanInterruptValue {
  node_id: string
  message: string
  fields: HumanInterruptField[]
  approval_required: boolean
  /** Set when the run auto-fails if no input arrives within this many seconds. */
  timeout_seconds?: number | null
  /** Unix timestamp (seconds) when the human input was requested. */
  requested_at?: number
}

// Generated run record + runtime-only `interrupt_value`, and fields the UI reads
// without null-checks (required in every response the backend actually sends).
export type WorkflowRun = GenWorkflowRun & {
  interrupt_value?: HumanInterruptValue
  input_data: Record<string, any>
  output_data?: Record<string, any>
  events: RunEvent[]
  total_tokens_input: number
  total_tokens_output: number
  estimated_cost_usd: number
}

export interface PausedRunSummary {
  id: string
  workflow_id: string
  node_id?: string | null
  message?: string | null
  requested_at?: number | null
  timeout_seconds?: number | null
  started_at?: number
}

export interface TemplateSummary {
  id: string
  name: string
  description?: string | null
}

export const templatesApi = {
  list: () => api.get<TemplateSummary[]>('/templates').then(r => r.data),
  get: (id: string) => api.get<Workflow>(`/templates/${id}`).then(r => r.data),
}

/** Fetch a bundled template and create a new workflow from it (fresh id, keeps the template's name). */
export async function instantiateTemplate(templateId: string): Promise<Workflow> {
  const tpl = await templatesApi.get(templateId)
  return workflowsApi.create({ ...tpl, id: `workflow_${Date.now()}` })
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
  listPausedRuns: () => api.get<PausedRunSummary[]>('/runs/paused').then(r => r.data),
  resumeRun: (runId: string, humanInput: Record<string, any>) =>
    api.post(`/runs/${runId}/resume`, humanInput).then(r => r.data),
  validate: (id: string, body?: Workflow) =>
    api.post<ValidationResult>(`/workflows/${id}/validate`, body ?? null).then(r => r.data),
}

/** Backoff delay (ms) before reconnect attempt N: 500, 1000, 2000, 4000, capped at 8000. */
export function reconnectDelay(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 8000)
}

const MAX_RECONNECT_ATTEMPTS = 5

/**
 * Subscribe to a run's live event stream over WebSocket. The server replays the
 * full event log from seq 0 on every connect, so reconnecting is safe and
 * self-healing (the caller's seq dedupe skips replayed events). On an
 * unexpected close the socket re-subscribes with bounded exponential backoff
 * (reconnectDelay, up to MAX_RECONNECT_ATTEMPTS); after that `onClose` fires.
 * Returns a `close()` function that stops reconnecting and closes the socket.
 */
export function streamRunEvents(
  runId: string,
  onEvent: (ev: RunEvent) => void,
  onClose?: () => void,
): () => void {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const url = `${proto}://${window.location.host}/api/runs/${runId}/events`
  let ws: WebSocket | null = null
  let closed = false
  let attempts = 0
  let timer: ReturnType<typeof setTimeout> | undefined

  const connect = () => {
    const socket = new WebSocket(url)
    ws = socket
    socket.onmessage = (msg) => {
      attempts = 0 // a live connection ends the failure episode; backoff restarts from 500ms
      try {
        onEvent(JSON.parse(msg.data) as RunEvent)
      } catch {
        // ignore malformed frames
      }
    }
    // no-op: per spec, error is always followed by close, which drives the retry
    socket.onerror = () => {}
    socket.onclose = () => {
      if (closed) return
      if (attempts < MAX_RECONNECT_ATTEMPTS) {
        timer = setTimeout(connect, reconnectDelay(attempts))
        attempts += 1
      } else {
        onClose?.()
      }
    }
  }

  connect()

  return () => {
    closed = true
    if (timer !== undefined) clearTimeout(timer)
    ws?.close()
  }
}
