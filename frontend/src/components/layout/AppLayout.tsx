import { Outlet, useParams } from 'react-router-dom'
import WorkflowSidebar from './WorkflowSidebar'

export default function AppLayout() {
  const { id } = useParams()

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      <WorkflowSidebar activeId={id ?? null} />
      <main className="min-w-0 flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  )
}
