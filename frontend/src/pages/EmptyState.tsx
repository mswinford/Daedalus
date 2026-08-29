import { Workflow } from 'lucide-react'

export default function EmptyState() {
  return (
    <div className="flex h-full items-center justify-center bg-zinc-950">
      <div className="flex flex-col items-center gap-3 text-center">
        <div className="rounded-full border border-zinc-800 bg-zinc-900/50 p-4">
          <Workflow size={28} className="text-zinc-600" />
        </div>
        <h2 className="text-base font-medium text-zinc-300">No workflow selected</h2>
        <p className="max-w-xs text-sm text-zinc-500">
          Pick a workflow from the left, or create a new one to start building.
        </p>
      </div>
    </div>
  )
}
