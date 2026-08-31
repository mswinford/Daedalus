import { describe, it, expect } from 'vitest'

import { remainingSeconds } from './RunPanel'

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
