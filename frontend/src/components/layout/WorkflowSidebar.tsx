import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Trash2 } from 'lucide-react'

import { workflowsApi, type WorkflowSummary } from '@/lib/api'

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
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [newName, setNewName] = useState('')

  const { data, isLoading, error } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => workflowsApi.list(),
  })

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
    </aside>
  )
}
