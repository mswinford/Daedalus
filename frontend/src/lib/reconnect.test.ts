import { describe, it, expect } from 'vitest'

import { reconnectDelay } from './api'

describe('reconnectDelay', () => {
  it('attempt 0 is 500ms', () => {
    expect(reconnectDelay(0)).toBe(500)
  })

  it('doubles per attempt: 1000, 2000, 4000', () => {
    expect(reconnectDelay(1)).toBe(1000)
    expect(reconnectDelay(2)).toBe(2000)
    expect(reconnectDelay(3)).toBe(4000)
  })

  it('caps at 8000ms from attempt 4 on', () => {
    expect(reconnectDelay(4)).toBe(8000)
    expect(reconnectDelay(6)).toBe(8000)
  })
})
