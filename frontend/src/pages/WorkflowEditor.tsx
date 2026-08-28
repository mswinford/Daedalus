import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Edge,
} from '@xyflow/react'
import { ArrowLeft, Save, Play, ShieldCheck, CheckCircle2, AlertTriangle } from 'lucide-react'

import { workflowsApi, type ValidationResult } from '@/lib/api'
import {
  ALL_NODE_TYPES,
  NODE_META,
  type WorkflowNode,
  type NodeConfig,
} from '@/lib/workflowTypes'
import {
  nodesToRF,
  edgesToRF,
  rfToNodes,
  rfToEdges,
  sourceHandlesFor,
  applyValidation,
  type FlowNodeType,
} from '@/lib/graphTransform'
import FlowNode from '@/components/flow/FlowNode'
import ConfigPanel from '@/components/flow/ConfigPanel'

import '@xyflow/react/dist/style.css'

const nodeTypes = {
  start: FlowNode,
  end: FlowNode,
  agent: FlowNode,
  conditional: FlowNode,
  transform: FlowNode,
  human_in_loop: FlowNode,
  custom_function: FlowNode,
}

export default function WorkflowEditor() {
  const { id } = useParams()
  const navigate = useNavigate()

  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNodeType>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [validation, setValidation] = useState<ValidationResult | null>(null)

  const { data: workflow, isLoading } = useQuery({
    queryKey: ['workflow', id],
    queryFn: () => workflowsApi.get(id!),
  })

  // (Re)initialize the canvas whenever a different workflow is loaded.
  useEffect(() => {
    if (!workflow) return
    setNodes(nodesToRF(workflow.nodes, workflow.edges))
    setEdges(edgesToRF(workflow.edges))
    setSelectedId(null)
    setValidation(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflow?.id])

  const issueMap = useMemo(() => {
    const m = new Map<string, 'error' | 'warning'>()
    if (!validation) return m
    for (const w of validation.warnings) if (w.node_id) m.set(w.node_id, 'warning')
    for (const e of validation.errors) if (e.node_id) m.set(e.node_id, 'error')
    return m
  }, [validation])

  const displayNodes = useMemo(() => applyValidation(nodes, issueMap), [nodes, issueMap])

  const selectedNode: WorkflowNode | null = useMemo(() => {
    if (!selectedId) return null
    const n = nodes.find((x) => x.id === selectedId)
    if (!n) return null
    return { id: n.id, type: n.data.nodeType, position: n.position, config: n.data.config } as WorkflowNode
  }, [nodes, selectedId])

  const handleConfigChange = (nodeId: string, config: NodeConfig) => {
    setNodes((ns) =>
      ns.map((n) => {
        if (n.id !== nodeId) return n
        let branchHandles = n.data.branchHandles
        if (n.data.nodeType === 'conditional') {
          const temp = { id: n.id, type: 'conditional' as const, position: n.position, config } as WorkflowNode
          branchHandles = sourceHandlesFor(temp, rfToEdges(edges))
        }
        return { ...n, data: { ...n.data, config, branchHandles } }
      }),
    )
  }

  const saveMutation = useMutation({
    mutationFn: () =>
      workflowsApi.update(id!, {
        id: workflow!.id,
        name: workflow!.name,
        description: workflow!.description ?? null,
        schema_version: workflow!.schema_version,
        nodes: rfToNodes(nodes),
        edges: rfToEdges(edges),
        tools: workflow!.tools,
        models: workflow!.models,
        state_schema: workflow!.state_schema ?? null,
      }),
    onSuccess: () => setValidation(null),
  })

  const validateMutation = useMutation({
    mutationFn: () => workflowsApi.validate(id!),
    onSuccess: (r) => setValidation(r),
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
          <button onClick={() => navigate('/')} className="rounded-md p-1.5 text-zinc-400 hover:text-zinc-100">
            <ArrowLeft size={18} />
          </button>
          <h1 className="font-medium">{workflow.name}</h1>
        </div>
        <div className="flex items-center gap-2">
          {validation && (
            <span className={`mr-1 flex items-center gap-1 text-xs ${validation.valid ? 'text-emerald-400' : 'text-red-400'}`}>
              {validation.valid ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
              {validation.errors.length} error{validation.errors.length === 1 ? '' : 's'}, {validation.warnings.length} warning{validation.warnings.length === 1 ? '' : 's'}
            </span>
          )}
          <button
            onClick={() => validateMutation.mutate()}
            disabled={validateMutation.isPending}
            className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-sm font-medium text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
          >
            <ShieldCheck size={14} />
            Validate
          </button>
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

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: node legend */}
        <aside className="w-48 border-r border-zinc-800 p-3">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-500">Node types</p>
          <div className="space-y-1">
            {ALL_NODE_TYPES.map((t) => (
              <div key={t} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-zinc-300">
                <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: NODE_META[t].color }} />
                {NODE_META[t].label}
              </div>
            ))}
          </div>
        </aside>

        {/* Center: canvas */}
        <main className="relative flex-1 bg-zinc-900/30">
          <ReactFlow
            nodes={displayNodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            nodeTypes={nodeTypes}
            onNodeClick={(_e, n) => setSelectedId(n.id)}
            onPaneClick={() => setSelectedId(null)}
            nodesConnectable={false}
            deleteKeyCode={null}
            fitView
          >
            <Background color="#27272a" gap={16} />
            <Controls className="!bg-zinc-900 !border-zinc-800" />
          </ReactFlow>

          {/* Validation issue list */}
          {validation && [...validation.errors, ...validation.warnings].length > 0 && (
            <div className="absolute bottom-3 left-3 max-h-48 w-80 overflow-auto rounded-md border border-zinc-800 bg-zinc-900/95 p-2 text-xs shadow-lg">
              {[...validation.errors, ...validation.warnings].map((issue, i) => (
                <div key={i} className="flex items-start gap-1.5 py-1">
                  {issue.level === 'error' ? (
                    <AlertTriangle size={13} className="mt-0.5 shrink-0 text-red-400" />
                  ) : (
                    <span className="mt-0.5 shrink-0 text-amber-400">•</span>
                  )}
                  <span className="text-zinc-300">{issue.message}</span>
                </div>
              ))}
            </div>
          )}
        </main>

        {/* Right: config panel */}
        <aside className="w-72 overflow-y-auto border-l border-zinc-800 p-3">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-500">Config</p>
          <ConfigPanel node={selectedNode} models={workflow.models} tools={workflow.tools} onConfigChange={handleConfigChange} />
        </aside>
      </div>

      {/* Bottom: run output */}
      {runMutation.data && (
        <div className="border-t border-zinc-800 p-3">
          <pre className="max-h-40 overflow-auto text-xs text-zinc-400">{JSON.stringify(runMutation.data, null, 2)}</pre>
        </div>
      )}
    </div>
  )
}
