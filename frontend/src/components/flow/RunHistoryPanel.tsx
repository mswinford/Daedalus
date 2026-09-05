import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { History, X } from 'lucide-react'

import { workflowsApi } from '@/lib/api'

const STATUS_STYLES: Record<string, string> = {
  running: 'bg-emerald-500/15 text-emerald-400',
  paused: 'bg-amber-500/15 text-amber-400',
  completed: 'bg-zinc-700/40 text-zinc-300',
  failed: 'bg-red-500/15 text-red-400',
  cancelled: 'bg-zinc-700/40 text-zinc-500',
}

function fmtTime(ts?: number | null): string {
  if (ts == null) return ''
  return new Date(ts * 1000).toLocaleString()
}

function fmtDuration(startedAt: number, completedAt?: number | null): string {
  if (completedAt == null) return ''
  const ms = Math.max(0, (completedAt - startedAt) * 1000)
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`
  const m = Math.floor(ms / 60_000)
  const s = Math.round((ms % 60_000) / 1000)
  return s ? `${m}m ${s}s` : `${m}m`
}

export default function RunHistoryPanel({
  workflowId,
  onOpenRun,
  onClose,
}: {
  workflowId: string
  onOpenRun: (runId: string) => void
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const { data: runs, isLoading, error } = useQuery({
    queryKey: ['runs', 'history', workflowId],
    queryFn: () => workflowsApi.listRuns({ workflow_id: workflowId, limit: 50 }),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="max-h-[80vh] w-[560px] overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-900 p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-100">
            <History size={16} /> Run history
          </h2>
          <button
            onClick={onClose}
            title="Close"
            aria-label="Close run history"
            className="rounded-md border border-zinc-700 p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-500"
          >
            <X size={14} />
          </button>
        </div>

        {isLoading && <p className="py-6 text-center text-sm text-zinc-500">Loading runs…</p>}
        {!isLoading && error && (
          <p className="py-6 text-center text-sm text-red-400">Could not load run history.</p>
        )}
        {!isLoading && !error && (runs ?? []).length === 0 && (
          <p className="py-6 text-center text-sm text-zinc-500">No runs yet for this workflow.</p>
        )}

        <ul className="space-y-1.5">
          {(runs ?? []).map((r) => (
            <li key={r.run_id}>
              <button
                onClick={() => onOpenRun(r.run_id)}
                title={`Open run ${r.run_id}`}
                className="w-full rounded-md border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-left hover:border-zinc-600 hover:bg-zinc-800/60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-500"
              >
                <div className="flex items-center gap-2">
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLES[r.status] ?? 'bg-zinc-700/40 text-zinc-400'}`}>
                    {r.status}
                  </span>
                  <span className="font-mono text-xs text-zinc-400">{r.run_id.slice(0, 8)}</span>
                  <span className="ml-auto text-xs text-zinc-500">{fmtTime(r.started_at)}</span>
                </div>
                <div className="mt-1 flex items-center gap-3 text-[11px] text-zinc-500">
                  {r.completed_at != null && (
                    <span>{fmtDuration(r.started_at, r.completed_at)}</span>
                  )}
                  {(r.total_tokens_input > 0 || r.total_tokens_output > 0) && (
                    <span>{r.total_tokens_input} in / {r.total_tokens_output} out</span>
                  )}
                  {r.estimated_cost_usd > 0 && <span>${r.estimated_cost_usd.toFixed(4)}</span>}
                  {r.status === 'failed' && r.error && (
                    <span className="min-w-0 flex-1 truncate text-red-400/80" title={r.error}>
                      {r.error}
                    </span>
                  )}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
