import { describe, it, expect } from 'vitest'

import { nodeDisplayName, displayNamesFor } from './workflowTypes'

describe('nodeDisplayName', () => {
  it('uses the label when present', () => {
    expect(nodeDisplayName('agent', 1, 2, 'Reviewer')).toBe('Reviewer')
  })

  it('treats a whitespace-only label as unset', () => {
    expect(nodeDisplayName('agent', 2, 2, '   ')).toBe('Agent 2')
  })

  it('omits the ordinal when the type is unique', () => {
    expect(nodeDisplayName('start', 1, 1, null)).toBe('Start')
  })

  it('numbers same-type nodes by ordinal', () => {
    expect(nodeDisplayName('agent', 2, 3, undefined)).toBe('Agent 2')
  })
})

describe('displayNamesFor', () => {
  it('numbers same-type nodes in list order and leaves unique types bare', () => {
    const names = displayNamesFor([
      { id: 's', type: 'start' },
      { id: 'a1', type: 'agent' },
      { id: 'a2', type: 'agent' },
      { id: 'e', type: 'end' },
    ])
    expect(names.get('s')).toBe('Start')
    expect(names.get('a1')).toBe('Agent 1')
    expect(names.get('a2')).toBe('Agent 2')
    expect(names.get('e')).toBe('End')
  })

  it('labels win over ordinals and only count against their own type', () => {
    const names = displayNamesFor([
      { id: 'a1', type: 'agent', label: 'Reviewer' },
      { id: 'a2', type: 'agent' },
      { id: 'c1', type: 'conditional' },
    ])
    expect(names.get('a1')).toBe('Reviewer')
    expect(names.get('a2')).toBe('Agent 2')
    expect(names.get('c1')).toBe('Conditional')
  })
})
