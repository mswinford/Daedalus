import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Edge,
  type Connection,
} from '@xyflow/react'
import { ArrowLeft, Save, Play, Braces, ShieldCheck, CheckCircle2, AlertTriangle } from 'lucide-react'

import { workflowsApi, type ValidationResult } from '@/lib/api'
import {
  ALL_NODE_TYPES,
  NODE_META,
  defaultConfig,
  type NodeType,
  type WorkflowNode,
  type NodeConfig,
} from '@/lib/workflowTypes'
import {
  nodesToRF,
  edgesToRF,
  rfToNodes,
  rfToEdges,
  sourceHandlesFor,
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

function WorkflowEditorInner() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { screenToFlowPosition } = useReactFlow()

  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNodeType>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [validation, setValidation] = useState<ValidationResult | null>(null)
  const [inputJson, setInputJson] = useState('')
  const [showInput, setShowInput] = useState(false)
  const [inputError, setInputError] = useState<string | null>(null)

  const { data: workflow, isLoading } = useQuery({
    queryKey: ['workflow', id],
    queryFn: () => workflowsApi.get(id!),
  })

  useEffect(() => {
    if (!workflow) return
    setNodes(nodesToRF(workflow.nodes, workflow.edges))
    setEdges(edgesToRF(workflow.edges))
    setSelectedId(null)
    setValidation(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflow?.id])

  const validationRef = useRef<Map<string, 'error' | 'warning'>>(new Map())

  useEffect(() => {
    if (!validation) return
    const m = new Map<string, 'error' | 'warning'>()
    for (const w of validation.warnings) if (w.node_id) m.set(w.node_id, 'warning')
    for (const e of validation.errors) if (e.node_id) m.set(e.node_id, 'error')
    validationRef.current = m
    setNodes((ns) => ns.map((n) => ({ ...n, data: { ...n.data, validation: m.get(n.id) } })))
  }, [validation, setNodes])

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

  // ─── Node creation (drag from palette) ─────────────────────────────────────

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      const type = e.dataTransfer.getData('application/reactflow') as NodeType
      if (!type || !ALL_NODE_TYPES.includes(type)) return
      const position = screenToFlowPosition({ x: e.clientX, y: e.clientY })
      const newId = crypto.randomUUID()
      const config = defaultConfig(type)
      const newNode: FlowNodeType = {
        id: newId,
        type,
        position,
        data: {
          nodeType: type,
          config,
          branchHandles: type === 'conditional' ? ['default'] : type === 'end' ? [] : ['default'],
        },
      }
      setNodes((ns) => [...ns, newNode])
      setSelectedId(newId)
    },
    [screenToFlowPosition, setNodes],
  )

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  // ─── Edge creation ─────────────────────────────────────────────────────────

  const handleConnect = useCallback(
    (params: Connection) => {
      if (params.source === params.target) return
      setEdges((eds) => [
        ...eds,
        {
          id: crypto.randomUUID(),
          source: params.source,
          sourceHandle: params.sourceHandle ?? 'default',
          target: params.target,
          type: 'default',
          data: { semanticType: 'static', condition: null },
        },
      ])
    },
    [setEdges],
  )

  // ─── Node deletion (cascade edges) ─────────────────────────────────────────

  const handleNodesDelete = useCallback(
    (deleted: FlowNodeType[]) => {
      const ids = new Set(deleted.map((n) => n.id))
      setEdges((eds) => eds.filter((e) => !ids.has(e.source) && !ids.has(e.target)))
      if (selectedId && ids.has(selectedId)) setSelectedId(null)
    },
    [setEdges, selectedId],
  )

  // ─── Edge deletion ─────────────────────────────────────────────────────────

  const handleEdgesDelete = useCallback(
    (_deleted: Edge[]) => {
      // No additional cleanup needed; React Flow removes them from state.
    },
    [],
  )

  // ─── Delete node from config panel ─────────────────────────────────────────

  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      setNodes((ns) => ns.filter((n) => n.id !== nodeId))
      setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId))
      setSelectedId(null)
    },
    [setNodes, setEdges],
  )

  // ─── Save / Validate / Run ─────────────────────────────────────────────────

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
    onSuccess: () => {
      setValidation(null)
      validationRef.current = new Map()
      setNodes((ns) => ns.map((n) => ({ ...n, data: { ...n.data, validation: undefined } })))
    },
  })

  const validateMutation = useMutation({
    mutationFn: () =>
      workflowsApi.validate(id!, {
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
    onSuccess: (r) => setValidation(r),
  })

  const runMutation = useMutation({
    mutationFn: (input: Record<string, any>) => workflowsApi.run(id!, input),
  })

  const handleRun = () => {
    const trimmed = inputJson.trim()
    if (!trimmed) {
      setInputError(null)
      runMutation.mutate({})
      return
    }
    let parsed: unknown
    try {
      parsed = JSON.parse(trimmed)
    } catch {
      setInputError('Invalid JSON')
      return
    }
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setInputError('Input must be a JSON object, e.g. {"score": 40}')
      return
    }
    setInputError(null)
    runMutation.mutate(parsed as Record<string, any>)
  }

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
            onClick={() => setShowInput((s) => !s)}
            className={`flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium disabled:opacity-50 ${
              showInput ? 'border-zinc-500 bg-zinc-800 text-zinc-100' : 'border-zinc-700 text-zinc-200 hover:bg-zinc-800'
            }`}
          >
            <Braces size={14} />
            Input
          </button>
          <button
            onClick={handleRun}
            disabled={runMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            <Play size={14} />
            Run
          </button>
        </div>
      </header>

      {showInput && (
        <div className="border-b border-zinc-800 bg-zinc-950 px-4 py-2">
          <div className="flex items-center justify-between">
            <label className="text-xs font-medium text-zinc-400">Run input (JSON) — optional</label>
            {inputJson && (
              <button onClick={() => setInputJson('')} className="text-xs text-zinc-500 hover:text-zinc-300">Clear</button>
            )}
          </div>
          <textarea
            value={inputJson}
            onChange={(e) => { setInputJson(e.target.value); if (inputError) setInputError(null) }}
            placeholder='Leave blank to run with no input, or e.g. {"score": 40}'
            spellCheck={false}
            className="mt-1 h-16 w-full resize-y rounded-md border border-zinc-800 bg-zinc-900 p-2 font-mono text-xs text-zinc-200 outline-none focus:border-zinc-600"
          />
          {inputError && <p className="mt-1 text-xs text-red-400">{inputError}</p>}
        </div>
      )}

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: node palette (draggable) */}
        <aside className="w-48 border-r border-zinc-800 p-3">
          <p className="mb-2 text-xs font-medium uppercase tracking-wider text-zinc-500">Add nodes</p>
          <div className="space-y-1">
            {ALL_NODE_TYPES.map((t) => (
              <div
                key={t}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData('application/reactflow', t)
                  e.dataTransfer.effectAllowed = 'move'
                }}
                className="flex cursor-grab items-center gap-2 rounded-md border border-zinc-800 bg-zinc-900/50 px-2 py-1.5 text-sm text-zinc-300 hover:border-zinc-600 hover:bg-zinc-800 active:cursor-grabbing"
              >
                <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: NODE_META[t].color }} />
                {NODE_META[t].label}
              </div>
            ))}
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-zinc-600">Drag onto canvas to add. Click a node to edit. Delete key removes selection.</p>
        </aside>

        {/* Center: canvas */}
        <main
          className="relative flex-1 bg-zinc-900/30"
          onDrop={handleDrop}
          onDragOver={handleDragOver}
        >
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={handleConnect}
            onNodesDelete={handleNodesDelete}
            onEdgesDelete={handleEdgesDelete}
            nodeTypes={nodeTypes}
            onNodeClick={(_e, n) => setSelectedId(n.id)}
            onEdgeClick={() => setSelectedId(null)}
            onPaneClick={() => setSelectedId(null)}
            deleteKeyCode="Delete"
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
          <ConfigPanel
            node={selectedNode}
            models={workflow.models}
            tools={workflow.tools}
            onConfigChange={handleConfigChange}
            onDeleteNode={handleDeleteNode}
            edges={edges}
          />
        </aside>
      </div>

      {/* Bottom: run output */}
      {runMutation.data && (
        <div className="border-t border-zinc-800 p-3">
          <div className="mb-1 flex items-center gap-2 text-xs">
            <span className={runMutation.data.status === 'completed' ? 'font-medium text-emerald-400' : 'font-medium text-red-400'}>
              {runMutation.data.status}
            </span>
            <span className="text-zinc-600">{runMutation.data.id}</span>
          </div>
          {runMutation.data.error ? (
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap text-xs text-red-300">{runMutation.data.error}</pre>
          ) : (
            <div className="space-y-2">
              {runMutation.data.output_data?.output && (
                <p className="text-sm text-zinc-100">{runMutation.data.output_data.output}</p>
              )}
              {runMutation.data.output_data?.node_outputs && (
                <pre className="max-h-40 overflow-auto text-xs text-zinc-500">
                  {JSON.stringify(runMutation.data.output_data.node_outputs, null, 2)}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function WorkflowEditor() {
  return (
    <ReactFlowProvider>
      <WorkflowEditorInner />
    </ReactFlowProvider>
  )
}
