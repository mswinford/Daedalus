import type { CapabilityEvaluationRef } from './registryApi'

/** Pull the merged runtime evaluation off a (loosely typed) manifest. */
export function getManifestEvaluation(
  manifest: Record<string, any> | null | undefined,
): CapabilityEvaluationRef | null {
  return (manifest?.evaluation ?? null) as CapabilityEvaluationRef | null
}

/** 0.6667 -> "66.7%". Null/undefined/NaN -> null. */
export function formatScorePercent(score: number | null | undefined): string | null {
  if (score == null || Number.isNaN(score)) return null
  return `${(score * 100).toFixed(1)}%`
}

/** 850 -> "850ms", 1200 -> "1.2s", 184000 -> "3m 4s". Null/undefined/NaN -> null. */
export function formatDurationMs(ms: number | null | undefined): string | null {
  if (ms == null || Number.isNaN(ms)) return null
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1).replace(/\.0$/, '')}s`
  let minutes = Math.floor(ms / 60_000)
  let seconds = Math.round((ms % 60_000) / 1000)
  if (seconds >= 60) {
    minutes += 1
    seconds -= 60
  }
  return `${minutes}m ${seconds}s`
}

/** 0.0042 -> "$0.0042", 0.5 -> "$0.50", 0 -> "$0". Null/undefined/NaN -> null. */
export function formatCostUsd(cost: number | null | undefined): string | null {
  if (cost == null || Number.isNaN(cost)) return null
  if (cost === 0) return '$0'
  let s = cost.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
  const dot = s.indexOf('.')
  if (dot !== -1 && s.length - dot - 1 < 2) s += '0'
  return `$${s}`
}
