import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Boxes, Check, ChevronDown, ChevronRight, Copy, Download, Loader2, Search } from 'lucide-react'

import {
  capabilitiesApi,
  CAPABILITY_KINDS,
  type CapabilityKind,
} from '@/lib/registryApi'
import { workflowsApi, type Workflow } from '@/lib/api'
import type {
  AgentNode,
  AgentNodeConfig,
  ModelConfig,
  ToolDefinition,
} from '@/lib/workflowTypes'

/** Common shape shared by list summaries and search hits. */
type RowCap = {
  name: string
  kind: CapabilityKind
  description?: string | null
  latest_published?: string | null
  version?: string
}

const KIND_COLORS: Record<CapabilityKind, string> = {
  tool: 'bg-sky-500/15 text-sky-400',
  prompt: 'bg-violet-500/15 text-violet-400',
  model_profile: 'bg-teal-500/15 text-teal-400',
  skill: 'bg-amber-500/15 text-amber-400',
  agent: 'bg-rose-500/15 text-rose-400',
  workflow: 'bg-emerald-500/15 text-emerald-400',
}

function KindBadge({ kind }: { kind: CapabilityKind }) {
  return (
    <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${KIND_COLORS[kind]}`}>
      {kind}
    </span>
  )
}

function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false)
  const copy = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }
  return [copied, copy]
}

function suffixedId(taken: Set<string>, base: string): string {
  if (!taken.has(base)) return base
  let i = 2
  while (taken.has(`${base}_${i}`)) i++
  return `${base}_${i}`
}

/** Merge an inlined capability artifact into a workflow doc (returns a new object). */
function applyCapability(
  wf: Workflow,
  kind: CapabilityKind,
  artifact: Record<string, any>,
  capName: string,
  targetNodeId?: string,
): Workflow {
  const prompts = [...(wf.prompts ?? [])]
  const next: Workflow = {
    ...wf,
    nodes: [...wf.nodes],
    edges: [...wf.edges],
    tools: [...(wf.tools ?? [])],
    models: [...(wf.models ?? [])],
    prompts,
  }

  const addTool = (t: ToolDefinition): string => {
    const id = suffixedId(new Set(next.tools.map((x) => x.id)), t.id)
    next.tools.push({ ...t, id })
    return id
  }
  const addModel = (m: ModelConfig): string => {
    const id = suffixedId(new Set(next.models.map((x) => x.id)), m.id)
    next.models.push({ ...m, id })
    return id
  }

  switch (kind) {
    case 'tool':
      addTool(artifact as ToolDefinition)
      break
    case 'model_profile':
      addModel(artifact as ModelConfig)
      break
    case 'prompt': {
      const base = capName.split('/').pop() ?? capName
      const id = suffixedId(new Set(prompts.map((p) => p.id)), base)
      prompts.push({ id, name: base, text: artifact.text })
      break
    }
    case 'skill': {
      const toolIds = (artifact.tools as ToolDefinition[]).map(addTool)
      const idx = next.nodes.findIndex((n) => n.id === targetNodeId)
      const node = idx >= 0 ? next.nodes[idx] : undefined
      if (!node || node.type !== 'agent') {
        throw new Error('Pick an agent node for the skill')
      }
      const cfg = { ...(node as AgentNode).config }
      cfg.skills = [
        ...(cfg.skills ?? []),
        { name: artifact.name, prompt: artifact.prompt, tool_ids: toolIds },
      ]
      next.nodes[idx] = { ...(node as AgentNode), config: cfg }
      break
    }
    case 'agent': {
      const modelId = addModel(artifact.model as ModelConfig)
      const ownToolIds = (artifact.tools as ToolDefinition[]).map(addTool)
      const skills = (
        artifact.skills as Array<{ name: string; prompt: string; tools: ToolDefinition[] }>
      ).map((s) => ({ name: s.name, prompt: s.prompt, tool_ids: s.tools.map(addTool) }))
      next.nodes.push({
        id: crypto.randomUUID(),
        type: 'agent',
        position: { x: 80, y: 320 + (next.nodes.length % 5) * 40 },
        config: {
          model_id: modelId,
          system_prompt: artifact.prompt ?? '',
          temperature: null,
          tool_ids: ownToolIds,
          max_iterations: 5,
          prompt_ref: null,
          skills,
        },
      })
      break
    }
    default:
      throw new Error(`kind '${kind}' is not importable into a workflow`)
  }
  return next
}

function UseButton({ cap }: { cap: RowCap }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [copied, copy] = useCopy()
  const [open, setOpen] = useState(false)
  const [targetWfId, setTargetWfId] = useState('')
  const [targetNodeId, setTargetNodeId] = useState('')

  const { data: workflows } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => workflowsApi.list(),
    enabled: open && cap.kind !== 'workflow',
  })
  // Separate key so this pre-apply fetch can't poison the editor's
  // ['workflow', id] cache (the editor would serve stale data after import).
  const { data: targetWf } = useQuery({
    queryKey: ['workflow-for-use', targetWfId],
    queryFn: () => workflowsApi.get(targetWfId),
    enabled: !!targetWfId,
  })
  const agentNodes = (targetWf?.nodes ?? []).filter((n) => n.type === 'agent')

  const applyUse = useMutation({
    mutationFn: async () => {
      if (!targetWf) throw new Error('Pick a target workflow')
      const { artifact } = await capabilitiesApi.use(cap.name, 'latest', true)
      return workflowsApi.update(
        targetWf.id,
        applyCapability(targetWf, cap.kind, artifact, cap.name, targetNodeId || undefined),
      )
    },
    onSuccess: (wf) => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] })
      queryClient.invalidateQueries({ queryKey: ['workflow', wf.id] })
      navigate(`/workflows/${wf.id}`)
    },
  })

  const importWorkflow = useMutation({
    mutationFn: async () => {
      const { artifact } = await capabilitiesApi.use(cap.name)
      return workflowsApi.create({
        id: `workflow_${Date.now()}`,
        name: cap.name.split('/').pop() ?? cap.name,
        description: cap.description ?? undefined,
        schema_version: artifact.schema_version ?? 1,
        nodes: artifact.nodes ?? [],
        edges: artifact.edges ?? [],
        tools: artifact.tools ?? [],
        models: artifact.models ?? [],
        state_schema: artifact.state_schema ?? null,
      })
    },
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ['workflows'] })
      navigate(`/workflows/${created.id}`)
    },
  })

  const copyArtifact = async () => {
    const result = await capabilitiesApi.use(cap.name)
    if (cap.kind === 'prompt' && typeof result.manifest.spec?.text === 'string') {
      copy(result.manifest.spec.text)
      return
    }
    copy(JSON.stringify(result.artifact, null, 2))
  }

  if (cap.kind === 'workflow') {
    return (
      <button
        onClick={() => importWorkflow.mutate()}
        disabled={importWorkflow.isPending}
        className="flex shrink-0 items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-xs font-medium text-emerald-400 transition-colors hover:bg-emerald-500/20 disabled:opacity-50"
      >
        {importWorkflow.isPending ? (
          <Loader2 size={12} className="animate-spin" />
        ) : (
          <Download size={12} />
        )}
        Import as workflow
      </button>
    )
  }

  const needsAgent = cap.kind === 'skill'
  const ready = !!targetWfId && (!needsAgent || !!targetNodeId)
  const applyError = applyUse.isError
    ? ((applyUse.error as any)?.response?.data?.detail ?? (applyUse.error as Error).message)
    : null

  return (
    <div className="flex shrink-0 items-center gap-2">
      {open && (
        <>
          <select
            value={targetWfId}
            onChange={(e) => {
              setTargetWfId(e.target.value)
              setTargetNodeId('')
            }}
            className="max-w-40 rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs text-zinc-300 outline-none focus:border-zinc-600"
          >
            <option value="">Workflow…</option>
            {(workflows ?? []).map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
          {needsAgent && (
            <select
              value={targetNodeId}
              onChange={(e) => setTargetNodeId(e.target.value)}
              disabled={!targetWfId}
              className="max-w-36 rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1 text-xs text-zinc-300 outline-none focus:border-zinc-600 disabled:opacity-50"
            >
              <option value="">Agent node…</option>
              {agentNodes.map((n) => (
                <option key={n.id} value={n.id}>
                  {(n.config as AgentNodeConfig).system_prompt?.slice(0, 24) || n.id.slice(0, 8)}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={() => applyUse.mutate()}
            disabled={!ready || applyUse.isPending}
            className="flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2 py-1 text-xs font-medium text-emerald-400 transition-colors hover:bg-emerald-500/20 disabled:opacity-50"
          >
            {applyUse.isPending ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Check size={12} />
            )}
            Apply
          </button>
        </>
      )}
      {applyError && (
        <span title={String(applyError)} className="max-w-44 truncate text-xs text-red-400">
          {String(applyError)}
        </span>
      )}
      <button
        onClick={() => setOpen(!open)}
        className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-800"
      >
        {open ? 'Close' : 'Use in…'}
      </button>
      <button
        onClick={copyArtifact}
        className="flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-800"
      >
        {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
        {copied ? 'Copied' : `Copy ${cap.kind === 'prompt' ? 'text' : 'JSON'}`}
      </button>
    </div>
  )
}

function CapabilityRow({ cap }: { cap: RowCap }) {
  const [open, setOpen] = useState(false)
  const { data: detail } = useQuery({
    queryKey: ['capability', cap.name],
    queryFn: () => capabilitiesApi.detail(cap.name),
    enabled: open,
  })

  return (
    <div className="border-b border-zinc-800/60 last:border-0">
      <div className="flex w-full items-center gap-3 px-4 py-3 transition-colors hover:bg-zinc-900/50">
        <button
          onClick={() => setOpen(!open)}
          className="flex min-w-0 flex-1 items-center gap-3 text-left"
        >
          {open ? (
            <ChevronDown size={16} className="shrink-0 text-zinc-500" />
          ) : (
            <ChevronRight size={16} className="shrink-0 text-zinc-500" />
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <p className="truncate text-sm font-medium text-zinc-200">{cap.name}</p>
              <KindBadge kind={cap.kind} />
              {cap.latest_published && (
                <span className="shrink-0 rounded bg-emerald-500/15 px-1.5 py-0.5 text-xs text-emerald-400">
                  v{cap.latest_published}
                </span>
              )}
            </div>
            {cap.description && (
              <p className="truncate text-xs text-zinc-500">{cap.description}</p>
            )}
          </div>
        </button>
        <UseButton cap={cap} />
      </div>

      {open && (
        <div className="space-y-2 border-t border-zinc-800/60 bg-zinc-950 px-10 py-3">
          {detail ? (
            detail.versions.map((v) => (
              <details key={v.version} className="group rounded-md border border-zinc-800">
                <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm">
                  <ChevronRight size={14} className="text-zinc-500 transition-transform group-open:rotate-90" />
                  <span className="font-mono text-zinc-200">{v.version}</span>
                  <span className="rounded bg-zinc-800 px-1.5 py-0.5 text-xs text-zinc-400">
                    {v.stage}
                  </span>
                  <span className="text-xs text-zinc-600">{v.security_status}</span>
                </summary>
                <pre className="max-h-80 overflow-auto border-t border-zinc-800 p-3 font-mono text-xs text-zinc-400">
                  {JSON.stringify(v.manifest, null, 2)}
                </pre>
              </details>
            ))
          ) : (
            <p className="text-sm text-zinc-500">Loading versions...</p>
          )}
        </div>
      )}
    </div>
  )
}

export default function Capabilities() {
  const [query, setQuery] = useState('')
  const [kind, setKind] = useState<CapabilityKind | ''>('')

  const { data: searchHits } = useQuery({
    queryKey: ['capabilities', 'search', query, kind],
    queryFn: () => capabilitiesApi.search(query, kind || undefined),
    enabled: query.trim().length > 0,
  })

  const { data: allCaps, isLoading, error } = useQuery({
    queryKey: ['capabilities', 'list', kind],
    queryFn: () => capabilitiesApi.list(kind || undefined),
    enabled: query.trim().length === 0,
  })

  const shown = useMemo<RowCap[]>(() => {
    if (query.trim()) return searchHits ?? []
    return allCaps ?? []
  }, [query, searchHits, allCaps])

  return (
    <div className="flex h-full flex-col bg-zinc-950">
      <div className="flex items-center gap-3 border-b border-zinc-800 px-4 py-3">
        <Boxes size={18} className="text-zinc-500" />
        <h2 className="text-sm font-semibold text-zinc-100">Capabilities</h2>
        <div className="relative min-w-0 flex-1">
          <Search size={14} className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-zinc-600" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search capabilities..."
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 py-1.5 pl-7 pr-2 text-sm outline-none placeholder:text-zinc-600 focus:border-zinc-600"
          />
        </div>
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as CapabilityKind | '')}
          className="shrink-0 rounded-md border border-zinc-800 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-300 outline-none focus:border-zinc-600"
        >
          <option value="">All kinds</option>
          {CAPABILITY_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </div>

      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <p className="px-4 py-3 text-sm text-zinc-500">Loading...</p>
        ) : error ? (
          <div className="px-4 py-3 text-sm text-red-400">
            Failed to load capabilities. Is the registry running on port 3010?
          </div>
        ) : shown.length === 0 ? (
          <p className="px-4 py-3 text-sm text-zinc-500">
            {query.trim() ? 'No matching capabilities.' : 'No capabilities published yet.'}
          </p>
        ) : (
          shown.map((cap) => (
            <CapabilityRow key={`${cap.name}${cap.version ? `@${cap.version}` : ''}`} cap={cap} />
          ))
        )}
      </div>
    </div>
  )
}
