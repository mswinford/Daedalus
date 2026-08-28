import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { workflowsApi } from '@/lib/api'
import { ArrowLeft, Save, Play } from 'lucide-react'

export default function WorkflowEditor() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: workflow, isLoading } = useQuery({
    queryKey: ['workflow', id],
    queryFn: () => workflowsApi.get(id!),
  })

  const saveMutation = useMutation({
    mutationFn: () => workflowsApi.update(id!, workflow!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflow', id] })
    },
  })

  const runMutation = useMutation({
    mutationFn: () => workflowsApi.run(id!),
  })

  if (isLoading) return <div className="p-6 text-zinc-500">Loading...</div>
  if (!workflow) return <div className="p-6 text-zinc-500">Workflow not found</div>

  return (
    <div className="flex h-screen flex-col bg-zinc-950">
      {/* Top bar */}
      <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-2">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/')}
            className="rounded-md p-1.5 text-zinc-400 hover:text-zinc-100"
          >
            <ArrowLeft size={18} />
          </button>
          <h1 className="font-medium">{workflow.name}</h1>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-50"
          >
            <Save size={14} />
            Save
          </button>
          <button
            onClick={() => runMutation.mutate()}
            disabled={runMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            <Play size={14} />
            Run
          </button>
        </div>
      </header>

      {/* Main content: editor shell */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left panel: node palette */}
        <aside className="w-48 border-r border-zinc-800 p-3">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-500">Nodes</p>
          <div className="space-y-1">
            {['Start', 'End', 'Agent', 'Conditional', 'Transform', 'Custom Function'].map((type) => (
              <div
                key={type}
                className="cursor-grab rounded-md border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm text-zinc-300 hover:border-zinc-600"
              >
                {type}
              </div>
            ))}
          </div>
        </aside>

        {/* Center: canvas placeholder */}
        <main className="flex flex-1 items-center justify-center bg-zinc-900/30">
          <div className="text-center">
            <p className="text-zinc-500">Graph editor coming in Phase 2</p>
            <p className="mt-1 text-sm text-zinc-600">
              Workflow has {workflow.nodes.length} nodes, {workflow.edges.length} edges
            </p>
          </div>
        </main>

        {/* Right panel: config */}
        <aside className="w-72 border-l border-zinc-800 p-3">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-500">Config</p>
          <p className="text-sm text-zinc-500">Select a node to configure</p>
        </aside>
      </div>

      {/* Bottom panel: run output */}
      {runMutation.data && (
        <div className="border-t border-zinc-800 p-3">
          <pre className="max-h-32 overflow-auto text-xs text-zinc-400">
            {JSON.stringify(runMutation.data, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}
