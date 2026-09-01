import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, XCircle, Timer, Coins, Cpu, PauseCircle, Play, X } from 'lucide-react'

import type { WorkflowRun, HumanInterruptField } from '@/lib/api'
import type { NodeType } from '@/lib/workflowTypes'
import type { FlowNodeType } from '@/lib/graphTransform'
import { summarize, groupExecutions, regionStats } from '@/lib/runEvents'

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

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(id)
  }, [active])
  return now
}

export function remainingSeconds(deadlineMs: number, now: number): number {
  return Math.max(0, Math.ceil((deadlineMs - now) / 1000))
}

function TimeoutCountdown({ deadlineMs, now }: { deadlineMs: number; now: number }) {
  const remaining = remainingSeconds(deadlineMs, now)
  if (remaining === 0) {
    return (
      <p className="mb-2 flex items-center gap-1 text-xs text-red-400">
        <Timer size={12} /> Timed out — the run has failed.
      </p>
    )
  }
  return (
    <p className="mb-2 flex items-center gap-1 text-xs text-amber-400">
      <Timer size={12} /> No response within {remaining}s will fail this run.
    </p>
  )
}

interface RunPanelProps {
  run: WorkflowRun
  nodes: FlowNodeType[]
  onResume?: (input: Record<string, any>) => void
}

function HumanInputForm({
  fields,
  approvalRequired,
  onSubmit,
}: {
  fields: HumanInterruptField[]
  approvalRequired: boolean
  onSubmit: (input: Record<string, any>) => void
}) {
  const [values, setValues] = useState<Record<string, string>>({})

  const handleSubmit = () => {
    const input: Record<string, any> = {}
    for (const f of fields) {
      const raw = values[f.name] ?? ''
      if (f.type === 'number') input[f.name] = raw ? Number(raw) : null
      else if (f.type === 'boolean') input[f.name] = raw === 'true'
      else input[f.name] = raw
    }
    if (approvalRequired) input.approved = true
    onSubmit(input)
  }

  return (
    <div className="rounded-md border border-amber-900/50 bg-amber-950/20 p-3">
      <p className="mb-2 text-sm font-medium text-amber-300">Human input required</p>
      <div className="space-y-2">
        {fields.map((f) => (
          <label key={f.name} className="block">
            <span className="text-xs text-zinc-400">
              {f.label}{f.required && <span className="text-red-400"> *</span>}
            </span>
            {f.type === 'select' && f.options ? (
              <select
                className="mt-0.5 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
                value={values[f.name] ?? ''}
                onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
              >
                <option value="">—</option>
                {f.options.map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
            ) : f.type === 'boolean' ? (
              <select
                className="mt-0.5 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
                value={values[f.name] ?? ''}
                onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
              >
                <option value="">—</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            ) : (
              <input
                type={f.type === 'number' ? 'number' : 'text'}
                className="mt-0.5 w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200"
                value={values[f.name] ?? ''}
                onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
              />
            )}
          </label>
        ))}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={handleSubmit}
          className="flex items-center gap-1.5 rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-500"
        >
          <Play size={14} />
          {approvalRequired ? 'Approve & Resume' : 'Submit & Resume'}
        </button>
        {approvalRequired && (
          <button
            onClick={() => onSubmit({ approved: false })}
            className="flex items-center gap-1.5 rounded-md border border-red-800 bg-red-950/40 px-3 py-1.5 text-sm font-medium text-red-300 hover:bg-red-900/40"
          >
            <X size={14} />
            Reject
          </button>
        )}
      </div>
    </div>
  )
}

export default function RunPanel({ run, nodes, onResume }: RunPanelProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const nodeTypeById = useMemo(() => {
    const m = new Map<string, NodeType>()
    for (const n of nodes) m.set(n.id, n.data.nodeType)
    return m
  }, [nodes])

  const { rows, totalExecutions } = useMemo(() => {
    const executions = summarize(run.events, nodeTypeById)
    return { rows: groupExecutions(executions), totalExecutions: executions.length }
  }, [run.events, nodeTypeById])

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

  const isRunning = run.status === 'running'
  const isPaused = run.status === 'paused'
  const failed = run.status !== 'completed' && !isRunning && !isPaused

  // Deadline for the pending human input, if the node has a timeout configured.
  const deadlineMs =
    run.interrupt_value?.timeout_seconds != null && run.interrupt_value.requested_at != null
      ? run.interrupt_value.requested_at * 1000 + run.interrupt_value.timeout_seconds * 1000
      : null
  const now = useNow(isPaused && deadlineMs != null)
  const timedOut = deadlineMs != null && now >= deadlineMs

  return (
    <div className="border-t border-zinc-800 bg-zinc-950">
      {/* Header metrics */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-xs">
        <span className={`flex items-center gap-1 font-medium ${isRunning ? 'text-amber-400' : isPaused ? 'text-purple-400' : failed ? 'text-red-400' : 'text-emerald-400'}`}>
          {isRunning ? <Timer size={14} /> : isPaused ? <PauseCircle size={14} /> : failed ? <XCircle size={14} /> : <CheckCircle2 size={14} />}
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
              Execution · {totalExecutions} node{totalExecutions === 1 ? '' : 's'}
            </p>
          {isPaused && run.interrupt_value && onResume && (
            <div className="mb-2">
              {run.interrupt_value.message && (
                <p className="mb-1 text-sm text-zinc-300">{run.interrupt_value.message}</p>
              )}
              {deadlineMs != null && <TimeoutCountdown deadlineMs={deadlineMs} now={now} />}
              {!timedOut && (
                <HumanInputForm
                  fields={run.interrupt_value.fields}
                  approvalRequired={run.interrupt_value.approval_required}
                  onSubmit={onResume}
                />
              )}
            </div>
          )}
          {run.error && (
            <pre className="mb-2 max-h-24 overflow-auto whitespace-pre-wrap rounded-md border border-red-900/50 bg-red-950/30 p-2 text-xs text-red-300">
              {run.error}
            </pre>
          )}
          <div className="max-h-56 space-y-0.5 overflow-auto pr-1">
            {totalExecutions === 0 && (
              <p className="py-2 text-xs text-zinc-600">No node execution recorded.</p>
            )}
            {rows.map(({ ex, children }) => {
              const isRegion = children.length > 0
              const stats = isRegion ? regionStats(ex, children) : null
              const durationMs = stats?.durationMs ?? ex.durationMs
              const tokensIn = stats?.tokensIn ?? ex.tokensIn
              const tokensOut = stats?.tokensOut ?? ex.tokensOut
              const llmCalls = stats?.llmCalls ?? ex.llmCalls
              const error = ex.error ?? stats?.error
              const open = expanded.has(ex.nodeId)
              const body = error ? error : formatOutput(ex.output)
              return (
                <div key={ex.nodeId}>
                  <button
                    onClick={() => toggle(ex.nodeId)}
                    className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left hover:bg-zinc-900"
                  >
                    <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: ex.color }} />
                    <span className="truncate text-sm text-zinc-200">{ex.label}</span>
                    {isRegion && (
                      <span className="shrink-0 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">
                        {children.length} inner
                      </span>
                    )}
                    <span className="shrink-0 font-mono text-[11px] text-zinc-600">{ex.nodeId}</span>
                    <span className="ml-auto flex shrink-0 items-center gap-2">
                      {llmCalls ? (
                        <span className="rounded bg-indigo-950/60 px-1.5 py-0.5 text-[11px] text-indigo-300">
                          {tokensIn}→{tokensOut} tok · {llmCalls} call{llmCalls === 1 ? '' : 's'}
                        </span>
                      ) : null}
                      {durationMs != null && (
                        <span className="text-[11px] text-zinc-500">{fmtMs(durationMs)}</span>
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
                  {open && isRegion && (
                    <div className="mb-1 ml-4 space-y-0.5 border-l border-zinc-800 pl-2">
                      {children.map((c) => {
                        const cOpen = expanded.has(c.nodeId)
                        const cBody = c.error ? c.error : formatOutput(c.output)
                        return (
                          <div key={c.nodeId}>
                            <button
                              onClick={() => toggle(c.nodeId)}
                              className="flex w-full items-center gap-2 rounded-md px-1.5 py-0.5 text-left hover:bg-zinc-900"
                            >
                              <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: c.color }} />
                              <span className="truncate text-xs text-zinc-300">{c.label}</span>
                              <span className="shrink-0 font-mono text-[10px] text-zinc-600">{c.nodeId}</span>
                              <span className="ml-auto flex shrink-0 items-center gap-2">
                                {c.llmCalls ? (
                                  <span className="rounded bg-indigo-950/60 px-1.5 py-0.5 text-[10px] text-indigo-300">
                                    {c.tokensIn}→{c.tokensOut} tok · {c.llmCalls} call{c.llmCalls === 1 ? '' : 's'}
                                  </span>
                                ) : null}
                                {c.durationMs != null && (
                                  <span className="text-[10px] text-zinc-500">{fmtMs(c.durationMs)}</span>
                                )}
                              </span>
                            </button>
                            {cOpen && cBody !== '' && (
                              <pre
                                className={`mb-1 ml-4 max-h-48 overflow-auto whitespace-pre-wrap rounded-md border p-2 text-xs ${
                                  c.error
                                    ? 'border-red-900/50 bg-red-950/30 text-red-300'
                                    : 'border-zinc-800 bg-zinc-900/60 text-zinc-400'
                                }`}
                              >
                                {cBody}
                              </pre>
                            )}
                          </div>
                        )
                      })}
                    </div>
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
