import { describe, it, expect } from 'vitest'

import {
  normalizeForDiff,
  deepEqual,
  computeFieldDiff,
  applyUpgradeChoices,
  stripStamps,
  toolMap,
  skillView,
  skillArtifactView,
  agentView,
  agentArtifactView,
  upsertTools,
  upsertModel,
  type FieldStatus,
} from './capabilityUpgrade'

function stamped(id: string, name: string, version: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return { id, source_capability: name, source_version: version, ...extra }
}

describe('normalizeForDiff', () => {
  it('strips id and both stamp fields', () => {
    const entry = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', description: 'd' })
    expect(normalizeForDiff(entry)).toEqual({ name: 't1', description: 'd' })
  })

  it('deep-copies — mutating the result does not affect the input', () => {
    const entry = stamped('t1', 'ns/t1', '1.0.0', { config: { retries: 3, nested: { deep: [1, 2] } } })
    const norm = normalizeForDiff(entry)
    ;(norm.config as any).retries = 99
    ;(norm.config as any).nested.deep.push(3)
    expect((entry.config as any).retries).toBe(3)
    expect((entry.config as any).nested.deep).toEqual([1, 2])
  })

  it('preserves nested structure and leaves other fields intact', () => {
    const entry = stamped('m1', 'ns/m1', '0.2.0', { provider: 'openai_compatible', params: { a: [null, { b: true }] } })
    expect(normalizeForDiff(entry)).toEqual({
      provider: 'openai_compatible',
      params: { a: [null, { b: true }] },
    })
  })

  it('handles entries without stamps or id', () => {
    expect(normalizeForDiff({ name: 'x' })).toEqual({ name: 'x' })
  })
})

describe('deepEqual', () => {
  it('is key-order insensitive for objects', () => {
    expect(deepEqual({ a: 1, b: { c: 2, d: 3 } }, { b: { d: 3, c: 2 }, a: 1 })).toBe(true)
    expect(deepEqual({ a: 1, b: 2 }, { b: 2, a: 1, c: undefined as unknown as number })).toBe(false)
  })

  it('compares nested arrays and objects structurally', () => {
    expect(deepEqual({ x: [[1, 2], { y: 'z' }] }, { x: [[1, 2], { y: 'z' }] })).toBe(true)
    expect(deepEqual({ x: [[1, 2], { y: 'z' }] }, { x: [[1, 3], { y: 'z' }] })).toBe(false)
    expect(deepEqual([1, [2, [3]]], [1, [2, [4]]])).toBe(false)
  })

  it('distinguishes null from objects and arrays', () => {
    expect(deepEqual(null, null)).toBe(true)
    expect(deepEqual(null, {})).toBe(false)
    expect(deepEqual([], {})).toBe(false)
    expect(deepEqual([1], { 0: 1 })).toBe(false)
  })

  it('compares primitives and booleans', () => {
    expect(deepEqual(1, 1)).toBe(true)
    expect(deepEqual(1, '1')).toBe(false)
    expect(deepEqual(true, false)).toBe(false)
    expect(deepEqual('a', 'a')).toBe(true)
  })

  it('treats missing keys as unequal when key counts differ', () => {
    expect(deepEqual({ a: 1 }, { a: 1, b: undefined })).toBe(false)
    expect(deepEqual({ a: undefined, b: 2 }, { b: 2 })).toBe(false)
  })
})

describe('computeFieldDiff', () => {
  it('marks unchanged fields as same with defaultChoice upstream', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', description: 'same' })
    const old = stamped('up-t1', 'ns/t1', '1.0.0', { name: 't1', description: 'same' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1', description: 'same' })
    expect(computeFieldDiff(local, old, neu)).toEqual([
      { field: 'name', localValue: 't1', upstreamValue: 't1', status: 'same', defaultChoice: 'upstream' },
      { field: 'description', localValue: 'same', upstreamValue: 'same', status: 'same', defaultChoice: 'upstream' },
    ])
  })

  it('marks a field local-edited when only the local value changed', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', description: 'edited by me' })
    const old = stamped('up-t1', 'ns/t1', '1.0.0', { name: 't1', description: 'original' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1', description: 'original' })
    const diff = computeFieldDiff(local, old, neu)
    expect(diff.find((d) => d.field === 'description')).toEqual({
      field: 'description',
      localValue: 'edited by me',
      upstreamValue: 'original',
      status: 'local-edited',
      defaultChoice: 'local',
    })
  })

  it('marks a field upstream-changed when only the upstream value changed', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', description: 'original' })
    const old = stamped('up-t1', 'ns/t1', '1.0.0', { name: 't1', description: 'original' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1', description: 'new upstream' })
    const diff = computeFieldDiff(local, old, neu)
    expect(diff.find((d) => d.field === 'description')).toEqual({
      field: 'description',
      localValue: 'original',
      upstreamValue: 'new upstream',
      status: 'upstream-changed',
      defaultChoice: 'upstream',
    })
  })

  it('marks a field both when local and upstream changed differently', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', description: 'my edit' })
    const old = stamped('up-t1', 'ns/t1', '1.0.0', { name: 't1', description: 'original' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1', description: 'their edit' })
    const diff = computeFieldDiff(local, old, neu)
    expect(diff.find((d) => d.field === 'description')?.status).toBe('both')
    expect(diff.find((d) => d.field === 'description')?.defaultChoice).toBe('local')
  })

  it('treats converged values (both sides changed to the same thing) as same', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { description: 'converged' })
    const old = stamped('up-t1', 'ns/t1', '1.0.0', { description: 'original' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { description: 'converged' })
    expect(computeFieldDiff(local, old, neu).find((d) => d.field === 'description')?.status).toBe('same')
  })

  it('includes a field added upstream as upstream-changed with localValue undefined', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1' })
    const old = stamped('up-t1', 'ns/t1', '1.0.0', { name: 't1' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1', added: true })
    expect(computeFieldDiff(local, old, neu).find((d) => d.field === 'added')).toEqual({
      field: 'added',
      localValue: undefined,
      upstreamValue: true,
      status: 'upstream-changed',
      defaultChoice: 'upstream',
    })
  })

  it('includes a field removed upstream as upstream-changed with upstreamValue undefined', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', gone: 'still here' })
    const old = stamped('up-t1', 'ns/t1', '1.0.0', { name: 't1', gone: 'still here' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1' })
    expect(computeFieldDiff(local, old, neu).find((d) => d.field === 'gone')).toEqual({
      field: 'gone',
      localValue: 'still here',
      upstreamValue: undefined,
      status: 'upstream-changed',
      defaultChoice: 'upstream',
    })
  })

  it('marks a locally-edited-then-removed-upstream field as both', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', gone: 'my edit' })
    const old = stamped('up-t1', 'ns/t1', '1.0.0', { name: 't1', gone: 'original' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1' })
    expect(computeFieldDiff(local, old, neu).find((d) => d.field === 'gone')).toEqual({
      field: 'gone',
      localValue: 'my edit',
      upstreamValue: undefined,
      status: 'both',
      defaultChoice: 'local',
    })
  })

  it('treats a removed-then-differently-readded field as both', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', gone: 'my value' })
    const old = stamped('up-t1', 'ns/t1', '1.0.0', { name: 't1', gone: 'original' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1', gone: 'their value' })
    expect(computeFieldDiff(local, old, neu).find((d) => d.field === 'gone')?.status).toBe('both')
  })

  it('with upstreamOld null marks differing fields both and defaultChoice upstream', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', description: 'mine' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1', description: 'theirs' })
    expect(computeFieldDiff(local, null, neu)).toEqual([
      { field: 'name', localValue: 't1', upstreamValue: 't1', status: 'same', defaultChoice: 'upstream' },
      { field: 'description', localValue: 'mine', upstreamValue: 'theirs', status: 'both', defaultChoice: 'upstream' },
    ])
  })

  it('with upstreamOld null marks matching fields same', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1' })
    expect(computeFieldDiff(local, null, neu)).toEqual([
      { field: 'name', localValue: 't1', upstreamValue: 't1', status: 'same', defaultChoice: 'upstream' },
    ])
  })

  it('does not count stamp-only differences as a local change', () => {
    const local = stamped('local-id', 'ns/t1', '2.0.0', { name: 't1', description: 'original' })
    const old = stamped('up-t1', 'ns/t1', '1.0.0', { name: 't1', description: 'original' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1', description: 'original' })
    const statuses = computeFieldDiff(local, old, neu).map((d) => d.status) as FieldStatus[]
    expect(statuses).toEqual(['same', 'same'])
  })

  it('exposes raw stamped values for display, not normalized ones', () => {
    const local = stamped('local-id', 'ns/t1', '2.0.0', { name: 't1' })
    const old = stamped('up-t1', 'ns/t1', '1.0.0', { name: 't1' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 'renamed' })
    const diff = computeFieldDiff(local, old, neu)
    expect(diff).toHaveLength(1)
    expect(diff[0].localValue).toBe('t1')
    expect(diff[0].upstreamValue).toBe('renamed')
  })
})

describe('applyUpgradeChoices', () => {
  it('preserves the local id even when upstream has a different id', () => {
    const local = stamped('my-local-id', 'ns/t1', '1.0.0', { name: 't1' })
    const neu = stamped('upstream-id', 'ns/t1', '2.0.0', { name: 't1' })
    expect(applyUpgradeChoices(local, neu, {}, 'ns/t1', '2.0.0').id).toBe('my-local-id')
  })

  it('re-stamps source_capability and source_version', () => {
    const local = stamped('t1', 'ns/old', '0.9.0', { name: 't1' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1' })
    const out = applyUpgradeChoices(local, neu, {}, 'ns/t1', '3.1.4')
    expect(out.source_capability).toBe('ns/t1')
    expect(out.source_version).toBe('3.1.4')
  })

  it('honors per-field choices in both directions', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 'local-name', description: 'local-desc' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 'upstream-name', description: 'upstream-desc' })
    const out = applyUpgradeChoices(local, neu, { name: 'local', description: 'upstream' }, 'ns/t1', '2.0.0')
    expect(out.name).toBe('local-name')
    expect(out.description).toBe('upstream-desc')
  })

  it('defaults fields missing from choices to upstream', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 'local-name', description: 'local-desc' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 'upstream-name', description: 'upstream-desc' })
    const out = applyUpgradeChoices(local, neu, {}, 'ns/t1', '2.0.0')
    expect(out.name).toBe('upstream-name')
    expect(out.description).toBe('upstream-desc')
  })

  it('keeps a field that only exists locally, ignoring choices for it', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', extra: 'mine' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1' })
    const out = applyUpgradeChoices(local, neu, { extra: 'upstream' }, 'ns/t1', '2.0.0')
    expect(out.extra).toBe('mine')
  })

  it('adds a field that only exists upstream', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1', added: { deep: [1] } })
    const out = applyUpgradeChoices(local, neu, {}, 'ns/t1', '2.0.0')
    expect(out.added).toEqual({ deep: [1] })
  })

  it('does not mutate the local input and deep-copies taken values', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', config: { retries: 1 } })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1', config: { retries: 5 } })
    const out = applyUpgradeChoices(local, neu, { config: 'upstream' }, 'ns/t1', '2.0.0')
    ;(out.config as any).retries = 99
    expect((local.config as any).retries).toBe(1)
    expect((neu.config as any).retries).toBe(5)
  })

  it('keeps unstamped local fields intact alongside the new stamps', () => {
    const local = stamped('t1', 'ns/t1', '1.0.0', { name: 't1', prompt: 'hello' })
    const neu = stamped('up-t1', 'ns/t1', '2.0.0', { name: 't1', prompt: 'changed' })
    const out = applyUpgradeChoices(local, neu, { prompt: 'local' }, 'ns/t1', '2.0.0')
    expect(out).toEqual({ id: 't1', source_capability: 'ns/t1', source_version: '2.0.0', name: 't1', prompt: 'hello' })
  })
})

describe('stripStamps / toolMap', () => {
  it('stripStamps removes only the two stamp fields and deep-copies', () => {
    const entry = { id: 't1', source_capability: 'ns/s', source_version: '1.0.0', config: { a: [1] } }
    const out = stripStamps(entry)
    expect(out).toEqual({ id: 't1', config: { a: [1] } })
    ;(out.config as any).a.push(2)
    expect((entry.config as any).a).toEqual([1])
  })

  it('toolMap keys by name and keeps the id for remapping', () => {
    const m = toolMap([
      { id: 'alpha', name: 'Alpha', source_capability: 'ns/s', config: {} },
      { id: 'beta', name: 'Beta' },
    ])
    expect(Object.keys(m).sort()).toEqual(['Alpha', 'Beta'])
    expect((m.Alpha as any).id).toBe('alpha')
    expect((m.Alpha as any).source_capability).toBeUndefined()
  })

  it('toolMap tolerates empty input', () => {
    expect(toolMap([])).toEqual({})
  })
})

describe('skillView / skillArtifactView', () => {
  const wfTools = [
    { id: 't-a', name: 'Alpha', source_capability: 'ns/s1', source_version: '1.0.0' },
    { id: 't-b', name: 'Beta' },
    { id: 'unrelated', name: 'Other' },
  ]

  it('projects a skill entry to prompt + resolved tools by name', () => {
    const v = skillView({ name: 's1', prompt: 'do things', tool_ids: ['t-b', 't-a'] }, wfTools)
    expect(v).toEqual({
      prompt: 'do things',
      tools: { Alpha: { id: 't-a', name: 'Alpha' }, Beta: { id: 't-b', name: 'Beta' } },
    })
  })

  it('ignores tool ids that no longer resolve in the pool', () => {
    const v = skillView({ prompt: '', tool_ids: ['t-a', 'ghost'] }, wfTools)
    expect(Object.keys((v.tools as object))).toEqual(['Alpha'])
  })

  it('artifact view matches entry view when nothing was edited locally', () => {
    const artifact = { name: 's1', prompt: 'do things', tools: [{ id: 't-a', name: 'Alpha' }, { id: 't-b', name: 'Beta' }] }
    const local = skillView({ name: 's1', prompt: 'do things', tool_ids: ['t-a', 't-b'] }, wfTools)
    expect(deepEqual(local, skillArtifactView(artifact))).toBe(true)
  })

  it('local edits to a nested tool surface as a difference', () => {
    const edited = wfTools.map((t) => (t.id === 't-a' ? { ...t, description: 'I changed this' } : t))
    const local = skillView({ prompt: '', tool_ids: ['t-a'] }, edited)
    const upstream = skillArtifactView({ name: 's1', prompt: '', tools: [{ id: 't-a', name: 'Alpha' }] })
    expect(deepEqual(local, upstream)).toBe(false)
  })
})

describe('agentView / agentArtifactView', () => {
  const wfModels = [{ id: 'm-1', name: 'GPT', provider: 'openai_compatible', source_capability: 'ns/a' }]
  const wfTools = [{ id: 't-a', name: 'Alpha' }, { id: 't-s', name: 'SkillTool' }]

  it('projects config to model / system_prompt / tools / skills-by-name', () => {
    const cfg = {
      model_id: 'm-1',
      system_prompt: 'be nice',
      tool_ids: ['t-a'],
      skills: [{ name: 'sk', prompt: 'skill body', tool_ids: ['t-s'] }],
    }
    const v = agentView(cfg, wfModels, wfTools)
    expect(v.model).toEqual({ id: 'm-1', name: 'GPT', provider: 'openai_compatible' })
    expect(v.system_prompt).toBe('be nice')
    expect(v.tools).toEqual({ Alpha: { id: 't-a', name: 'Alpha' } })
    expect(v.skills).toEqual({ sk: { prompt: 'skill body', tools: { SkillTool: { id: 't-s', name: 'SkillTool' } } } })
  })

  it('artifact view matches config view when nothing was edited locally', () => {
    const artifact = {
      model: { id: 'm-1', name: 'GPT', provider: 'openai_compatible' },
      prompt: 'be nice',
      tools: [{ id: 't-a', name: 'Alpha' }],
      skills: [{ name: 'sk', prompt: 'skill body', tools: [{ id: 't-s', name: 'SkillTool' }] }],
    }
    const cfg = { model_id: 'm-1', system_prompt: 'be nice', tool_ids: ['t-a'], skills: [{ name: 'sk', prompt: 'skill body', tool_ids: ['t-s'] }] }
    expect(deepEqual(agentView(cfg, wfModels, wfTools), agentArtifactView(artifact))).toBe(true)
  })

  it('empty model pool projects an empty model object', () => {
    const v = agentView({ model_id: 'missing', system_prompt: '', tool_ids: [], skills: [] }, [], [])
    expect(v.model).toEqual({})
  })
})

describe('upsertTools / upsertModel', () => {
  it('updates existing pool entries in place and re-stamps them', () => {
    const pool = [{ id: 't-a', name: 'Alpha', description: 'old', source_capability: 'ns/old', source_version: '1.0.0' }]
    const out = upsertTools(pool, [{ id: 't-a', name: 'Alpha', description: 'new' }], 'ns/new', '2.0.0')
    expect(out).toEqual([{ id: 't-a', name: 'Alpha', description: 'new', source_capability: 'ns/new', source_version: '2.0.0' }])
  })

  it('adds missing definitions and leaves others untouched', () => {
    const pool = [{ id: 't-a', name: 'Alpha' }]
    const out = upsertTools(pool, [{ id: 't-b', name: 'Beta' }], 'ns/new', '2.0.0')
    expect(out).toHaveLength(2)
    expect(out[0]).toEqual({ id: 't-a', name: 'Alpha' })
    expect(out[1]).toEqual({ id: 't-b', name: 'Beta', source_capability: 'ns/new', source_version: '2.0.0' })
  })

  it('does not mutate the input pool', () => {
    const pool = [{ id: 't-a', name: 'Alpha' }]
    upsertTools(pool, [{ id: 't-b', name: 'Beta' }], 'ns/new', '2.0.0')
    expect(pool).toHaveLength(1)
  })

  it('upsertModel returns the entry id and re-stamps existing entries', () => {
    const pool = [{ id: 'm-1', name: 'GPT', source_version: '1.0.0' }]
    const r = upsertModel(pool, { id: 'm-1', name: 'GPT-2' }, 'ns/a', '3.0.0')
    expect(r.id).toBe('m-1')
    expect(r.pool[0]).toEqual({ id: 'm-1', name: 'GPT-2', source_capability: 'ns/a', source_version: '3.0.0' })
  })

  it('upsertModel appends unknown models', () => {
    const r = upsertModel([], { id: 'm-9', name: 'New' }, 'ns/a', '1.0.0')
    expect(r.id).toBe('m-9')
    expect(r.pool).toEqual([{ id: 'm-9', name: 'New', source_capability: 'ns/a', source_version: '1.0.0' }])
  })
})
