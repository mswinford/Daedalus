import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Boxes, Check, ChevronDown, ChevronRight, Copy, Download, Loader2, Search } from 'lucide-react'

import {
  capabilitiesApi,
  CAPABILITY_KINDS,
  type CapabilityKind,
} from '@/lib/registryApi'

/** Common shape shared by list summaries and search hits. */
type RowCap = {
  name: string
  kind: CapabilityKind
  description?: string | null
  latest_published?: string | null
  version?: string
}
import { workflowsApi } from '@/lib/api'

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

function UseButton({ cap }: { cap: RowCap }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [copied, copy] = useCopy()

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

  return (
    <button
      onClick={copyArtifact}
      className="flex shrink-0 items-center gap-1 rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-800"
    >
      {copied ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
      {copied ? 'Copied' : `Copy ${cap.kind === 'prompt' ? 'text' : 'JSON'}`}
    </button>
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
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-zinc-900/50"
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
        <UseButton cap={cap} />
      </button>

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
