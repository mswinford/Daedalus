import { BrowserRouter, Routes, Route, useParams } from 'react-router-dom'
import AppLayout from './components/layout/AppLayout'
import Capabilities from './pages/Capabilities'
import EmptyState from './pages/EmptyState'
import WorkflowEditor from './pages/WorkflowEditor'

// Remount the editor per workflow so transient state (run panel, input JSON,
// validation) resets on switch. Same route with a different :id does not
// remount on its own, hence the explicit key.
function EditorRoute() {
  const { id } = useParams()
  return <WorkflowEditor key={id} />
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<EmptyState />} />
          <Route path="capabilities" element={<Capabilities />} />
          <Route path="workflows/:id" element={<EditorRoute />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
