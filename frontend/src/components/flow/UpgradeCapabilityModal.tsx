import { useEffect, useState } from 'react'
import { AlertTriangle, Loader2, X } from 'lucide-react'

import { capabilitiesApi } from '@/lib/registryApi'
import { apiErrorMessage } from '@/lib/api'
import type { UpdateStatus } from '@/lib/capabilityUpdates'
import { applyUpgradeChoices, computeFieldDiff, type FieldDiff } from '@/lib/capabilityUpgrade'

export interface UpgradeViews {
  local: Record<string, unknown>
  old: Record<string, unknown> | null
  new: Record<string, unknown>
}

interface Props {
  status: UpdateStatus
  localEntry: Record<string, unknown>
  /** Composite kinds (skill/agent): project raw artifacts + local state into comparable views. When absent, pool-entry mode. */
  project?: (oldArtifact: Record<string, any> | null, newArtifact: Record<string, any>) => UpgradeViews
  runWarning?: string | null
  onClose(): void
  onApply(upgraded: Record<string, unknown>, choices: Record<string, 'local' | 'upstream'>): Promise<void>
}

function valueText(v: unknown): string {
  if (v === undefined || v === null) return '—'
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s.length > 120 ? `${s.slice(0, 120)}…` : s
}

function buildUpstreamObj(kind: UpdateStatus['kind'], artifact: Record<string, any>, localEntry: Record<string, unknown>): Record<string, unknown> {
  if (kind === 'prompt') {
    const obj: Record<string, unknown> = { text: artifact.text }
    if ('variables' in localEntry) obj.variables = artifact.variables ?? []
    return obj
  }
  return artifact
}

export default function UpgradeCapabilityModal({ status, localEntry, project, runWarning, onClose, onApply }: Props) {
  const [attempt, setAttempt] = useState(0)
  const [phase, setPhase] = useState<'loading' | 'ready' | 'error'>('loading')
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [diff, setDiff] = useState<FieldDiff[]>([])
  const [localView, setLocalView] = useState<Record<string, unknown>>(localEntry)
  const [upstreamNew, setUpstreamNew] = useState<Record<string, unknown> | null>(null)
  const [choices, setChoices] = useState<Record<string, 'local' | 'upstream'>>({})
  const [breakingAck, setBreakingAck] = useState(false)
  const [runAck, setRunAck] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    let cancelled = false
    setPhase('loading')
    setFetchError(null)
    ;(async () => {
      try {
        const newArtifact = await capabilitiesApi.use(status.capabilityName, status.latestVersion!, true)
        const oldArtifact = status.currentVersion != null ? await capabilitiesApi.use(status.capabilityName, status.currentVersion, true) : null
        if (cancelled) return
        let local: Record<string, unknown>
        let old: Record<string, unknown> | null
        let fresh: Record<string, unknown>
        if (project) {
          const v = project(oldArtifact?.artifact ?? null, newArtifact.artifact)
          ;({ local, old, new: fresh } = v)
        } else {
          local = localEntry
          old = oldArtifact ? buildUpstreamObj(status.kind, oldArtifact.artifact, localEntry) : null
          fresh = buildUpstreamObj(status.kind, newArtifact.artifact, localEntry)
        }
        setLocalView(local)
        setUpstreamNew(fresh)
        const d = computeFieldDiff(local, old, fresh)
        setDiff(d)
        const init: Record<string, 'local' | 'upstream'> = {}
        for (const f of d) if (f.status !== 'same') init[f.field] = f.defaultChoice
        setChoices(init)
        setPhase('ready')
      } catch (e) {
        if (!cancelled) {
          setFetchError(apiErrorMessage(e))
          setPhase('error')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [attempt, status, localEntry])

  const doApply = async () => {
    if (!upstreamNew) return
    setApplying(true)
    setApplyError(null)
    try {
      await onApply(applyUpgradeChoices(localView, upstreamNew, choices, status.capabilityName, status.latestVersion!), choices)
    } catch (e) {
      setApplyError(apiErrorMessage(e))
    } finally {
      setApplying(false)
    }
  }

  const changed = diff.filter((f) => f.status !== 'same')

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/50 p-4 pt-[8vh]" onMouseDown={onClose}>
      <div
        className="flex max-h-[75vh] w-full max-w-md flex-col overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900 shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <h2 className="min-w-0 truncate text-sm font-medium text-zinc-100">
            Upgrade {status.capabilityName}
            <span className="ml-2 font-mono text-xs text-zinc-500">
              v{status.currentVersion ?? '?'} → v{status.latestVersion}
            </span>
          </h2>
          <button onClick={onClose} className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300">
            <X size={16} />
          </button>
        </div>

        {phase === 'loading' && (
          <div className="flex items-center justify-center gap-2 p-8 text-xs text-zinc-500">
            <Loader2 size={14} className="animate-spin" /> Fetching versions…
          </div>
        )}

        {phase === 'error' && (
          <div className="flex flex-col items-center gap-3 p-8 text-xs text-zinc-400">
            <p className="text-red-400">{fetchError}</p>
            <div className="flex gap-2">
              <button
                onClick={() => setAttempt((a) => a + 1)}
                className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-800"
              >
                Retry
              </button>
              <button onClick={onClose} className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800">
                Cancel
              </button>
            </div>
          </div>
        )}

        {phase === 'ready' && (
          <>
            <div className="flex-1 overflow-y-auto p-4">
              {status.isBreaking && (
                <div className="mb-3 flex items-start gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-400">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  Breaking (major) version update — review field changes carefully.
                </div>
              )}

              {runWarning && (
                <div className="mb-3 flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-400">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  {runWarning}
                </div>
              )}

              {changed.length === 0 ? (
                <p className="text-xs text-zinc-500">No content differences — this upgrade only re-points the version stamp.</p>
              ) : (
                <div className="space-y-3">
                  {changed.map((f) => (
                    <div key={f.field} className="rounded-md border border-zinc-800 bg-zinc-950 p-2.5">
                      <p className="mb-1.5 flex items-center gap-2 font-mono text-xs text-zinc-300">
                        {f.field}
                        <span className="rounded bg-zinc-800 px-1 py-0.5 text-[10px] uppercase tracking-wide text-zinc-500">{f.status}</span>
                      </p>
                      <div className="grid grid-cols-2 gap-2">
                        <div className="min-w-0 rounded bg-zinc-900 px-2 py-1.5">
                          <p className="mb-0.5 text-[10px] uppercase tracking-wide text-zinc-600">Yours</p>
                          <p className="break-all font-mono text-[11px] text-zinc-300" title={typeof f.localValue === 'string' ? f.localValue : JSON.stringify(f.localValue)}>
                            {valueText(f.localValue)}
                          </p>
                        </div>
                        <div className="min-w-0 rounded bg-zinc-900 px-2 py-1.5">
                          <p className="mb-0.5 text-[10px] uppercase tracking-wide text-zinc-600">Upstream</p>
                          <p className="break-all font-mono text-[11px] text-zinc-300" title={typeof f.upstreamValue === 'string' ? f.upstreamValue : JSON.stringify(f.upstreamValue)}>
                            {valueText(f.upstreamValue)}
                          </p>
                        </div>
                      </div>
                      <div className="mt-2 flex gap-1.5">
                        {(['local', 'upstream'] as const).map((c) => (
                          <button
                            key={c}
                            onClick={() => setChoices((prev) => ({ ...prev, [f.field]: c }))}
                            className={`rounded-md border px-2 py-1 text-[11px] font-medium ${
                              choices[f.field] === c
                                ? 'border-indigo-500 bg-indigo-500/15 text-indigo-300'
                                : 'border-zinc-700 text-zinc-400 hover:bg-zinc-800'
                            }`}
                          >
                            {c === 'local' ? 'Keep mine' : 'Take upstream'}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {status.isBreaking && (
                <label className="mt-3 flex cursor-pointer items-center gap-2 text-xs text-zinc-300">
                  <input
                    type="checkbox"
                    checked={breakingAck}
                    onChange={(e) => setBreakingAck(e.target.checked)}
                    className="h-3.5 w-3.5 accent-red-500"
                  />
                  I understand this is a breaking change
                </label>
              )}

              {runWarning && (
                <label className="mt-3 flex cursor-pointer items-center gap-2 text-xs text-zinc-300">
                  <input type="checkbox" checked={runAck} onChange={(e) => setRunAck(e.target.checked)} className="h-3.5 w-3.5 accent-amber-500" />
                  I understand this may affect the active run(s)
                </label>
              )}
            </div>

            <div className="flex items-center justify-between border-t border-zinc-800 px-4 py-3">
              <p className="min-w-0 flex-1 truncate pr-3 text-xs text-red-400">{applyError}</p>
              <div className="flex shrink-0 gap-2">
                <button onClick={onClose} className="rounded-md border border-zinc-700 px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800">
                  Cancel
                </button>
                <button
                  onClick={doApply}
                  disabled={applying || (status.isBreaking && !breakingAck) || (!!runWarning && !runAck)}
                  className="flex items-center gap-1.5 rounded-md border border-indigo-500 bg-indigo-500/15 px-3 py-1.5 text-xs font-medium text-indigo-300 hover:bg-indigo-500/25 disabled:opacity-40"
                >
                  {applying && <Loader2 size={12} className="animate-spin" />}
                  Apply upgrade
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
