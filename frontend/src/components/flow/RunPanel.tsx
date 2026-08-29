import { useMemo, useState } from 'react'
import { CheckCircle2, XCircle, Timer, Coins, Cpu } from 'lucide-react'

import type { WorkflowRun, RunEvent } from '@/lib/api'
import { NODE_META, type NodeType } from '@/lib/workflowTypes'
import type { FlowNodeType } from '@/lib/graphTransform'

interface NodeExecution {
  nodeId: string
  label: string
  color: string
  durationMs?: number
  output?: unknown
  error?: string
  tokensIn?: number
  tokensOut?: number
  llmCalls?: number
}

function fmtMs(ms?: number): string {
  if (ms == null) return ''
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

function formatOutput(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

/** Fold the flat event stream into one summary per executed node, in run order. */
function summarize(events: RunEvent[], nodeTypeById: Map<string, NodeType>): NodeExecution[] {
  const byId = new Map<string, NodeExecution>()
  const order: string[] = []

  const ensure = (nodeId: string): NodeExecution => {
    let ex = byId.get(nodeId)
    if (!ex) {
      const type = nodeTypeById.get(nodeId)
      ex = {
        nodeId,
        label: type ? NODE_META[type].label : nodeId,
        color: type ? NODE_META[type].color : '#71717a',
      }
      byId.set(nodeId, ex)
      order.push(nodeId)
    }
    return ex
  }

  for (const ev of events) {
    if (!ev.node_id) continue
    const ex = ensure(ev.node_id)
    if (ev.type === 'node_end') {
      ex.durationMs = ev.data.duration_ms
      ex.output = ev.data.output
    } else if (ev.type === 'node_error') {
      ex.error = ev.data.error
      if (typeof ev.data.duration_ms === 'number') ex.durationMs = ev.data.duration_ms
    } else if (ev.type === 'llm_call') {
      ex.tokensIn = (ex.tokensIn ?? 0) + (ev.data.tokens_input ?? 0)
      ex.tokensOut = (ex.tokensOut ?? 0) + (ev.data.tokens_output ?? 0)
      ex.llmCalls = (ex.llmCalls ?? 0) + 1
    }
  }

  return order.map((id) => byId.get(id)!)
}

interface RunPanelProps {
  run: WorkflowRun
  nodes: FlowNodeType[]
}

export default function RunPanel({ run, nodes }: RunPanelProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const nodeTypeById = useMemo(() => {
    const m = new Map<string, NodeType>()
    for (const n of nodes) m.set(n.id, n.data.nodeType)
    return m
  }, [nodes])

  const executions = useMemo(
    () => summarize(run.events, nodeTypeById),
    [run.events, nodeTypeById],
  )

  const totalMs =
    run.started_at != null && run.completed_at != null
      ? (run.completed_at - run.started_at) * 1000
      : undefined

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const failed = run.status !== 'completed'

  return (
    <div className="border-t border-zinc-800 bg-zinc-950">
      {/* Header metrics */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-xs">
        <span className={`flex items-center gap-1 font-medium ${failed ? 'text-red-400' : 'text-emerald-400'}`}>
          {failed ? <XCircle size={14} /> : <CheckCircle2 size={14} />}
          {run.status}
        </span>
        {totalMs != null && (
          <span className="flex items-center gap-1 text-zinc-400">
            <Timer size={13} />
            {fmtMs(totalMs)}
          </span>
        )}
        {(run.total_tokens_input > 0 || run.total_tokens_output > 0) && (
          <span className="flex items-center gap-1 text-zinc-400">
            <Cpu size={13} />
            {run.total_tokens_input} in / {run.total_tokens_output} out tokens
          </span>
        )}
        {run.estimated_cost_usd > 0 && (
          <span className="flex items-center gap-1 text-zinc-400">
            <Coins size={13} />
            ${run.estimated_cost_usd.toFixed(4)}
          </span>
        )}
        <span className="ml-auto text-zinc-600">{run.id}</span>
      </div>

      {/* Body: execution timeline + output */}
      <div className="flex flex-col gap-3 border-t border-zinc-800/60 px-3 py-2 md:flex-row">
        {/* Left: per-node execution */}
        <div className="min-w-0 flex-1">
          <p className="mb-1 text-[11px] font-medium uppercase tracking-wider text-zinc-500">
            Execution · {executions.length} node{executions.length === 1 ? '' : 's'}
          </p>
          {run.error && (
            <pre className="mb-2 max-h-24 overflow-auto whitespace-pre-wrap rounded-md border border-red-900/50 bg-red-950/30 p-2 text-xs text-red-300">
              {run.error}
            </pre>
          )}
          <div className="max-h-56 space-y-0.5 overflow-auto pr-1">
            {executions.length === 0 && (
              <p className="py-2 text-xs text-zinc-600">No node execution recorded.</p>
            )}
            {executions.map((ex) => {
              const open = expanded.has(ex.nodeId)
              const body = ex.error ? ex.error : formatOutput(ex.output)
              return (
                <div key={ex.nodeId}>
                  <button
                    onClick={() => toggle(ex.nodeId)}
                    className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left hover:bg-zinc-900"
                  >
                    <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: ex.color }} />
                    <span className="truncate text-sm text-zinc-200">{ex.label}</span>
                    <span className="shrink-0 font-mono text-[11px] text-zinc-600">{ex.nodeId}</span>
                    <span className="ml-auto flex shrink-0 items-center gap-2">
                      {ex.llmCalls ? (
                        <span className="rounded bg-indigo-950/60 px-1.5 py-0.5 text-[11px] text-indigo-300">
                          {ex.tokensIn}→{ex.tokensOut} tok · {ex.llmCalls} call{ex.llmCalls === 1 ? '' : 's'}
                        </span>
                      ) : null}
                      {ex.durationMs != null && (
                        <span className="text-[11px] text-zinc-500">{fmtMs(ex.durationMs)}</span>
                      )}
                    </span>
                  </button>
                  {open && body !== '' && (
                    <pre
                      className={`mb-1 ml-4 max-h-48 overflow-auto whitespace-pre-wrap rounded-md border p-2 text-xs ${
                        ex.error
                          ? 'border-red-900/50 bg-red-950/30 text-red-300'
                          : 'border-zinc-800 bg-zinc-900/60 text-zinc-400'
                      }`}
                    >
                      {body}
                    </pre>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        {/* Right: final output */}
        <div className="min-w-0 flex-1">
          <p className="mb-1 text-[11px] font-medium uppercase tracking-wider text-zinc-500">Output</p>
          <div className="max-h-56 space-y-2 overflow-auto pr-1">
            {run.output_data?.output && (
              <p className="whitespace-pre-wrap rounded-md border border-zinc-800 bg-zinc-900/40 p-2 text-sm text-zinc-100">
                {run.output_data.output}
              </p>
            )}
            {run.output_data?.node_outputs && (
              <pre className="overflow-auto rounded-md border border-zinc-800 bg-zinc-900/60 p-2 text-xs text-zinc-500">
                {JSON.stringify(run.output_data.node_outputs, null, 2)}
              </pre>
            )}
            {!run.output_data?.output && !run.output_data?.node_outputs && (
              <p className="py-2 text-xs text-zinc-600">No output.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
