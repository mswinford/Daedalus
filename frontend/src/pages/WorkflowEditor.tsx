import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
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
  type NodeChange,
  type EdgeChange,
} from '@xyflow/react'
import { Save, Play, Braces, ShieldCheck, CheckCircle2, AlertTriangle, Layers, KeyRound, PackagePlus, RefreshCw, X } from 'lucide-react'

import { workflowsApi, streamRunEvents, apiErrorMessage, type ValidationResult, type Workflow, type WorkflowRun } from '@/lib/api'
import {
  ALL_NODE_TYPES,
  PALETTE_GROUPS,
  NODE_META,
  defaultConfig,
  type NodeType,
  type WorkflowNode,
  type NodeConfig,
} from '@/lib/workflowTypes'
import {
  ERROR_EDGE_STYLE,
  nodesToRF,
  edgesToRF,
  rfToNodes,
  rfToEdges,
  sourceHandlesFor,
  type FlowNodeType,
} from '@/lib/graphTransform'
import FlowNode from '@/components/flow/FlowNode'
import ConfigPanel from '@/components/flow/ConfigPanel'
import ResourcesPanel from '@/components/flow/ResourcesPanel'
import type { CapabilityKind } from '@/lib/registryApi'
import RunPanel from '@/components/flow/RunPanel'
import SecretsPanel from '@/components/flow/SecretsPanel'
import CapabilityPicker from '@/components/flow/CapabilityPicker'
import CapabilityVersionBadge from '@/components/flow/CapabilityVersionBadge'
import TrackToggle from '@/components/flow/TrackToggle'
import { useCapabilityUpdates } from '@/lib/useCapabilityUpdates'
import type { UpdateStatus } from '@/lib/capabilityUpdates'
import UpgradeCapabilityModal from '@/components/flow/UpgradeCapabilityModal'
import {
  agentArtifactView,
  agentView,
  runGuardWarning,
  skillArtifactView,
  skillView,
  upsertModel,
  upsertTools,
} from '@/lib/capabilityUpgrade'
import type { AgentNodeConfig, AgentSkill, ModelConfig, ToolDefinition } from '@/lib/workflowTypes'

import '@xyflow/react/dist/style.css'

const nodeTypes = {
  start: FlowNode,
  end: FlowNode,
  agent: FlowNode,
  conditional: FlowNode,
  transform: FlowNode,
  human_in_loop: FlowNode,
  custom_function: FlowNode,
  invoke: FlowNode,
}

function WorkflowEditorInner() {
  const { id } = useParams()
  const { screenToFlowPosition } = useReactFlow()

  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNodeType>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [validation, setValidation] = useState<ValidationResult | null>(null)
  const [inputJson, setInputJson] = useState('')
  const [showInput, setShowInput] = useState(false)
  const [inputError, setInputError] = useState<string | null>(null)
  const [models, setModels] = useState<ModelConfig[]>([])
  const [tools, setTools] = useState<ToolDefinition[]>([])
  const [showResources, setShowResources] = useState(false)
  const [pickerKind, setPickerKind] = useState<CapabilityKind | null>(null)
  const [showSecrets, setShowSecrets] = useState(false)
  const [showPicker, setShowPicker] = useState(false)
  const [dirty, setDirty] = useState(false)
  // Local override for the workflow-level live-ref flag (the query data is read-only).
  const [wfTrackOverride, setWfTrackOverride] = useState<boolean | null>(null)
  // Local override for the name while editing (query data is read-only).
  const [nameOverride, setNameOverride] = useState<string | null>(null)

  const { data: workflow, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['workflow', id],
    queryFn: () => workflowsApi.get(id!),
  })
  const updates = useCapabilityUpdates(workflow ?? null)
  const { data: pausedRuns } = useQuery({ queryKey: ['runs', 'paused'], queryFn: () => workflowsApi.listPausedRuns() })
  const [updateNoticeDismissed, setUpdateNoticeDismissed] = useState(false)
  const [configUpgrading, setConfigUpgrading] = useState<UpdateStatus | null>(null)
  const upgradeArtifactRef = useRef<Record<string, any> | null>(null)
  const emptyEntryRef = useRef({})
  useEffect(() => {
    if (updates.error) setUpdateNoticeDismissed(false)
  }, [updates.error])
  const navigate = useNavigate()

  const syncedIdRef = useRef<string | null>(null)
  const syncedJsonRef = useRef('')

  useEffect(() => {
    if (!workflow) return
    const json = JSON.stringify(workflow)
    // Re-adopt server data for the same workflow (e.g. a "Use" import from
    // the Capabilities view or another tab), unless it's unchanged or the
    // user has unsaved local edits that would be clobbered.
    if (syncedIdRef.current === workflow.id && (json === syncedJsonRef.current || dirtyRef.current)) return
    setNodes(nodesToRF(workflow.nodes, workflow.edges))
    setEdges(edgesToRF(workflow.edges))
    setModels(workflow.models)
    setTools(workflow.tools)
    setSelectedId(null)
    setValidation(null)
    setDirty(false)
    setWfTrackOverride(null)
    syncedIdRef.current = workflow.id
    syncedJsonRef.current = json
  }, [workflow])

  const validationRef = useRef<Map<string, 'error' | 'warning'>>(new Map())

  useEffect(() => {
    if (!validation) return
    const m = new Map<string, 'error' | 'warning'>()
    for (const w of validation.warnings) if (w.node_id) m.set(w.node_id, 'warning')
    for (const e of validation.errors) if (e.node_id) m.set(e.node_id, 'error')
    validationRef.current = m
    setNodes((ns) => ns.map((n) => ({ ...n, data: { ...n.data, validation: m.get(n.id) } })))
  }, [validation, setNodes])

  const dirtyRef = useRef(false)
  const latestPayloadRef = useRef<Workflow | null>(null)

  const buildPayload = useCallback((): Workflow | null => {
    if (!workflow || !id) return null
    return {
      id: workflow.id,
      name: nameOverride ?? workflow.name,
      description: workflow.description ?? null,
      schema_version: workflow.schema_version,
      nodes: rfToNodes(nodes),
      edges: rfToEdges(edges),
      tools,
      models,
      prompts: workflow.prompts ?? [],
      state_schema: workflow.state_schema ?? null,
      // Top-level provenance must round-trip or autosave would wipe the stamp.
      source_capability: workflow.source_capability ?? null,
      source_version: workflow.source_version ?? null,
      track_latest: wfTrackOverride ?? workflow.track_latest ?? false,
    }
  }, [workflow, id, nodes, edges, tools, models, wfTrackOverride, nameOverride])

  useEffect(() => {
    latestPayloadRef.current = buildPayload()
  }, [buildPayload])

  useEffect(() => {
    dirtyRef.current = dirty
  }, [dirty])

  const selectedNode: WorkflowNode | null = useMemo(() => {
    if (!selectedId) return null
    const n = nodes.find((x) => x.id === selectedId)
    if (!n) return null
    return { id: n.id, type: n.data.nodeType, position: n.position, config: n.data.config, error_handling: n.data.errorHandling ?? false } as WorkflowNode
  }, [nodes, selectedId])

  const handleConfigChange = (nodeId: string, config: NodeConfig) => {
    setDirty(true)
    setNodes((ns) =>
      ns.map((n) => {
        if (n.id !== nodeId) return n
        const temp = { id: n.id, type: n.data.nodeType, position: n.position, config, error_handling: n.data.errorHandling ?? false } as WorkflowNode
        return { ...n, data: { ...n.data, config, branchHandles: sourceHandlesFor(temp, rfToEdges(edges)) } }
      }),
    )
  }

  // Pool removals must also drop the references in agent configs, otherwise
  // re-adding a tool/model with the same id silently re-enables it.
  const handleToolsChange = (t: ToolDefinition[]) => {
    setTools(t)
    const removed = tools.filter((x) => !t.some((y) => y.id === x.id)).map((x) => x.id)
    if (removed.length > 0) {
      setNodes((ns) =>
        ns.map((n) => {
          if (n.data.nodeType !== 'agent') return n
          const c = n.data.config as AgentNodeConfig
          return {
            ...n,
            data: {
              ...n.data,
              config: {
                ...c,
                tool_ids: c.tool_ids.filter((id) => !removed.includes(id)),
                skills: (c.skills ?? []).map((s) => ({ ...s, tool_ids: s.tool_ids.filter((id) => !removed.includes(id)) })),
              },
            },
          }
        }),
      )
    }
    setDirty(true)
  }

  const handleModelsChange = (m: ModelConfig[]) => {
    setModels(m)
    const removed = models.filter((x) => !m.some((y) => y.id === x.id)).map((x) => x.id)
    if (removed.length > 0) {
      setNodes((ns) =>
        ns.map((n) => {
          if (n.data.nodeType !== 'agent') return n
          const c = n.data.config as AgentNodeConfig
          if (!removed.includes(c.model_id)) return n
          return { ...n, data: { ...n.data, config: { ...c, model_id: '' } } }
        }),
      )
    }
    setDirty(true)
  }

  // ─── Capability upgrades (skill attachments + agent nodes) ─────────────────

  const openConfigUpgrade = (where: string) => {
    const s = updates.statuses.find((x) => x.where === where && x.hasUpdate)
    if (!s) return
    upgradeArtifactRef.current = null
    setConfigUpgrading(s)
  }

  const projectForUpgrade = useCallback(
    (oldA: Record<string, any> | null, newA: Record<string, any>) => {
      const s = configUpgrading
      if (!s) throw new Error('No upgrade in progress')
      upgradeArtifactRef.current = newA
      const nodeId = s.where.split(' ')[0].slice(5)
      const node = nodes.find((n) => n.id === nodeId)
      if (!node || node.data.nodeType !== 'agent') throw new Error('Agent node no longer exists')
      const cfg = node.data.config as AgentNodeConfig
      const wfTools = tools as unknown as Array<Record<string, unknown>>
      if (s.kind === 'skill') {
        const skills = cfg.skills ?? []
        const si = skills.findIndex((sk) => sk.source_capability === s.capabilityName)
        if (si < 0) throw new Error('Skill no longer attached to this agent')
        return {
          local: skillView(skills[si] as unknown as Record<string, unknown>, wfTools),
          old: oldA ? skillArtifactView(oldA) : null,
          new: skillArtifactView(newA),
        }
      }
      const wfModels = models as unknown as Array<Record<string, unknown>>
      return {
        local: agentView(cfg as unknown as Record<string, unknown>, wfModels, wfTools),
        old: oldA ? agentArtifactView(oldA) : null,
        new: agentArtifactView(newA),
      }
    },
    [configUpgrading, nodes, tools, models],
  )

  const applyConfigUpgrade = async (merged: Record<string, unknown>, choices: Record<string, 'local' | 'upstream'>) => {
    const s = configUpgrading
    if (!s) return
    const artifact = upgradeArtifactRef.current
    const nodeId = s.where.split(' ')[0].slice(5)
    const node = nodes.find((n) => n.id === nodeId)
    if (!node || node.data.nodeType !== 'agent') throw new Error('Agent node no longer exists')
    const cfg = { ...(node.data.config as AgentNodeConfig) }

    let nextTools: Array<Record<string, unknown>> | null = null
    const upsertIntoPool = (defs: ToolDefinition[]) => {
      if (defs.length === 0) return
      const base = nextTools ?? (tools as unknown as Array<Record<string, unknown>>)
      const defsAny = defs as unknown as Array<Record<string, unknown>>
      nextTools = upsertTools(base, defsAny, s.capabilityName, s.latestVersion!)
    }

    if (s.kind === 'skill') {
      const skills = cfg.skills ?? []
      const si = skills.findIndex((sk) => sk.source_capability === s.capabilityName)
      if (si < 0) throw new Error('Skill no longer attached to this agent')
      let toolIds = skills[si].tool_ids
      if (choices.tools === 'upstream' && artifact) upsertIntoPool((artifact.tools as ToolDefinition[]) ?? [])
      if (choices.tools === 'upstream' && artifact) toolIds = ((artifact.tools as ToolDefinition[]) ?? []).map((t) => t.id)
      const skill: AgentSkill = { ...skills[si], prompt: merged.prompt as string, tool_ids: toolIds, source_version: s.latestVersion! }
      cfg.skills = skills.map((sk, i) => (i === si ? skill : sk))
    } else {
      if (choices.model === 'upstream' && artifact?.model) {
        const r = upsertModel(models as unknown as Array<Record<string, unknown>>, artifact.model as Record<string, any>, s.capabilityName, s.latestVersion!)
        handleModelsChange(r.pool as unknown as ModelConfig[])
        cfg.model_id = r.id
      }
      cfg.system_prompt = merged.system_prompt as string
      if (choices.tools === 'upstream' && artifact) {
        upsertIntoPool((artifact.tools as ToolDefinition[]) ?? [])
        cfg.tool_ids = ((artifact.tools as ToolDefinition[]) ?? []).map((t) => t.id)
      }
      if (choices.skills === 'upstream' && artifact) {
        const nested: Array<{ name: string; prompt: string; tools?: ToolDefinition[] }> = (artifact.skills as any) ?? []
        upsertIntoPool(nested.flatMap((sk) => sk.tools ?? []))
        cfg.skills = nested.map((sk) => ({
          name: sk.name,
          prompt: sk.prompt,
          tool_ids: (sk.tools ?? []).map((t) => t.id),
          source_capability: s.capabilityName,
          source_version: s.latestVersion!,
        }))
      }
      cfg.source_version = s.latestVersion!
    }

    if (nextTools) handleToolsChange(nextTools as unknown as ToolDefinition[])
    handleConfigChange(nodeId, cfg)
    setConfigUpgrading(null)
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
      setDirty(true)
    },
    [screenToFlowPosition, setNodes],
  )

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  // Wrap React Flow's change handlers to mark the doc dirty on meaningful
  // changes (drag/move/delete), ignoring pure selection & dimension updates.
  const handleNodesChange = useCallback(
    (changes: NodeChange<FlowNodeType>[]) => {
      onNodesChange(changes)
      if (changes.some((c) => c.type !== 'select' && c.type !== 'dimensions')) setDirty(true)
    },
    [onNodesChange],
  )

  const handleEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      onEdgesChange(changes)
      if (changes.some((c) => c.type !== 'select')) setDirty(true)
    },
    [onEdgesChange],
  )

  // ─── Edge creation ─────────────────────────────────────────────────────────

  const handleConnect = useCallback(
    (params: Connection) => {
      if (params.source === params.target) return
      const isError = params.sourceHandle === 'error'
      setEdges((eds) => [
        ...eds,
        {
          id: crypto.randomUUID(),
          source: params.source,
          sourceHandle: params.sourceHandle ?? 'default',
          target: params.target,
          type: 'default',
          style: isError ? ERROR_EDGE_STYLE : undefined,
          data: { semanticType: isError ? 'error' : 'static', condition: null },
        },
      ])
      setDirty(true)
    },
    [setEdges],
  )

  const handleErrorToggle = useCallback(
    (nodeId: string, enabled: boolean) => {
      setNodes((ns) =>
        ns.map((n) => {
          if (n.id !== nodeId) return n
          const temp = { id: n.id, type: n.data.nodeType, position: n.position, config: n.data.config, error_handling: enabled } as WorkflowNode
          return {
            ...n,
            data: { ...n.data, errorHandling: enabled, branchHandles: sourceHandlesFor(temp, rfToEdges(edges)) },
          }
        }),
      )
      if (!enabled) {
        // Drop any edge wired from the now-removed error handle.
        setEdges((eds) => eds.filter((e) => !(e.source === nodeId && e.sourceHandle === 'error')))
      }
      setDirty(true)
    },
    [edges, setNodes, setEdges],
  )

  // ─── Node deletion (cascade edges) ─────────────────────────────────────────

  const handleNodesDelete = useCallback(
    (deleted: FlowNodeType[]) => {
      const ids = new Set(deleted.map((n) => n.id))
      setEdges((eds) => eds.filter((e) => !ids.has(e.source) && !ids.has(e.target)))
      if (selectedId && ids.has(selectedId)) setSelectedId(null)
      setDirty(true)
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
      setDirty(true)
    },
    [setNodes, setEdges],
  )

  // ─── Save / Validate / Run ─────────────────────────────────────────────────

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = buildPayload()
      if (!payload) throw new Error('Workflow not loaded')
      return workflowsApi.update(id!, payload)
    },
    onSuccess: (saved) => {
      setDirty(false)
      setValidation(null)
      validationRef.current = new Map()
      setNodes((ns) => ns.map((n) => ({ ...n, data: { ...n.data, validation: undefined } })))
      syncedJsonRef.current = JSON.stringify(saved)
    },
  })

  const importMutation = useMutation({
    mutationFn: (merged: Workflow) => workflowsApi.update(id!, merged),
    onSuccess: (saved) => {
      setNodes(nodesToRF(saved.nodes, saved.edges))
      setEdges(edgesToRF(saved.edges))
      setModels(saved.models)
      setTools(saved.tools)
      setDirty(false)
      syncedJsonRef.current = JSON.stringify(saved)
    },
  })

  const validateMutation = useMutation({
    mutationFn: () => {
      const payload = buildPayload()
      if (!payload) throw new Error('Workflow not loaded')
      return workflowsApi.validate(id!, payload)
    },
    onSuccess: (r) => setValidation(r),
  })

  const [run, setRun] = useState<WorkflowRun | null>(null)
  const runCloseRef = useRef<(() => void) | null>(null)
  const runLastSeqRef = useRef(0)
  const runFinishedRef = useRef(false)

  // Paused runs rebuild their graph from the saved workflow on resume, so
  // upgrading capabilities mid-flight can break them.
  const runWarning = useMemo(() => {
    const pausedCount = (pausedRuns ?? []).filter((r) => r.workflow_id === id).length
    return runGuardWarning(run?.status, pausedCount)
  }, [pausedRuns, id, run])

  const streamEvents = (runId: string): (() => void) => {
    let close: () => void = () => {}
    close = streamRunEvents(
      runId,
      (ev) => {
        if (ev.seq != null && ev.seq <= runLastSeqRef.current) return
        if (ev.seq != null) runLastSeqRef.current = ev.seq
        setRun((r) => (r ? { ...r, events: [...r.events, ev] } : r))
        const terminal = ev.type === 'run_end' || ev.type === 'human_timeout' || ev.type === 'run_cancelled' || (ev.type === 'node_error' && !!ev.data?.fatal)
        if (terminal || ev.type === 'human_request') {
          runFinishedRef.current = true
          workflowsApi.getRun(runId).then(setRun).catch(() => {})
          close()
        }
      },
      () => {
        if (!runFinishedRef.current) {
          setRun((r) => (r ? { ...r, status: 'failed', error: r.error ?? 'Connection lost' } : r))
        }
      },
    )
    return close
  }

  const handleRun = async () => {
    const trimmed = inputJson.trim()
    let parsed: Record<string, any> = {}
    if (trimmed) {
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
    }
    setInputError(null)

    runCloseRef.current?.()
    runCloseRef.current = null
    runLastSeqRef.current = 0
    runFinishedRef.current = false

    setRun({
      id: '', workflow_id: id!, status: 'running', input_data: parsed,
      events: [], total_tokens_input: 0, total_tokens_output: 0, estimated_cost_usd: 0,
    })

    try {
      const { run_id } = await workflowsApi.run(id!, parsed)
      setRun((r) => (r ? { ...r, id: run_id } : r))
      const close = streamEvents(run_id)
      runCloseRef.current = close
    } catch (err) {
      setRun((r) => (r ? { ...r, status: 'failed', error: String(err) } : r))
    }
  }

  const handleCancel = async () => {
    if (!run?.id) return
    try {
      await workflowsApi.cancelRun(run.id)
      // Paused runs are terminal immediately; running runs get the terminal
      // `run_cancelled` event over the existing stream. Either way a fresh
      // fetch keeps the panel in sync.
      workflowsApi.getRun(run.id).then(setRun).catch(() => {})
    } catch {
      workflowsApi.getRun(run.id).then(setRun).catch(() => {})
    }
  }

  const handleResume = async (humanInput: Record<string, any>) => {
    if (!run?.id) return
    runCloseRef.current?.()
    runCloseRef.current = null
    runFinishedRef.current = false
    setRun((r) => (r ? { ...r, status: 'running' } : r))

    try {
      await workflowsApi.resumeRun(run.id, humanInput)
      const close = streamEvents(run.id)
      runCloseRef.current = close
    } catch (err) {
      setRun((r) => (r ? { ...r, status: 'failed', error: String(err) } : r))
    }
  }

  useEffect(() => () => runCloseRef.current?.(), [])

  // Open a specific run when navigated here with ?run=<id> (e.g. from the
  // Pending Approvals section in the sidebar).
  const [searchParams] = useSearchParams()
  const pendingRunId = searchParams.get('run')
  useEffect(() => {
    if (!pendingRunId) return
    let cancelled = false
    runCloseRef.current?.()
    runCloseRef.current = null
    runLastSeqRef.current = 0
    runFinishedRef.current = false
    workflowsApi
      .getRun(pendingRunId)
      .then((r) => {
        if (cancelled) return
        setRun(r)
        if (r.status === 'running' || r.status === 'paused') {
          runCloseRef.current = streamEvents(r.id)
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, pendingRunId])

  // Debounced auto-save: persist ~800ms after the last edit while dirty.
  useEffect(() => {
    if (!dirty || !workflow) return
    const t = setTimeout(() => saveMutation.mutate(), 800)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty, workflow, nodes, edges, tools, models])

  // Flush pending changes on unmount (workflow switch / navigating away).
  useEffect(() => {
    return () => {
      if (dirtyRef.current && id && latestPayloadRef.current) {
        workflowsApi.update(id, latestPayloadRef.current).catch(() => {})
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  if (isLoading) return <div className="p-6 text-zinc-500">Loading...</div>
  const updateCount = updates.statuses.filter((s) => s.hasUpdate).length
  const hasBreakingUpdate = updates.statuses.some((s) => s.hasUpdate && s.isBreaking)
  const wfUpdate = updates.statuses.find((s) => s.kind === 'workflow')

  if (isError || !workflow)
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 bg-zinc-950 p-6 text-center">
        <AlertTriangle size={20} className="text-red-400" />
        <p className="text-sm text-zinc-300">{isError ? apiErrorMessage(error) : 'Workflow not found'}</p>
        <div className="flex gap-2">
          {isError && (
            <button
              onClick={() => refetch()}
              className="rounded-md border border-zinc-700 px-3 py-1.5 text-sm font-medium text-zinc-200 hover:bg-zinc-800"
            >
              Retry
            </button>
          )}
          <button
            onClick={() => navigate('/')}
            className="rounded-md border border-zinc-700 px-3 py-1.5 text-sm font-medium text-zinc-200 hover:bg-zinc-800"
          >
            Back to workflows
          </button>
        </div>
      </div>
    )

  return (
    <div className="flex h-full flex-col bg-zinc-950">
      {/* Top bar */}
      <header className="flex items-center justify-between border-b border-zinc-800 px-4 py-2">
        <div className="flex items-center gap-2">
          <input
            value={nameOverride ?? workflow.name}
            onChange={(e) => { setNameOverride(e.target.value); setDirty(true) }}
            aria-label="Workflow name"
            className="w-56 rounded-md border border-transparent bg-transparent px-1.5 py-0.5 font-medium outline-none hover:border-zinc-700 focus:border-zinc-600 focus:bg-zinc-900"
          />
          {wfUpdate && (
            <CapabilityVersionBadge current={wfUpdate.currentVersion} latest={wfUpdate.latestVersion} breaking={wfUpdate.isBreaking} tracking={!!(wfTrackOverride ?? workflow.track_latest)} />
          )}
          {workflow.source_capability && (
            <TrackToggle checked={!!(wfTrackOverride ?? workflow.track_latest)} onChange={(v) => { setWfTrackOverride(v); setDirty(true) }} />
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowResources(true)}
            className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-sm font-medium text-zinc-200 hover:bg-zinc-800"
          >
            <Layers size={14} />
            Resources
          </button>
          <button
            onClick={() => { setPickerKind(null); setShowPicker(true) }}
            className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-sm font-medium text-zinc-200 hover:bg-zinc-800"
          >
            <PackagePlus size={14} />
            Add capability
          </button>
          {updates.statuses.length > 0 && (
            <button
              onClick={updates.check}
              disabled={updates.checking}
              title="Check for capability updates"
              className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-sm font-medium text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
            >
              <RefreshCw size={14} className={updates.checking ? 'animate-spin' : ''} />
              Updates
              {updateCount > 0 && (
                <span className={`rounded-full px-1.5 text-xs font-semibold ${hasBreakingUpdate ? 'bg-red-500/20 text-red-400' : 'bg-amber-500/20 text-amber-400'}`}>
                  {updateCount}
                </span>
              )}
            </button>
          )}
          <button
            onClick={() => setShowSecrets(true)}
            className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-sm font-medium text-zinc-200 hover:bg-zinc-800"
          >
            <KeyRound size={14} />
            Secrets
          </button>
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
          {saveMutation.isPending ? (
            <span className="mr-1 text-xs text-zinc-400">Saving…</span>
          ) : dirty ? (
            <span className="mr-1 flex items-center gap-1.5 text-xs text-amber-400">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
              Unsaved
            </span>
          ) : (
            <span className="mr-1 flex items-center gap-1 text-xs text-emerald-400">
              <CheckCircle2 size={13} />
              Saved
            </span>
          )}
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex items-center gap-1.5 rounded-md border border-zinc-700 px-3 py-1.5 text-sm font-medium text-zinc-200 hover:bg-zinc-800 disabled:opacity-50"
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
            disabled={run?.status === 'running'}
            className="flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            <Play size={14} />
            {run?.status === 'running' ? 'Running…' : 'Run'}
          </button>
        </div>
      </header>

      {updates.error && !updateNoticeDismissed && (
        <div className="flex items-center justify-between gap-3 border-b border-zinc-800 bg-amber-950/20 px-4 py-1.5">
          <p className="text-xs text-amber-400">Couldn't check for capability updates — {updates.error}</p>
          <button onClick={() => setUpdateNoticeDismissed(true)} className="shrink-0 text-zinc-500 hover:text-zinc-300">
            <X size={12} />
          </button>
        </div>
      )}

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
          {PALETTE_GROUPS.map((g) => (
            <div key={g.label} className="mb-2.5">
              <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-zinc-600">{g.label}</p>
              <div className="space-y-1">
                {g.types.map((t) => (
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
            </div>
          ))}
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
            onNodesChange={handleNodesChange}
            onEdgesChange={handleEdgesChange}
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
            models={models}
            tools={tools}
            prompts={workflow?.prompts ?? []}
            onConfigChange={handleConfigChange}
            onErrorHandlingChange={handleErrorToggle}
            onDeleteNode={handleDeleteNode}
            edges={edges}
            updates={updates.statuses}
            onUpgradeOrigin={openConfigUpgrade}
          />
        </aside>
      </div>

      {/* Upgrade modal for skill attachments / agent nodes */}
      {configUpgrading && (
        <UpgradeCapabilityModal
          status={configUpgrading}
          localEntry={emptyEntryRef.current}
          project={projectForUpgrade}
          runWarning={runWarning}
          onClose={() => setConfigUpgrading(null)}
          onApply={applyConfigUpgrade}
        />
      )}

      {/* Resources panel (tools + models) */}
      {showResources && (
        <ResourcesPanel
          tools={tools}
          models={models}
          prompts={workflow?.prompts ?? []}
          wfId={id ?? undefined}
          updates={updates.statuses}
          runWarning={runWarning}
          onToolsChange={handleToolsChange}
          onModelsChange={handleModelsChange}
          onOpenRegistry={(kind) => { setShowResources(false); setPickerKind(kind); setShowPicker(true) }}
          onClose={() => setShowResources(false)}
        />
      )}

      {/* Secrets modal */}
      {showSecrets && <SecretsPanel onClose={() => setShowSecrets(false)} />}

      {/* Capability picker */}
      {showPicker && (
        <CapabilityPicker
          getWorkflow={buildPayload}
          defaultAgentId={selectedNode?.type === 'agent' ? selectedNode.id : null}
          onApply={(merged) => importMutation.mutate(merged)}
          onClose={() => { setShowPicker(false); setPickerKind(null) }}
          kindFilter={pickerKind}
        />
      )}

      {/* Bottom: run log / debug panel */}
      {run && <RunPanel run={run} nodes={nodes} onResume={handleResume} onCancel={handleCancel} />}
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
