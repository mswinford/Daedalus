import { describe, it, expect } from 'vitest'

import type { HumanInterruptField } from '@/lib/api'
import { remainingSeconds, missingRequiredFields } from './RunPanel'

describe('remainingSeconds', () => {
  it('is 0 exactly at the deadline', () => {
    expect(remainingSeconds(1000, 1000)).toBe(0)
  })

  it('clamps to 0 past the deadline', () => {
    expect(remainingSeconds(1000, 2500)).toBe(0)
  })

  it('rounds up: 1500ms remaining is 2', () => {
    expect(remainingSeconds(2500, 1000)).toBe(2)
  })

  it('1000ms remaining is 1', () => {
    expect(remainingSeconds(2000, 1000)).toBe(1)
  })

  it('1ms remaining is 1', () => {
    expect(remainingSeconds(1001, 1000)).toBe(1)
  })
})

describe('missingRequiredFields', () => {
  const fields: HumanInterruptField[] = [
    { name: 'score', label: 'Score', type: 'number', required: true },
    { name: 'note', label: 'Note', type: 'text', required: false },
    { name: 'decision', label: 'Decision', type: 'select', required: true, options: ['a', 'b'] },
  ]

  it('returns nothing when all required fields are filled', () => {
    expect(missingRequiredFields(fields, { score: '3', decision: 'a' })).toEqual([])
  })

  it('flags missing and whitespace-only required fields by label', () => {
    expect(missingRequiredFields(fields, {})).toEqual(['Score', 'Decision'])
    expect(missingRequiredFields(fields, { score: '   ', decision: 'b' })).toEqual(['Score'])
  })

  it('ignores empty optional fields', () => {
    expect(missingRequiredFields(fields, { score: '1', note: '', decision: 'b' })).toEqual([])
  })
})
