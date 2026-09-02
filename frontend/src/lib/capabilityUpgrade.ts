export function normalizeForDiff(entry: Record<string, unknown>): Record<string, unknown> {
  const clone = structuredClone(entry)
  delete clone.id
  delete clone.source_capability
  delete clone.source_version
  return clone
}

export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return false
    if (a.length !== b.length) return false
    return a.every((v, i) => deepEqual(v, b[i]))
  }
  const ka = Object.keys(a as Record<string, unknown>)
  const kb = Object.keys(b as Record<string, unknown>)
  if (ka.length !== kb.length) return false
  return ka.every(
    (k) => Object.prototype.hasOwnProperty.call(b, k) && deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]),
  )
}

export type FieldStatus = 'same' | 'local-edited' | 'upstream-changed' | 'both'

export interface FieldDiff {
  field: string
  localValue: unknown
  upstreamValue: unknown
  status: FieldStatus
  defaultChoice: 'local' | 'upstream'
}

export function computeFieldDiff(
  local: Record<string, unknown>,
  upstreamOld: Record<string, unknown> | null,
  upstreamNew: Record<string, unknown>,
): FieldDiff[] {
  const normLocal = normalizeForDiff(local)
  const normOld = upstreamOld ? normalizeForDiff(upstreamOld) : null
  const normNew = normalizeForDiff(upstreamNew)
  const fields = [...new Set([...Object.keys(normLocal), ...Object.keys(normNew)])]
  return fields.map((f) => {
    const lv = normLocal[f]
    const ov = normOld ? normOld[f] : undefined
    const nv = normNew[f]
    const localChanged = normOld ? !deepEqual(lv, ov) : true
    const upstreamChanged = normOld ? !deepEqual(ov, nv) : true
    let status: FieldStatus
    if (deepEqual(lv, nv)) status = 'same'
    else if (localChanged && upstreamChanged) status = 'both'
    else if (localChanged) status = 'local-edited'
    else status = 'upstream-changed'
    const defaultChoice: 'local' | 'upstream' = normOld && localChanged ? 'local' : 'upstream'
    return { field: f, localValue: local[f], upstreamValue: upstreamNew[f], status, defaultChoice }
  })
}

export function stripStamps(entry: Record<string, unknown>): Record<string, unknown> {
  const clone = structuredClone(entry)
  delete clone.source_capability
  delete clone.source_version
  return clone
}

/** name → stamped-free definition map (keeps `id` so apply can remap). */
export function toolMap(defs: Array<Record<string, unknown>>): Record<string, unknown> {
  const m: Record<string, unknown> = {}
  for (const t of defs) m[String(t.name)] = stripStamps(t)
  return m
}

/** Comparable view of a skill attachment: prompt text + resolved nested tools by name. */
export function skillView(skill: Record<string, unknown>, wfTools: Array<Record<string, unknown>>): Record<string, unknown> {
  const ids = (skill.tool_ids as string[] | undefined) ?? []
  const defs = ids.map((id) => wfTools.find((t) => t.id === id)).filter(Boolean) as Array<Record<string, unknown>>
  return { prompt: skill.prompt ?? '', tools: toolMap(defs) }
}

export function skillArtifactView(artifact: Record<string, any>): Record<string, unknown> {
  return { prompt: artifact.prompt ?? '', tools: toolMap((artifact.tools as Array<Record<string, unknown>>) ?? []) }
}

/** Comparable view of an agent node config: resolved model, system prompt, enabled tools, skills by name. */
export function agentView(
  config: Record<string, unknown>,
  wfModels: Array<Record<string, unknown>>,
  wfTools: Array<Record<string, unknown>>,
): Record<string, unknown> {
  const model = wfModels.find((m) => m.id === config.model_id)
  const toolIds = (config.tool_ids as string[] | undefined) ?? []
  const tools = toolMap(toolIds.map((id) => wfTools.find((t) => t.id === id)).filter(Boolean) as Array<Record<string, unknown>>)
  const skills: Record<string, unknown> = {}
  for (const s of (config.skills as Array<Record<string, unknown>> | undefined) ?? []) {
    skills[String(s.name)] = skillView(s, wfTools)
  }
  return { model: model ? stripStamps(model) : {}, system_prompt: config.system_prompt ?? '', tools, skills }
}

export function agentArtifactView(artifact: Record<string, any>): Record<string, unknown> {
  const skills: Record<string, unknown> = {}
  for (const s of (artifact.skills as Array<{ name: string; prompt: string; tools: Array<Record<string, unknown>> }>) ?? []) {
    skills[s.name] = { prompt: s.prompt, tools: toolMap(s.tools) }
  }
  return { model: stripStamps((artifact.model as Record<string, any>) ?? {}), system_prompt: artifact.prompt ?? '', tools: toolMap((artifact.tools as Array<Record<string, unknown>>) ?? []), skills }
}

/** Upsert definitions into the workflow tools pool by id (re-stamped); returns a new array. */
export function upsertTools(
  pool: Array<Record<string, unknown>>,
  defs: Array<Record<string, unknown>>,
  capName: string,
  version: string,
): Array<Record<string, unknown>> {
  const next = pool.map((t) => ({ ...t }))
  for (const d of defs) {
    const i = next.findIndex((t) => t.id === d.id)
    if (i >= 0) next[i] = { ...stripStamps(d), source_capability: capName, source_version: version }
    else next.push({ ...d, source_capability: capName, source_version: version })
  }
  return next
}

/** Upsert a model profile into the workflow models pool by id; returns new pool + the entry id. */
export function upsertModel(
  pool: Array<Record<string, unknown>>,
  model: Record<string, unknown>,
  capName: string,
  version: string,
): { pool: Array<Record<string, unknown>>; id: string } {
  const next = pool.map((m) => ({ ...m }))
  const i = next.findIndex((m) => m.id === model.id)
  if (i >= 0) next[i] = { ...stripStamps(model), source_capability: capName, source_version: version }
  else next.push({ ...model, source_capability: capName, source_version: version })
  return { pool: next, id: String(model.id) }
}

export function applyUpgradeChoices(
  local: Record<string, unknown>,
  upstreamNew: Record<string, unknown>,
  choices: Record<string, 'local' | 'upstream'>,
  capabilityName: string,
  newVersion: string,
): Record<string, unknown> {
  const result = structuredClone(local)
  for (const f of Object.keys(upstreamNew)) {
    if (Object.prototype.hasOwnProperty.call(local, f)) {
      const choice = choices[f] ?? 'upstream'
      result[f] = choice === 'local' ? local[f] : structuredClone(upstreamNew[f])
    } else {
      result[f] = structuredClone(upstreamNew[f])
    }
  }
  result.id = local.id
  result.source_capability = capabilityName
  result.source_version = newVersion
  return result
}
