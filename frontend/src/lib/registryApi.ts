import axios from 'axios'

const registry = axios.create({
  baseURL: '/registry',
})

export type CapabilityKind =
  | 'tool'
  | 'prompt'
  | 'model_profile'
  | 'skill'
  | 'agent'
  | 'workflow'

export const CAPABILITY_KINDS: CapabilityKind[] = [
  'tool',
  'prompt',
  'model_profile',
  'skill',
  'agent',
  'workflow',
]

export interface CapabilitySummary {
  name: string
  kind: CapabilityKind
  description?: string | null
  tags: string[]
  spec?: Record<string, any>
  latest_published: string | null
  newest_version: string
  version_count: number
}

export interface CapabilityVersionInfo {
  name: string
  version: string
  kind: CapabilityKind
  stage: string
  security_status: string
  source_commit?: string | null
  created_at: number
  manifest: Record<string, any>
}

export interface SearchHit {
  name: string
  version: string
  kind: CapabilityKind
  description?: string | null
  spec?: Record<string, any>
  score: number
  rank: number
}

export interface UseResult {
  version: string
  manifest: Record<string, any>
  artifact: Record<string, any>
}

/** Aggregated production run metrics for a capability version (mirrors schema.capability.CapabilityEvaluationStats). */
export interface CapabilityEvaluationStats {
  runs_total: number
  runs_failed: number
  duration_ms_p50?: number | null
  duration_ms_p95?: number | null
  avg_cost_usd?: number | null
}

/** Runtime evaluation merged into every manifest dump (mirrors schema.capability.CapabilityEvaluationRef). */
export interface CapabilityEvaluationRef {
  suite_id?: string | null
  last_scored_at?: number | null
  score?: number | null
  stats?: CapabilityEvaluationStats | null
}

export const capabilitiesApi = {
  list: (kind?: CapabilityKind) =>
    registry
      .get<{ capabilities: CapabilitySummary[] }>('/capabilities', {
        params: kind ? { kind } : undefined,
      })
      .then((r) => r.data.capabilities),
  detail: (name: string) =>
    registry
      .get<{ name: string; versions: CapabilityVersionInfo[] }>(`/capabilities/${name}`)
      .then((r) => r.data),
  search: (q: string, kind?: CapabilityKind) =>
    registry
      .get<{ results: SearchHit[] }>('/search', {
        params: { q, ...(kind ? { kind } : {}) },
      })
      .then((r) => r.data.results),
  use: (name: string, version = 'latest', inline = false) =>
    registry
      .get<UseResult>(`/capabilities/${name}/use`, { params: { version, inline } })
      .then((r) => r.data),
}
