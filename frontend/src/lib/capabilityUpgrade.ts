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
