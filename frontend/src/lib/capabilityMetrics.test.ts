import { describe, it, expect } from 'vitest'

import {
  getManifestEvaluation,
  formatScorePercent,
  formatDurationMs,
  formatCostUsd,
} from './capabilityMetrics'

describe('formatScorePercent', () => {
  it('formats a fractional score to one-decimal percent', () => {
    expect(formatScorePercent(0.6667)).toBe('66.7%')
    expect(formatScorePercent(0.12345)).toBe('12.3%')
  })

  it('handles score 0 and 1', () => {
    expect(formatScorePercent(0)).toBe('0.0%')
    expect(formatScorePercent(1)).toBe('100.0%')
  })

  it('returns null for null/undefined/NaN', () => {
    expect(formatScorePercent(null)).toBeNull()
    expect(formatScorePercent(undefined)).toBeNull()
    expect(formatScorePercent(Number.NaN)).toBeNull()
  })
})

describe('formatDurationMs', () => {
  it('keeps sub-second durations in ms', () => {
    expect(formatDurationMs(0)).toBe('0ms')
    expect(formatDurationMs(850)).toBe('850ms')
    expect(formatDurationMs(999.4)).toBe('999ms')
  })

  it('shows seconds with one decimal, trimming a bare .0', () => {
    expect(formatDurationMs(1000)).toBe('1s')
    expect(formatDurationMs(1200)).toBe('1.2s')
    expect(formatDurationMs(59_000)).toBe('59s')
  })

  it('shows multi-minute durations as m/s', () => {
    expect(formatDurationMs(60_000)).toBe('1m 0s')
    expect(formatDurationMs(184_000)).toBe('3m 4s')
    expect(formatDurationMs(3_599_999)).toBe('60m 0s')
  })

  it('returns null for null/undefined/NaN', () => {
    expect(formatDurationMs(null)).toBeNull()
    expect(formatDurationMs(undefined)).toBeNull()
    expect(formatDurationMs(Number.NaN)).toBeNull()
  })
})

describe('formatCostUsd', () => {
  it('formats small costs to up to 4 decimals', () => {
    expect(formatCostUsd(0.0042)).toBe('$0.0042')
    expect(formatCostUsd(1.23456)).toBe('$1.2346')
  })

  it('trims trailing zeros but keeps at least two decimals', () => {
    expect(formatCostUsd(0.5)).toBe('$0.50')
    expect(formatCostUsd(10)).toBe('$10')
    expect(formatCostUsd(2)).toBe('$2')
  })

  it('handles zero cost', () => {
    expect(formatCostUsd(0)).toBe('$0')
  })

  it('returns null for null/undefined/NaN', () => {
    expect(formatCostUsd(null)).toBeNull()
    expect(formatCostUsd(undefined)).toBeNull()
    expect(formatCostUsd(Number.NaN)).toBeNull()
  })
})

describe('getManifestEvaluation', () => {
  it('returns the evaluation object when present', () => {
    const ev = { score: 0.5, stats: { runs_total: 1, runs_failed: 0 } }
    expect(getManifestEvaluation({ evaluation: ev })).toEqual(ev)
  })

  it('returns null for a missing manifest or null/absent evaluation', () => {
    expect(getManifestEvaluation(null)).toBeNull()
    expect(getManifestEvaluation(undefined)).toBeNull()
    expect(getManifestEvaluation({})).toBeNull()
    expect(getManifestEvaluation({ evaluation: null })).toBeNull()
  })
})
