import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Search, X } from 'lucide-react'

import { capabilitiesApi, type CapabilityKind } from '@/lib/registryApi'

const INVOKABLE: CapabilityKind[] = ['tool', 'workflow']

const KIND_COLORS: Record<string, string> = {
  tool: 'bg-sky-500/15 text-sky-400',
  workflow: 'bg-emerald-500/15 text-emerald-400',
}

interface Row {
  name: string
  kind: CapabilityKind
  description?: string | null
  version?: string | null
}

interface Props {
  onPick: (name: string) => void
  onClose: () => void
}

export default function InvokeCapabilityPicker({ onPick, onClose }: Props) {
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')

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
    queryKey: ['invoke-cap-picker', debounced],
    queryFn: async (): Promise<Row[]> => {
      const all = debounced ? await capabilitiesApi.search(debounced) : await capabilitiesApi.list()
      return (all as Row[]).filter((r) => INVOKABLE.includes(r.kind))
    },
  })

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 pt-[10vh]" onMouseDown={onClose}>
      <div
        className="flex max-h-[70vh] w-full max-w-md flex-col overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900 shadow-2xl"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <h2 className="text-sm font-medium text-zinc-100">Pick a capability (tool or workflow)</h2>
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
        </div>

        <div className="flex-1 overflow-y-auto">
          {isFetching && (
            <div className="flex items-center justify-center gap-2 p-6 text-xs text-zinc-500">
              <Loader2 size={14} className="animate-spin" /> Searching…
            </div>
          )}
          {!isFetching && (rows ?? []).length === 0 && (
            <p className="p-6 text-center text-xs text-zinc-500">No tools or workflows found.</p>
          )}
          {(rows ?? []).map((row) => (
            <button
              key={`${row.kind}:${row.name}`}
              onClick={() => onPick(row.name)}
              className="flex w-full items-center gap-2 border-b border-zinc-800/60 px-4 py-2.5 text-left hover:bg-zinc-800/50"
            >
              <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${KIND_COLORS[row.kind] ?? 'bg-zinc-700/30 text-zinc-400'}`}>
                {row.kind}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-zinc-200">{row.name}</p>
                {row.description && <p className="truncate text-xs text-zinc-500">{row.description}</p>}
              </div>
              {row.version && <span className="shrink-0 font-mono text-[11px] text-zinc-600">{row.version}</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
