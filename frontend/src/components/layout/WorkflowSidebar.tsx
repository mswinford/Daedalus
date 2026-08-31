import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Boxes, Hourglass, Plus, Search, Trash2 } from 'lucide-react'

import { apiErrorMessage, workflowsApi, type PausedRunSummary, type WorkflowSummary } from '@/lib/api'

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  return now
}

function countdownLabel(run: PausedRunSummary, now: number): { text: string; expired: boolean } {
  if (run.timeout_seconds == null || run.requested_at == null) return { text: 'waiting', expired: false }
  const remainingMs = run.requested_at * 1000 + run.timeout_seconds * 1000 - now
  if (remainingMs <= 0) return { text: 'timed out', expired: true }
  const s = Math.ceil(remainingMs / 1000)
  const m = Math.floor(s / 60)
  return { text: m > 0 ? `${m}m ${s % 60}s left` : `${s}s left`, expired: false }
}

function PendingApprovalRow({
  run,
  workflowName,
  now,
  onOpen,
}: {
  run: PausedRunSummary
  workflowName: string
  now: number
  onOpen: () => void
}) {
  const { text, expired } = countdownLabel(run, now)
  return (
    <button
      onClick={onOpen}
      title={`Open run ${run.id}`}
      className="w-full rounded-md px-3 py-2 text-left transition-colors hover:bg-zinc-900"
    >
      <div className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-amber-400" />
        <p className="min-w-0 flex-1 truncate text-sm text-zinc-200">{workflowName}</p>
        <span className={`shrink-0 text-xs ${expired ? 'text-red-400' : 'text-amber-400'}`}>{text}</span>
      </div>
      {run.message && (
        <p className="truncate pl-3 text-xs text-zinc-500">{run.message}</p>
      )}
    </button>
  )
}

function SidebarRow({
  workflow,
  active,
  onDelete,
}: {
  workflow: WorkflowSummary
  active: boolean
  onDelete: () => void
}) {
  return (
    <div
      className={`group relative flex items-center rounded-md pr-1 transition-colors ${
        active ? 'bg-zinc-800' : 'hover:bg-zinc-900'
      }`}
    >
      <Link to={`/workflows/${workflow.id}`} className="block min-w-0 flex-1 px-3 py-2">
        <p className={`truncate text-sm ${active ? 'font-medium text-zinc-100' : 'text-zinc-300'}`}>
          {workflow.name}
        </p>
        {workflow.description && (
          <p className="truncate text-xs text-zinc-500">{workflow.description}</p>
        )}
      </Link>
      <button
        onClick={onDelete}
        title="Delete workflow"
        className="absolute right-1.5 rounded p-1 text-zinc-500 opacity-0 transition-opacity hover:text-red-400 focus:opacity-100 group-hover:opacity-100"
      >
        <Trash2 size={14} />
      </button>
    </div>
  )
}

export default function WorkflowSidebar({ activeId }: { activeId: string | null }) {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [newName, setNewName] = useState('')
  const onCapabilities = location.pathname === '/capabilities'

  const { data, isLoading, error } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => workflowsApi.list(),
  })

  const { data: pausedRuns } = useQuery({
    queryKey: ['runs', 'paused'],
    queryFn: () => workflowsApi.listPausedRuns(),
    refetchInterval: 5000,
  })
  const now = useNow((pausedRuns?.length ?? 0) > 0)
  const workflowName = (id: string) => data?.find((w) => w.id === id)?.name ?? id

  const createMutation = useMutation({
    mutationFn: (name: string) =>
      workflowsApi.create({
        id: `workflow_${Date.now()}`,
        name,
        schema_version: 1,
        nodes: [],
        edges: [],
        tools: [],
        models: [],
      }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] })
      setNewName('')
      navigate(`/workflows/${created.id}`)
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => workflowsApi.delete(id),
    onSuccess: (_res, id) => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] })
      if (id === activeId) navigate('/')
    },
  })

  const filtered = useMemo(() => {
    const list = data ?? []
    const q = search.trim().toLowerCase()
    const match = q ? list.filter((w) => w.name.toLowerCase().includes(q)) : list
    return [...match].sort((a, b) => a.name.localeCompare(b.name))
  }, [data, search])

  const handleDelete = (workflow: WorkflowSummary) => {
    if (!window.confirm(`Delete "${workflow.name}"? This cannot be undone.`)) return
    deleteMutation.mutate(workflow.id)
  }

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950">
      <div className="border-b border-zinc-800 px-4 py-3">
        <h1 className="text-sm font-semibold text-zinc-100">AI Forge</h1>
      </div>

      <div className="space-y-2 border-b border-zinc-800 p-3">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            const name = newName.trim()
            if (name) createMutation.mutate(name)
          }}
          className="flex gap-1.5"
        >
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New workflow name..."
            className="min-w-0 flex-1 rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm outline-none placeholder:text-zinc-600 focus:border-zinc-600"
          />
          <button
            type="submit"
            disabled={!newName.trim() || createMutation.isPending}
            title="Create workflow"
            className="rounded-md bg-zinc-100 px-2.5 text-zinc-900 transition-colors hover:bg-zinc-200 disabled:opacity-40"
          >
            <Plus size={16} />
          </button>
        </form>
        {createMutation.error && (
          <p className="text-xs text-red-400">Failed to create workflow</p>
        )}
        {deleteMutation.error && (
          <p className="text-xs text-red-400">Failed to delete workflow: {apiErrorMessage(deleteMutation.error)}</p>
        )}

        <div className="relative">
          <Search size={14} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-zinc-600" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter workflows..."
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 py-1.5 pl-7 pr-2 text-sm outline-none placeholder:text-zinc-600 focus:border-zinc-600"
          />
        </div>
      </div>

      {pausedRuns && pausedRuns.length > 0 && (
        <div className="border-b border-zinc-800 p-3">
          <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-amber-400">
            <Hourglass size={12} />
            Pending approvals ({pausedRuns.length})
          </p>
          <div className="space-y-0.5">
            {pausedRuns.map((run) => (
              <PendingApprovalRow
                key={run.id}
                run={run}
                workflowName={workflowName(run.workflow_id)}
                now={now}
                onOpen={() => navigate(`/workflows/${run.workflow_id}?run=${run.id}`)}
              />
            ))}
          </div>
        </div>
      )}

      <nav className="flex-1 space-y-0.5 overflow-y-auto p-2">
        {isLoading ? (
          <p className="px-3 py-2 text-sm text-zinc-500">Loading...</p>
        ) : error ? (
          <p className="px-3 py-2 text-sm text-red-400">Failed to load workflows</p>
        ) : filtered.length === 0 ? (
          <p className="px-3 py-2 text-sm text-zinc-500">
            {search ? 'No matching workflows.' : 'No workflows yet. Create one above.'}
          </p>
        ) : (
          filtered.map((wf) => (
            <SidebarRow
              key={wf.id}
              workflow={wf}
              active={activeId === wf.id}
              onDelete={() => handleDelete(wf)}
            />
          ))
        )}
      </nav>

      <div className="border-t border-zinc-800 p-2">
        <Link
          to="/capabilities"
          className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
            onCapabilities ? 'bg-zinc-800 font-medium text-zinc-100' : 'text-zinc-400 hover:bg-zinc-900'
          }`}
        >
          <Boxes size={15} />
          Capabilities
        </Link>
      </div>
    </aside>
  )
}
