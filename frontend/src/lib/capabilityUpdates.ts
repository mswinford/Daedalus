import type { WorkflowDoc } from './workflowTypes'

export type OriginKind = 'tool' | 'model_profile' | 'prompt' | 'skill' | 'agent' | 'workflow'

export interface OriginRef {
  kind: OriginKind
  capabilityName: string
  currentVersion: string | null
  where: string
}

export function collectOrigins(wf: WorkflowDoc): OriginRef[] {
  const out: OriginRef[] = []
  for (const t of wf.tools) {
    if (t.source_capability) {
      out.push({ kind: 'tool', capabilityName: t.source_capability, currentVersion: t.source_version ?? null, where: t.id })
    }
  }
  for (const m of wf.models) {
    if (m.source_capability) {
      out.push({ kind: 'model_profile', capabilityName: m.source_capability, currentVersion: m.source_version ?? null, where: m.id })
    }
  }
  for (const p of wf.prompts ?? []) {
    if (p.source_capability) {
      out.push({ kind: 'prompt', capabilityName: p.source_capability, currentVersion: p.source_version ?? null, where: p.id })
    }
  }
  for (const n of wf.nodes) {
    if (n.type !== 'agent') continue
    const c = n.config
    if (c.source_capability) {
      out.push({ kind: 'agent', capabilityName: c.source_capability, currentVersion: c.source_version ?? null, where: `node:${n.id}` })
    }
    for (const [i, s] of (c.skills ?? []).entries()) {
      if (s.source_capability) {
        out.push({ kind: 'skill', capabilityName: s.source_capability, currentVersion: s.source_version ?? null, where: `node:${n.id} skill:${s.name ?? i}` })
      }
    }
  }
  if (wf.source_capability) {
    out.push({ kind: 'workflow', capabilityName: wf.source_capability, currentVersion: wf.source_version ?? null, where: 'workflow' })
  }
  return out
}

const SEMVER_RE =
  /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?$/

function parseSemver(v: string): [number, number, number, string[]] | null {
  const m = SEMVER_RE.exec(v)
  if (!m) return null
  return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] ? m[4].split('.') : []]
}

export function semverCompare(a: string, b: string): number {
  const pa = parseSemver(a)
  const pb = parseSemver(b)
  if (!pa || !pb) return a < b ? -1 : a > b ? 1 : 0
  for (let i = 0; i < 3; i++) {
    if (pa[i] !== pb[i]) return pa[i] < pb[i] ? -1 : 1
  }
  const ap = pa[3]
  const bp = pb[3]
  if (ap.length === 0 && bp.length === 0) return 0
  if (ap.length === 0) return 1
  if (bp.length === 0) return -1
  for (let i = 0; i < Math.max(ap.length, bp.length); i++) {
    const x = ap[i]
    const y = bp[i]
    if (x === undefined) return -1
    if (y === undefined) return 1
    if (x === y) continue
    const xn = /^\d+$/.test(x)
    const yn = /^\d+$/.test(y)
    if (xn && yn) return Number(x) < Number(y) ? -1 : 1
    if (xn) return -1
    if (yn) return 1
    return x < y ? -1 : 1
  }
  return 0
}

export interface CapabilityDetailLike {
  name: string
  versions: Array<{ version: string; stage: string }>
}

export interface UpdateStatus extends OriginRef {
  latestVersion: string | null
  hasUpdate: boolean
  isBreaking: boolean
  missing?: boolean
}

export function compareUpdates(origins: OriginRef[], detailByName: Map<string, CapabilityDetailLike>): UpdateStatus[] {
  return origins.map((o) => {
    const published = (detailByName.get(o.capabilityName)?.versions ?? []).filter((v) => v.stage === 'published')
    if (published.length === 0) {
      return { ...o, latestVersion: null, hasUpdate: false, isBreaking: false, missing: true }
    }
    let latest = published[0].version
    for (const v of published) {
      if (semverCompare(v.version, latest) > 0) latest = v.version
    }
    const hasUpdate = o.currentVersion == null ? true : semverCompare(latest, o.currentVersion) > 0
    let isBreaking = false
    if (hasUpdate && o.currentVersion != null) {
      const pa = parseSemver(latest)
      const pb = parseSemver(o.currentVersion)
      if (pa && pb) isBreaking = pa[0] > pb[0]
    }
    return { ...o, latestVersion: latest, hasUpdate, isBreaking }
  })
}
