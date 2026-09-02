import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Workflow as WorkflowIcon } from 'lucide-react'

import { apiErrorMessage, instantiateTemplate, templatesApi, workflowsApi } from '@/lib/api'

export default function EmptyState() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: templates } = useQuery({
    queryKey: ['templates'],
    queryFn: () => templatesApi.list(),
  })

  const createMutation = useMutation({
    mutationFn: (templateId?: string) =>
      templateId
        ? instantiateTemplate(templateId)
        : workflowsApi.create({
            id: `workflow_${Date.now()}`,
            name: 'Untitled workflow',
            schema_version: 1,
            nodes: [],
            edges: [],
            tools: [],
            models: [],
          }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] })
      navigate(`/workflows/${created.id}`)
    },
  })

  return (
    <div className="flex h-full items-center justify-center bg-zinc-950">
      <div className="flex w-full max-w-md flex-col items-center gap-6 px-6">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="rounded-full border border-zinc-800 bg-zinc-900/50 p-4">
            <WorkflowIcon size={28} className="text-zinc-600" />
          </div>
          <h2 className="text-base font-medium text-zinc-300">No workflow selected</h2>
          <p className="max-w-xs text-sm text-zinc-500">
            Pick a workflow from the left, or start a new one.
          </p>
        </div>

        <button
          onClick={() => createMutation.mutate()}
          disabled={createMutation.isPending}
          className="flex items-center gap-1.5 rounded-md bg-zinc-100 px-3 py-2 text-sm font-medium text-zinc-900 transition-colors hover:bg-zinc-200 disabled:opacity-40"
        >
          <Plus size={16} />
          New blank workflow
        </button>

        {templates && templates.length > 0 && (
          <div className="w-full">
            <p className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-500">
              Or start from a template
            </p>
            <div className="flex w-full flex-col gap-2">
              {templates.map((t) => (
                <button
                  key={t.id}
                  onClick={() => createMutation.mutate(t.id)}
                  disabled={createMutation.isPending}
                  className="w-full rounded-lg border border-zinc-800 bg-zinc-900/40 px-3 py-2.5 text-left transition-colors hover:border-zinc-600 disabled:opacity-40"
                >
                  <p className="text-sm font-medium text-zinc-200">{t.name}</p>
                  {t.description && (
                    <p className="mt-0.5 line-clamp-2 text-xs text-zinc-500">{t.description}</p>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}

        {createMutation.error && (
          <p className="text-xs text-red-400">Failed to create workflow: {apiErrorMessage(createMutation.error)}</p>
        )}
      </div>
    </div>
  )
}
