import type { CapabilityKind } from './registryApi'
import type { Workflow } from './api'
import type { AgentNode, ModelConfig, ToolDefinition } from './workflowTypes'

export interface ApplyResult {
  wf: Workflow
  /** false when the capability was already present (nothing to add) */
  added: boolean
}

/**
 * Secrets a manifest declares but the consumer's secret store lacks.
 * `known` is the list of secret names already available (env or file).
 */
export function missingSecrets(
  manifest: { secrets_required?: string[] } | null | undefined,
  known: string[],
): string[] {
  const required = manifest?.secrets_required ?? []
  return required.filter((s) => !known.includes(s))
}

/**
 * Is this capability already present in the workflow at its attachment point?
 *
 * `key` semantics per kind:
 * - tool / model_profile: pool entry id — match `wf.tools[].id` / `wf.models[].id`
 * - prompt: full capability name — base derived via `key.split('/').pop() ?? key`, matched against `wf.prompts[].id`
 * - skill: skill name — the node at `targetNodeId` must exist and be an agent; match `(config.skills ?? []).some(s => s.name === key)`
 * - anything else (agent, workflow): false
 */
export function isCapabilityPresent(
  wf: Workflow,
  kind: CapabilityKind,
  key: string,
  targetNodeId?: string | null,
): boolean {
  switch (kind) {
    case 'tool':
      return (wf.tools ?? []).some((t) => t.id === key)
    case 'model_profile':
      return (wf.models ?? []).some((m) => m.id === key)
    case 'prompt': {
      const base = key.split('/').pop() ?? key
      return (wf.prompts ?? []).some((p) => p.id === base)
    }
    case 'skill': {
      const node = (wf.nodes ?? []).find((n) => n.id === targetNodeId)
      if (!node || node.type !== 'agent') return false
      return ((node as AgentNode).config.skills ?? []).some((s) => s.name === key)
    }
    default:
      return false
  }
}

/**
 * Merge an inlined capability artifact into a workflow doc (returns a new object).
 *
 * Presence rules — "once per (capability, attachment point)":
 * - tool / model_profile / prompt: once per workflow (pool entries referenced by id)
 * - skill: once per agent node (the same skill may serve several agents)
 * - agent: always adds a new node instance
 */
export function applyCapability(
  wf: Workflow,
  kind: CapabilityKind,
  artifact: Record<string, any>,
  capName: string,
  targetNodeId?: string,
  sourceVersion?: string | null,
): ApplyResult {
  const prompts = [...(wf.prompts ?? [])]
  const next: Workflow = {
    ...wf,
    nodes: [...wf.nodes],
    edges: [...wf.edges],
    tools: [...(wf.tools ?? [])],
    models: [...(wf.models ?? [])],
    prompts,
  }

  // Nested adds (skills/agents carrying their own tools/models) reuse existing pool entries.
  // New entries are stamped with the importing capability's provenance; existing ones are left untouched.
  const addTool = (t: ToolDefinition): string => {
    if (!next.tools.some((x) => x.id === t.id)) {
      next.tools.push({ ...t, source_capability: capName, source_version: sourceVersion ?? null })
    }
    return t.id
  }
  const addModel = (m: ModelConfig): string => {
    if (!next.models.some((x) => x.id === m.id)) {
      next.models.push({ ...m, source_capability: capName, source_version: sourceVersion ?? null })
    }
    return m.id
  }

  switch (kind) {
    case 'tool': {
      const t = artifact as ToolDefinition
      if (isCapabilityPresent(wf, 'tool', t.id)) return { wf, added: false }
      addTool(t)
      break
    }
    case 'model_profile': {
      const m = artifact as ModelConfig
      if (isCapabilityPresent(wf, 'model_profile', m.id)) return { wf, added: false }
      addModel(m)
      break
    }
    case 'prompt': {
      const base = capName.split('/').pop() ?? capName
      if (isCapabilityPresent(wf, 'prompt', capName)) return { wf, added: false }
      prompts.push({
        id: base,
        name: base,
        text: artifact.text,
        source_capability: capName,
        source_version: sourceVersion ?? null,
      })
      break
    }
    case 'skill': {
      const idx = next.nodes.findIndex((n) => n.id === targetNodeId)
      const node = idx >= 0 ? next.nodes[idx] : undefined
      if (!node || node.type !== 'agent') {
        throw new Error('Pick an agent node for the skill')
      }
      if (isCapabilityPresent(wf, 'skill', artifact.name, targetNodeId)) {
        return { wf, added: false }
      }
      const cfg = { ...(node as AgentNode).config }
      const toolIds = (artifact.tools as ToolDefinition[]).map(addTool)
      cfg.skills = [
        ...(cfg.skills ?? []),
        {
          name: artifact.name,
          prompt: artifact.prompt,
          tool_ids: toolIds,
          source_capability: capName,
          source_version: sourceVersion ?? null,
        },
      ]
      next.nodes[idx] = { ...(node as AgentNode), config: cfg }
      break
    }
    case 'agent': {
      const modelId = addModel(artifact.model as ModelConfig)
      const ownToolIds = (artifact.tools as ToolDefinition[]).map(addTool)
      const skills = (
        artifact.skills as Array<{ name: string; prompt: string; tools: ToolDefinition[] }>
      ).map((s) => ({ name: s.name, prompt: s.prompt, tool_ids: s.tools.map(addTool) }))
      next.nodes.push({
        id: crypto.randomUUID(),
        type: 'agent',
        position: { x: 80, y: 320 + (next.nodes.length % 5) * 40 },
        config: {
          model_id: modelId,
          system_prompt: artifact.prompt ?? '',
          temperature: null,
          tool_ids: ownToolIds,
          max_iterations: 5,
          prompt_ref: null,
          skills,
          source_capability: capName,
          source_version: sourceVersion ?? null,
        },
      })
      break
    }
    default:
      throw new Error(`kind '${kind}' is not importable into a workflow`)
  }
  return { wf: next, added: true }
}
