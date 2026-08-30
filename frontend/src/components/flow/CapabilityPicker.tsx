import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, Loader2, Plus, Search, X } from 'lucide-react'

import { capabilitiesApi, type CapabilityKind } from '@/lib/registryApi'
import { applyCapability } from '@/lib/capabilityImport'
import type { Workflow } from '@/lib/api'

const KIND_COLORS: Record<CapabilityKind, string> = {
  tool: 'bg-sky-500/15 text-sky-400',
  prompt: 'bg-violet-500/15 text-violet-400',
  model_profile: 'bg-teal-500/15 text-teal-400',
  skill: 'bg-amber-500/15 text-amber-400',
  agent: 'bg-rose-500/15 text-rose-400',
  workflow: 'bg-emerald-500/15 text-emerald-400',
}

function KindBadge({ kind }: { kind: CapabilityKind }) {
  return (
    <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${KIND_COLORS[kind]}`}>
      {kind}
    </span>
  )
}

type Row = {
  name: string
  kind: CapabilityKind
  description?: string | null
  spec?: Record<string, any>
}

/** Is this capability already present in the workflow (per its attachment point)? */
function isPresent(row: Row, wf: Workflow | null, agentId: string | null): boolean {
  if (!wf) return false
  switch (row.kind) {
    case 'tool':
      return !!row.spec && (wf.tools ?? []).some((t) => t.id === row.spec!.id)
    case 'model_profile':
      return !!row.spec && (wf.models ?? []).some((m) => m.id === row.spec!.id)
    case 'prompt':
      return (wf.prompts ?? []).some((p) => p.id === (row.name.split('/').pop() ?? row.name))
    case 'skill': {
      const node = wf.nodes.find((n) => n.id === agentId)
      if (!node || node.type !== 'agent' || !row.spec) return false
      return ((node.config as any).skills ?? []).some((s: any) => s.name === row.spec!.name)
    }
    default:
      return false
  }
}

interface Props {
  getWorkflow: () => Workflow | null
  defaultAgentId?: string | null
  onApply: (merged: Workflow) => void
  onClose: () => void
}

export default function CapabilityPicker({ getWorkflow, defaultAgentId, onApply, onClose }: Props) {
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [agentId, setAgentId] = useState<string | null>(defaultAgentId ?? null)
  const [pending, setPending] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 250)
    return () => clearTimeout(t)
  }, [q])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const { data: rows, isFetching } = useQuery({
    queryKey: ['cap-picker-search', debounced],
    queryFn: async (): Promise<Row[]> =>
      debounced ? capabilitiesApi.search(debounced) : capabilitiesApi.list(),
  })

  const wf = getWorkflow()
  const agents = (wf?.nodes ?? []).filter((n) => n.type === 'agent')
  const showAgentSelect = (rows ?? []).some((r) => r.kind === 'skill')

  const add = async (row: Row) => {
    if (!wf || pending) return
    setPending(row.name)
    setError(null)
    try {
      const res = await capabilitiesApi.use(row.name, 'latest', true)
      const { wf: merged, added } = applyCapability(wf, row.kind, res.artifact, row.name, agentId ?? undefined)
      if (added) onApply(merged)
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? 'Failed to add capability')
    } finally {
      setPending(null)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 pt-[10vh]"
      onMouseDown={onClose}
    >
      <div
        className="flex max-h-[70vh] w-full max-w-md flex-col overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900 shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <h2 className="text-sm font-medium text-zinc-100">Add capability</h2>
          <button onClick={onClose} className="rounded p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300">
            <X size={16} />
          </button>
        </div>

        <div className="border-b border-zinc-800 p-3">
          <div className="relative">
            <Search size={14} className="absolute left-2.5 top-2.5 text-zinc-500" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search the registry… (blank = all)"
              className="w-full rounded-md border border-zinc-700 bg-zinc-950 py-1.5 pl-8 pr-3 text-sm text-zinc-200 outline-none focus:border-zinc-500"
            />
          </div>
          {showAgentSelect && (
            <div className="mt-2 flex items-center gap-2">
              <label className="text-xs text-zinc-400">Skills attach to:</label>
              <select
                value={agentId ?? ''}
                onChange={(e) => setAgentId(e.target.value || null)}
                disabled={agents.length === 0}
                className="flex-1 rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-200 outline-none focus:border-zinc-500 disabled:opacity-50"
              >
                {agents.length === 0 ? (
                  <option value="">No agent nodes in this workflow</option>
                ) : (
                  agents.map((n, i) => (
                    <option key={n.id} value={n.id}>
                      Agent {i + 1} ({n.id.slice(0, 8)}…)
                    </option>
                  ))
                )}
              </select>
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {isFetching && (
            <div className="flex items-center justify-center gap-2 p-6 text-xs text-zinc-500">
              <Loader2 size={14} className="animate-spin" /> Searching…
            </div>
          )}
          {!isFetching && (rows ?? []).length === 0 && (
            <p className="p-6 text-center text-xs text-zinc-500">No capabilities found.</p>
          )}
          {(rows ?? []).map((row) => {
            const importable = row.kind !== 'workflow'
            const isApplied = isPresent(row, wf, agentId)
            const disabled = !importable || pending !== null || (row.kind === 'skill' && !agentId)
            return (
              <div key={row.name} className="flex items-center gap-2 border-b border-zinc-800/60 px-4 py-2.5">
                <KindBadge kind={row.kind} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-zinc-200">{row.name}</p>
                  {row.description && (
                    <p className="truncate text-xs text-zinc-500">{row.description}</p>
                  )}
                </div>
                {isApplied ? (
                  <span className="flex items-center gap-1 text-xs text-emerald-400">
                    <Check size={14} /> Applied
                  </span>
                ) : (
                  <button
                    onClick={() => add(row)}
                    disabled={disabled}
                    title={
                      row.kind === 'workflow'
                        ? 'Workflows are imported as new workflows from the Capabilities page'
                        : row.kind === 'skill' && !agentId
                          ? 'Select an agent node first'
                          : undefined
                    }
                    className="flex items-center gap-1 rounded-md border border-zinc-700 px-2 py-1 text-xs font-medium text-zinc-200 hover:bg-zinc-800 disabled:opacity-40"
                  >
                    {pending === row.name ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
                    Add
                  </button>
                )}
              </div>
            )
          })}
        </div>

        {error && <p className="border-t border-zinc-800 px-4 py-2 text-xs text-red-400">{error}</p>}
      </div>
    </div>
  )
}
