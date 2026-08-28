import { Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { workflowsApi, WorkflowSummary } from '@/lib/api'

function WorkflowCard({ workflow }: { workflow: WorkflowSummary }) {
  const queryClient = useQueryClient()

  const deleteMutation = useMutation({
    mutationFn: () => workflowsApi.delete(workflow.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] })
    },
  })

  return (
    <div className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/50 p-4 hover:border-zinc-700 transition-colors">
      <Link to={`/workflows/${workflow.id}`} className="flex-1 block">
        <h3 className="font-medium text-zinc-100">{workflow.name}</h3>
        {workflow.description && (
          <p className="mt-1 text-sm text-zinc-400">{workflow.description}</p>
        )}
      </Link>
      <button
        onClick={() => deleteMutation.mutate()}
        disabled={deleteMutation.isPending}
        className="rounded-md p-1.5 text-zinc-500 hover:text-red-400 disabled:opacity-50"
      >
        <Trash2 size={16} />
      </button>
    </div>
  )
}

function CreateWorkflowForm() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const createMutation = useMutation({
    mutationFn: async (name: string) => {
      console.log('Creating workflow:', name)
      const result = await workflowsApi.create({
        id: `workflow_${Date.now()}`,
        name,
        schema_version: 1,
        nodes: [],
        edges: [],
        tools: [],
        models: [],
      })
      console.log('Created:', result)
      return result
    },
    onSuccess: (data) => {
      console.log('onSuccess, navigating to:', data.id)
      queryClient.invalidateQueries({ queryKey: ['workflows'] })
      navigate(`/workflows/${data.id}`)
    },
    onError: (err) => {
      console.error('Failed to create workflow:', err)
    },
  })

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        const form = e.currentTarget
        const input = form.querySelector('input') as HTMLInputElement
        if (input.value.trim()) {
          createMutation.mutate(input.value.trim())
        }
      }}
      className="rounded-lg border border-dashed border-zinc-700 p-4"
    >
      <div className="flex gap-2">
        <input
          type="text"
          placeholder="New workflow name..."
          className="flex-1 bg-transparent outline-none placeholder:text-zinc-500"
        />
        <button
          type="submit"
          disabled={createMutation.isPending}
          className="rounded-md bg-zinc-100 px-3 py-1.5 text-sm font-medium text-zinc-900 hover:bg-zinc-200 disabled:opacity-50"
        >
          {createMutation.isPending ? 'Creating...' : 'Create'}
        </button>
      </div>
      {createMutation.error && (
        <p className="mt-2 text-sm text-red-400">
          Failed to create workflow
        </p>
      )}
    </form>
  )
}

export default function WorkflowList() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => workflowsApi.list(),
  })

  return (
    <div className="min-h-screen bg-zinc-950">
      <header className="border-b border-zinc-800 px-6 py-4">
        <h1 className="text-xl font-semibold">AI Forge</h1>
      </header>

      <main className="mx-auto max-w-4xl p-6">
        <div className="mb-6">
          <CreateWorkflowForm />
        </div>

        <div className="space-y-3">
          {isLoading ? (
            <p className="text-zinc-500">Loading...</p>
          ) : error ? (
            <p className="text-red-400">Error loading workflows: {error.message}</p>
          ) : data?.length === 0 ? (
            <p className="text-zinc-500">No workflows yet. Create one above.</p>
          ) : (
            data?.map((wf) => <WorkflowCard key={wf.id} workflow={wf} />)
          )}
        </div>
      </main>
    </div>
  )
}
