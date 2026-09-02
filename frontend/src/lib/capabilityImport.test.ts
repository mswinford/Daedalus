import { describe, it, expect } from 'vitest'

import { isCapabilityPresent, applyCapability, missingSecrets } from './capabilityImport'
import type { Workflow } from './api'
import type { AgentNode, ModelConfig, ToolDefinition, StartNode } from './workflowTypes'

function makeTool(id: string): ToolDefinition {
  return { id, name: id, description: '', parameters: {}, implementation: { type: 'builtin', config: {} } }
}

function makeModel(id: string): ModelConfig {
  return { id, name: id, provider: 'openai_compatible', model: 'gpt-test', default_temperature: 0.7, track_cost: false }
}

function makeAgent(id: string, skills?: AgentNode['config']['skills']): AgentNode {
  return {
    id,
    type: 'agent',
    position: { x: 0, y: 0 },
    config: { model_id: 'm1', system_prompt: '', tool_ids: [], max_iterations: 5, skills },
  }
}

function makeWf(overrides: Partial<Workflow> = {}): Workflow {
  return { id: 'wf1', name: 'test', schema_version: 1, nodes: [], edges: [], tools: [], models: [], ...overrides }
}

describe('isCapabilityPresent', () => {
  it('matches tools by pool id (present and absent)', () => {
    const wf = makeWf({ tools: [makeTool('t1')] })
    expect(isCapabilityPresent(wf, 'tool', 't1')).toBe(true)
    expect(isCapabilityPresent(wf, 'tool', 't2')).toBe(false)
  })

  it('matches model_profile by pool id', () => {
    const wf = makeWf({ models: [makeModel('m1')] })
    expect(isCapabilityPresent(wf, 'model_profile', 'm1')).toBe(true)
    expect(isCapabilityPresent(wf, 'model_profile', 'm2')).toBe(false)
  })

  it('matches prompts by namespaced name against prompts[].id', () => {
    const wf = makeWf({ prompts: [{ id: 'base', name: 'base', text: 'x' }] })
    expect(isCapabilityPresent(wf, 'prompt', 'ns/base')).toBe(true)
    expect(isCapabilityPresent(wf, 'prompt', 'ns/other')).toBe(false)
  })

  it('matches a skill on its target agent node', () => {
    const wf = makeWf({ nodes: [makeAgent('a', [{ name: 'sk1', prompt: 'p', tool_ids: [] }])] })
    expect(isCapabilityPresent(wf, 'skill', 'sk1', 'a')).toBe(true)
  })

  it('does not match a skill on a different node, a non-agent node, or a missing node', () => {
    const wf = makeWf({
      nodes: [
        makeAgent('a', [{ name: 'sk1', prompt: 'p', tool_ids: [] }]),
        makeAgent('b'),
        { id: 's', type: 'start', position: { x: 0, y: 0 }, config: { input_fields: [] } } as StartNode,
      ],
    })
    expect(isCapabilityPresent(wf, 'skill', 'sk1', 'b')).toBe(false)
    expect(isCapabilityPresent(wf, 'skill', 'sk1', 's')).toBe(false)
    expect(isCapabilityPresent(wf, 'skill', 'sk1', 'nope')).toBe(false)
  })

  it('agent and workflow kinds are never "present"', () => {
    const wf = makeWf()
    expect(isCapabilityPresent(wf, 'agent', 'anything')).toBe(false)
    expect(isCapabilityPresent(wf, 'workflow', 'anything')).toBe(false)
  })
})

describe('applyCapability dedupe', () => {
  it('adds the same tool only once', () => {
    const t = makeTool('t1')
    const r1 = applyCapability(makeWf(), 'tool', t, 'ns/t1')
    expect(r1.added).toBe(true)
    expect(r1.wf.tools).toHaveLength(1)
    const r2 = applyCapability(r1.wf, 'tool', t, 'ns/t1')
    expect(r2.added).toBe(false)
    expect(r2.wf.tools).toHaveLength(1)
  })

  it('adds the same model_profile only once', () => {
    const m = makeModel('m1')
    const r1 = applyCapability(makeWf(), 'model_profile', m, 'ns/m1')
    expect(r1.added).toBe(true)
    expect(r1.wf.models).toHaveLength(1)
    const r2 = applyCapability(r1.wf, 'model_profile', m, 'ns/m1')
    expect(r2.added).toBe(false)
    expect(r2.wf.models).toHaveLength(1)
  })

  it('adds the same prompt only once (by name)', () => {
    const r1 = applyCapability(makeWf(), 'prompt', { text: 'hello' }, 'ns/base')
    expect(r1.added).toBe(true)
    expect(r1.wf.prompts).toHaveLength(1)
    expect(r1.wf.prompts?.[0].id).toBe('base')
    const r2 = applyCapability(r1.wf, 'prompt', { text: 'hello' }, 'ns/base')
    expect(r2.added).toBe(false)
    expect(r2.wf.prompts).toHaveLength(1)
  })

  it('adds a skill once per agent node (per-node rule)', () => {
    const wf = makeWf({ nodes: [makeAgent('a'), makeAgent('b')] })
    const art = { name: 'sk1', prompt: 'p', tools: [] }

    const r1 = applyCapability(wf, 'skill', art, 'ns/sk1', 'a')
    expect(r1.added).toBe(true)
    expect((r1.wf.nodes.find((n) => n.id === 'a') as AgentNode).config.skills).toHaveLength(1)

    const r2 = applyCapability(r1.wf, 'skill', art, 'ns/sk1', 'a')
    expect(r2.added).toBe(false)
    expect((r2.wf.nodes.find((n) => n.id === 'a') as AgentNode).config.skills).toHaveLength(1)

    const r3 = applyCapability(r1.wf, 'skill', art, 'ns/sk1', 'b')
    expect(r3.added).toBe(true)
    expect((r3.wf.nodes.find((n) => n.id === 'b') as AgentNode).config.skills).toHaveLength(1)
  })

  it('always adds a new node for kind agent, even with an identical artifact', () => {
    const art = { model: makeModel('m2'), tools: [], skills: [], prompt: 'sys' }
    const r1 = applyCapability(makeWf(), 'agent', art, 'ns/ag')
    expect(r1.added).toBe(true)
    expect(r1.wf.nodes).toHaveLength(1)
    const r2 = applyCapability(r1.wf, 'agent', art, 'ns/ag')
    expect(r2.added).toBe(true)
    expect(r2.wf.nodes).toHaveLength(2)
  })

  it('reuses existing pool tools referenced by a skill artifact', () => {
    const wf = makeWf({ nodes: [makeAgent('a')], tools: [makeTool('t1')] })
    const art = { name: 'sk', prompt: 'p', tools: [makeTool('t1')] }
    const r = applyCapability(wf, 'skill', art, 'ns/sk', 'a')
    expect(r.added).toBe(true)
    expect(r.wf.tools).toHaveLength(1)
    const skill = (r.wf.nodes.find((n) => n.id === 'a') as AgentNode).config.skills?.[0]
    expect(skill?.tool_ids).toEqual(['t1'])
  })
})

describe('applyCapability provenance stamping', () => {
  it('stamps imported tools with capability name and resolved version', () => {
    const r = applyCapability(makeWf(), 'tool', makeTool('t1'), 'ns/t1', undefined, '1.2.3')
    expect(r.added).toBe(true)
    expect(r.wf.tools[0].source_capability).toBe('ns/t1')
    expect(r.wf.tools[0].source_version).toBe('1.2.3')
  })

  it('stamps imported model profiles with capability name and resolved version', () => {
    const r = applyCapability(makeWf(), 'model_profile', makeModel('m1'), 'ns/m1', undefined, '0.4.0')
    expect(r.added).toBe(true)
    expect(r.wf.models[0].source_capability).toBe('ns/m1')
    expect(r.wf.models[0].source_version).toBe('0.4.0')
  })

  it('stamps nested tools/models carried by skill and agent artifacts', () => {
    const wf = makeWf({ nodes: [makeAgent('a')] })
    const r = applyCapability(wf, 'skill', { name: 'sk1', prompt: 'p', tools: [makeTool('t9')] }, 'ns/sk1', 'a', '2.0.0')
    expect(r.wf.tools[0].source_capability).toBe('ns/sk1')
    expect(r.wf.tools[0].source_version).toBe('2.0.0')

    const r2 = applyCapability(
      makeWf(),
      'agent',
      { model: makeModel('m9'), tools: [makeTool('t8')], skills: [], prompt: 'sys' },
      'ns/ag', undefined, '3.1.4',
    )
    expect(r2.wf.models[0].source_capability).toBe('ns/ag')
    expect(r2.wf.models[0].source_version).toBe('3.1.4')
    expect(r2.wf.tools[0].source_capability).toBe('ns/ag')
    expect(r2.wf.tools[0].source_version).toBe('3.1.4')
  })

  it('stamps the capability name even when no resolved version is provided', () => {
    const r = applyCapability(makeWf(), 'tool', makeTool('t1'), 'ns/t1')
    expect(r.wf.tools[0].source_capability).toBe('ns/t1')
    expect(r.wf.tools[0].source_version).toBeNull()
  })

  it('does not re-stamp pool entries that already exist (first provenance wins)', () => {
    const existing = makeWf({ tools: [{ ...makeTool('t1'), source_capability: 'other/t1', source_version: '9.9.9' }] })
    const r = applyCapability(existing, 'tool', makeTool('t1'), 'ns/t1', undefined, '1.0.0')
    expect(r.added).toBe(false)
    expect(r.wf.tools[0].source_capability).toBe('other/t1')
    expect(r.wf.tools[0].source_version).toBe('9.9.9')
  })

  it('leaves custom (form-created, non-imported) entries unstamped', () => {
    // Form-created tools/models are pushed straight into the pool and never pass through applyCapability.
    const wf = makeWf({ tools: [makeTool('custom-1')], models: [makeModel('custom-m')] })
    expect(wf.tools[0].source_capability).toBeUndefined()
    expect(wf.tools[0].source_version).toBeUndefined()
    expect(wf.models[0].source_capability).toBeUndefined()
    expect(wf.models[0].source_version).toBeUndefined()
  })
})

describe('missingSecrets', () => {
  it('returns declared secrets absent from the known store', () => {
    expect(missingSecrets({ secrets_required: ['GITHUB_TOKEN', 'SLACK_TOKEN'] }, ['SLACK_TOKEN'])).toEqual(['GITHUB_TOKEN'])
  })
  it('returns [] when everything is covered or nothing is declared', () => {
    expect(missingSecrets({ secrets_required: ['A'] }, ['A', 'B'])).toEqual([])
    expect(missingSecrets({}, [])).toEqual([])
    expect(missingSecrets(null, [])).toEqual([])
  })
})
