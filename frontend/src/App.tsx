import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import WorkflowList from './pages/WorkflowList'
import WorkflowEditor from './pages/WorkflowEditor'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<WorkflowList />} />
        <Route path="/workflows/:id" element={<WorkflowEditor />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App
