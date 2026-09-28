import type { RunEvent } from '@/lib/api'
import { NODE_META, type NodeType } from '@/lib/workflowTypes'

export interface ToolCallView {
  name: string
  args?: unknown
  /** Undefined while the call is still in flight. */
  success?: boolean
}

export interface NodeExecution {
  nodeId: string
  label: string
  color: string
  /** Unix timestamp (seconds) of the first event for this node. */
  startedAt?: number
  durationMs?: number
  output?: unknown
  error?: string
  tokensIn?: number
  tokensOut?: number
  llmCalls?: number
  retries?: number
  lastRetryError?: string
  toolCalls?: ToolCallView[]
}

/** Inner nodes of an invoke region carry ids of the form `{invoke_id}__{inner}`. */
export function splitInvokeId(nodeId: string): { invokeId: string; innerId: string } | null {
  const i = nodeId.indexOf('__')
  if (i <= 0) return null
  return { invokeId: nodeId.slice(0, i), innerId: nodeId.slice(i + 2) }
}

/** Fold the flat event stream into one summary per executed node, in run order. */
export function summarize(events: RunEvent[], nodeTypeById: Map<string, NodeType>, displayNameById?: Map<string, string>): NodeExecution[] {
  const byId = new Map<string, NodeExecution>()
  const order: string[] = []

  const ensure = (nodeId: string, ts?: number): NodeExecution => {
    let ex = byId.get(nodeId)
    if (!ex) {
      const type = nodeTypeById.get(nodeId)
      if (type) {
        ex = { nodeId, label: displayNameById?.get(nodeId) ?? NODE_META[type].label, color: NODE_META[type].color, startedAt: ts }
      } else {
        // Expanded inner nodes are not in the parent's node list — fall back to the authored inner id.
        const split = splitInvokeId(nodeId)
        ex = { nodeId, label: split ? split.innerId : nodeId, color: '#71717a', startedAt: ts }
      }
      byId.set(nodeId, ex)
      order.push(nodeId)
    }
    return ex
  }

  for (const ev of events) {
    if (!ev.node_id) continue
    const ex = ensure(ev.node_id, ev.timestamp)
    if (ev.type === 'node_end') {
      ex.durationMs = ev.data.duration_ms
      ex.output = ev.data.output
    } else if (ev.type === 'node_error') {
      ex.error = ev.data.error
      if (typeof ev.data.duration_ms === 'number') ex.durationMs = ev.data.duration_ms
    } else if (ev.type === 'human_timeout') {
      ex.error = ev.data.error
    } else if (ev.type === 'llm_call') {
      ex.tokensIn = (ex.tokensIn ?? 0) + (ev.data.tokens_input ?? 0)
      ex.tokensOut = (ex.tokensOut ?? 0) + (ev.data.tokens_output ?? 0)
      ex.llmCalls = (ex.llmCalls ?? 0) + 1
    } else if (ev.type === 'retry') {
      ex.retries = (ex.retries ?? 0) + 1
      ex.lastRetryError = ev.data.error
    } else if (ev.type === 'tool_call') {
      ex.toolCalls = ex.toolCalls ?? []
      ex.toolCalls.push({ name: ev.data.name, args: ev.data.args })
    } else if (ev.type === 'tool_result') {
      // Results carry no id — resolve the oldest unresolved call of the same
      // name: the runtime emits start/complete pairs in order, so completions
      // match calls in start order.
      const calls = ex.toolCalls
      if (calls) {
        for (const c of calls) {
          if (c.name === ev.data.name && c.success === undefined) {
            c.success = Boolean(ev.data.success)
            break
          }
        }
      }
    }
  }

  return order.map((id) => byId.get(id)!)
}

export interface ExecutionRow {
  ex: NodeExecution
  children: NodeExecution[]
}

/** Nest `{inv}__*` rows under their invoke parent row (only when the parent executed). */
export function groupExecutions(executions: NodeExecution[]): ExecutionRow[] {
  const byId = new Map(executions.map((e) => [e.nodeId, e]))
  const rows: ExecutionRow[] = []
  const rowByNodeId = new Map<string, ExecutionRow>()

  for (const ex of executions) {
    const split = splitInvokeId(ex.nodeId)
    if (split && byId.has(split.invokeId)) {
      let parentRow = rowByNodeId.get(split.invokeId)
      if (!parentRow) {
        // Parent has not produced a row yet — create it in place.
        parentRow = { ex: byId.get(split.invokeId)!, children: [] }
        rows.push(parentRow)
        rowByNodeId.set(split.invokeId, parentRow)
      }
      parentRow.children.push(ex)
    } else {
      const row: ExecutionRow = { ex, children: [] }
      rows.push(row)
      rowByNodeId.set(ex.nodeId, row)
    }
  }

  return rows
}

export interface RegionStats {
  durationMs?: number
  tokensIn?: number
  tokensOut?: number
  llmCalls?: number
  error?: string
}

/** Aggregate a region's inner nodes (plus the parent gate's own duration) for the collapsed row. */
export function regionStats(parent: NodeExecution, children: NodeExecution[]): RegionStats {
  let durationMs = parent.durationMs ?? 0
  let tokensIn = 0
  let tokensOut = 0
  let llmCalls = 0
  let error: string | undefined

  for (const c of children) {
    if (c.durationMs != null) durationMs += c.durationMs
    tokensIn += c.tokensIn ?? 0
    tokensOut += c.tokensOut ?? 0
    llmCalls += c.llmCalls ?? 0
    error = error ?? c.error
  }

  return {
    durationMs: parent.durationMs != null || children.some((c) => c.durationMs != null) ? durationMs : undefined,
    tokensIn: tokensIn || undefined,
    tokensOut: tokensOut || undefined,
    llmCalls: llmCalls || undefined,
    error,
  }
}
