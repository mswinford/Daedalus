import { describe, it, expect } from 'vitest'

import { collectOrigins, semverCompare, compareUpdates, type CapabilityDetailLike, type OriginKind, type OriginRef } from './capabilityUpdates'
import type { Workflow } from './api'
import type { AgentNode, ModelConfig, StartNode, ToolDefinition } from './workflowTypes'

function makeTool(id: string): ToolDefinition {
  return { id, name: id, description: '', parameters: {}, implementation: { type: 'builtin', config: {} } }
}

function makeModel(id: string): ModelConfig {
  return { id, name: id, provider: 'openai_compatible', model: 'gpt-test', default_temperature: 0.7, track_cost: false }
}

function makeAgent(id: string, config: Partial<AgentNode['config']> = {}): AgentNode {
  return {
    id,
    type: 'agent',
    position: { x: 0, y: 0 },
    config: { model_id: 'm1', system_prompt: '', tool_ids: [], max_iterations: 5, ...config },
  }
}

function makeWf(overrides: Partial<Workflow> = {}): Workflow {
  return { id: 'wf1', name: 'test', schema_version: 1, nodes: [], edges: [], tools: [], models: [], ...overrides }
}

function origin(capabilityName: string, currentVersion: string | null, kind: OriginKind = 'tool', where = 'x'): OriginRef {
  return { kind, capabilityName, currentVersion, where }
}

function detail(name: string, versions: Array<[string, string]>): CapabilityDetailLike {
  return { name, versions: versions.map(([version, stage]) => ({ version, stage })) }
}

describe('collectOrigins', () => {
  it('finds all six stamped kinds with correct location descriptors', () => {
    const wf = makeWf({
      tools: [{ ...makeTool('t1'), source_capability: 'ns/t1', source_version: '1.2.3' }],
      models: [{ ...makeModel('m1'), source_capability: 'ns/m1', source_version: '0.4.0' }],
      prompts: [{ id: 'p1', name: 'p1', text: 'x', source_capability: 'ns/p1', source_version: '2.0.0' }],
      nodes: [
        makeAgent('a1', {
          source_capability: 'ns/ag',
          source_version: '3.0.0',
          skills: [{ name: 'sk', prompt: '', tool_ids: [], source_capability: 'ns/sk', source_version: '1.0.0' }],
        }),
      ],
      source_capability: 'ns/wf',
      source_version: '4.0.0',
    })
    expect(collectOrigins(wf)).toEqual([
      { kind: 'tool', capabilityName: 'ns/t1', currentVersion: '1.2.3', where: 't1' },
      { kind: 'model_profile', capabilityName: 'ns/m1', currentVersion: '0.4.0', where: 'm1' },
      { kind: 'prompt', capabilityName: 'ns/p1', currentVersion: '2.0.0', where: 'p1' },
      { kind: 'agent', capabilityName: 'ns/ag', currentVersion: '3.0.0', where: 'node:a1' },
      { kind: 'skill', capabilityName: 'ns/sk', currentVersion: '1.0.0', where: 'node:a1 skill:sk' },
      { kind: 'workflow', capabilityName: 'ns/wf', currentVersion: '4.0.0', where: 'workflow' },
    ])
  })

  it('skips unstamped entries and ignores non-agent nodes', () => {
    const wf = makeWf({
      tools: [makeTool('t1')],
      models: [makeModel('m1')],
      prompts: [{ id: 'p1', name: 'p1', text: 'x' }],
      nodes: [
        makeAgent('a1'),
        { id: 's', type: 'start', position: { x: 0, y: 0 }, config: { input_fields: [] } } as StartNode,
      ],
    })
    expect(collectOrigins(wf)).toEqual([])
  })

  it('keeps null currentVersion when only the capability is stamped', () => {
    const wf = makeWf({ tools: [{ ...makeTool('t1'), source_capability: 'ns/t1', source_version: null }] })
    expect(collectOrigins(wf)).toEqual([
      { kind: 'tool', capabilityName: 'ns/t1', currentVersion: null, where: 't1' },
    ])
  })

  it('does not dedupe — each stamped attachment is its own entry', () => {
    const wf = makeWf({
      tools: [
        { ...makeTool('t1'), source_capability: 'ns/x' },
        { ...makeTool('t2'), source_capability: 'ns/x' },
      ],
    })
    expect(collectOrigins(wf)).toHaveLength(2)
  })

  it('falls back to the skill index for unnamed skills', () => {
    const wf = makeWf({
      nodes: [makeAgent('a1', { skills: [{ name: null, prompt: '', tool_ids: [], source_capability: 'ns/sk' }] })],
    })
    expect(collectOrigins(wf)).toEqual([
      { kind: 'skill', capabilityName: 'ns/sk', currentVersion: null, where: 'node:a1 skill:0' },
    ])
  })
})

describe('semverCompare', () => {
  it('compares numerically, not lexicographically', () => {
    expect(semverCompare('1.9.0', '1.10.0')).toBeLessThan(0)
    expect(semverCompare('1.10.0', '1.9.0')).toBeGreaterThan(0)
    expect(semverCompare('2.0.0', '10.0.0')).toBeLessThan(0)
    expect(semverCompare('1.2.3', '1.2.3')).toBe(0)
  })

  it('orders prereleases per semver spec', () => {
    expect(semverCompare('1.0.0-rc.1', '1.0.0')).toBeLessThan(0)
    expect(semverCompare('1.0.0', '1.0.0-rc.1')).toBeGreaterThan(0)
    expect(semverCompare('1.0.0-rc.1', '1.0.0-rc.2')).toBeLessThan(0)
    expect(semverCompare('1.0.0-alpha', '1.0.0-beta')).toBeLessThan(0)
    expect(semverCompare('1.0.0-1', '1.0.0-alpha')).toBeLessThan(0)
    expect(semverCompare('1.0.0-rc.1', '1.0.0-rc.1.1')).toBeLessThan(0)
  })

  it('falls back to string comparison for non-semver input', () => {
    expect(semverCompare('latest', '1.0.0')).toBeGreaterThan(0)
    expect(semverCompare('abc', 'abc')).toBe(0)
  })
})

describe('compareUpdates', () => {
  it('reports an update when a newer published version exists', () => {
    const s = compareUpdates(
      [origin('ns/t', '1.2.3')],
      new Map([['ns/t', detail('ns/t', [['1.2.3', 'published'], ['1.4.0', 'published']])]]),
    )
    expect(s[0]).toMatchObject({ hasUpdate: true, latestVersion: '1.4.0', isBreaking: false })
  })

  it('reports no update when current is the newest published', () => {
    const s = compareUpdates(
      [origin('ns/t', '1.4.0')],
      new Map([['ns/t', detail('ns/t', [['1.2.0', 'published'], ['1.4.0', 'published']])]]),
    )
    expect(s[0]).toMatchObject({ hasUpdate: false, latestVersion: '1.4.0', isBreaking: false })
  })

  it('flags breaking updates on a major bump', () => {
    const s = compareUpdates(
      [origin('ns/t', '1.9.0')],
      new Map([['ns/t', detail('ns/t', [['2.0.0', 'published']])]]),
    )
    expect(s[0]).toMatchObject({ hasUpdate: true, latestVersion: '2.0.0', isBreaking: true })
  })

  it('treats an unknown current version as updatable but not breaking', () => {
    const s = compareUpdates(
      [origin('ns/t', null)],
      new Map([['ns/t', detail('ns/t', [['2.0.0', 'published']])]]),
    )
    expect(s[0]).toMatchObject({ hasUpdate: true, latestVersion: '2.0.0', isBreaking: false })
  })

  it('marks the capability missing when it is not in the registry', () => {
    const s = compareUpdates([origin('ns/gone', '1.0.0')], new Map())
    expect(s[0]).toMatchObject({ hasUpdate: false, latestVersion: null, missing: true })
  })

  it('marks the capability missing when no version is published', () => {
    const s = compareUpdates(
      [origin('ns/t', '1.0.0')],
      new Map([['ns/t', detail('ns/t', [['2.0.0', 'draft'], ['2.1.0', 'review']])]]),
    )
    expect(s[0]).toMatchObject({ hasUpdate: false, latestVersion: null, missing: true })
  })

  it('only counts published versions as the latest', () => {
    const s = compareUpdates(
      [origin('ns/t', '1.4.0')],
      new Map([['ns/t', detail('ns/t', [['2.0.0', 'draft'], ['1.5.0', 'published']])]]),
    )
    expect(s[0]).toMatchObject({ hasUpdate: true, latestVersion: '1.5.0', isBreaking: false })
  })

  it('picks the newest published version by semver order, including prereleases', () => {
    const s = compareUpdates(
      [origin('ns/t', '1.0.0-alpha')],
      new Map([['ns/t', detail('ns/t', [['1.0.0-rc.2', 'published'], ['1.0.0-rc.1', 'published']])]]),
    )
    expect(s[0]).toMatchObject({ hasUpdate: true, latestVersion: '1.0.0-rc.2', isBreaking: false })
  })

  it('emits one status per origin even for shared capabilities', () => {
    const s = compareUpdates(
      [origin('ns/t', '1.0.0', 'tool', 't1'), origin('ns/t', null, 'skill', 'node:a skill:sk')],
      new Map([['ns/t', detail('ns/t', [['1.0.0', 'published']])]]),
    )
    expect(s).toHaveLength(2)
    expect(s[0].hasUpdate).toBe(false)
    expect(s[1].hasUpdate).toBe(true)
  })
})
