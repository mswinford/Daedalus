import { describe, it, expect } from 'vitest'

import { runInputPlaceholder } from './runInput'

describe('runInputPlaceholder', () => {
  it('falls back to the generic example when no fields are declared', () => {
    expect(runInputPlaceholder([])).toBe('Leave blank to run with no input, or e.g. {"score": 40}')
  })

  it('builds a JSON skeleton from a single field', () => {
    expect(runInputPlaceholder(['query'])).toBe('{"query": ...}')
  })

  it('builds a JSON skeleton from multiple fields, in order', () => {
    expect(runInputPlaceholder(['query', 'context'])).toBe('{"query": ..., "context": ...}')
  })
})
