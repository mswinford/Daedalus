import { describe, expect, it } from 'vitest'

import type { RunEvent } from '@/lib/api'
import { groupExecutions, regionStats, splitInvokeId, summarize } from './runEvents'

function ev(partial: Record<string, unknown>): RunEvent {
  return { seq: 0, data: {}, ...partial } as unknown as RunEvent
}

describe('splitInvokeId', () => {
  it('returns null for plain node ids', () => {
    expect(splitInvokeId('agent_abc123')).toBeNull()
    expect(splitInvokeId('__leading')).toBeNull()
    expect(splitInvokeId('')).toBeNull()
  })

  it('splits on the first __ separator', () => {
    expect(splitInvokeId('inv1__cf_node')).toEqual({ invokeId: 'inv1', innerId: 'cf_node' })
    expect(splitInvokeId('inv1__b__c')).toEqual({ invokeId: 'inv1', innerId: 'b__c' })
  })
})

describe('summarize', () => {
  it('folds events into one execution per node in first-seen order', () => {
    const events = [
      ev({ type: 'node_start', node_id: 'a' }),
      ev({ type: 'llm_call', node_id: 'b', data: { tokens_input: 10, tokens_output: 5 } }),
      ev({ type: 'node_end', node_id: 'a', data: { duration_ms: 12, output: 'done' } }),
      ev({ type: 'llm_call', node_id: 'b', data: { tokens_input: 1, tokens_output: 2 } }),
    ]
    const out = summarize(events, new Map())
    expect(out.map((e) => e.nodeId)).toEqual(['a', 'b'])
    expect(out[0]).toMatchObject({ durationMs: 12, output: 'done' })
    expect(out[1]).toMatchObject({ tokensIn: 11, tokensOut: 7, llmCalls: 2 })
  })

  it('captures errors from node_error and human_timeout', () => {
    const events = [
      ev({ type: 'node_error', node_id: 'a', data: { error: 'boom' } }),
      ev({ type: 'human_timeout', node_id: 'b', data: { error: 'timed out' } }),
    ]
    const out = summarize(events, new Map())
    expect(out[0].error).toBe('boom')
    expect(out[1].error).toBe('timed out')
  })

  it('folds retry events into a per-node counter', () => {
    const events = [
      ev({ type: 'node_start', node_id: 'a' }),
      ev({ type: 'retry', node_id: 'a', data: { attempt: 1, max_retries: 3, error: 'rate limited', category: 'rate_limit', delay_s: 1 } }),
      ev({ type: 'retry', node_id: 'a', data: { attempt: 2, max_retries: 3, error: 'timed out', category: 'timeout', delay_s: 2 } }),
      ev({ type: 'node_end', node_id: 'a', data: { duration_ms: 900, output: 'ok' } }),
    ]
    const out = summarize(events, new Map())
    expect(out[0]).toMatchObject({ retries: 2, lastRetryError: 'timed out', durationMs: 900 })
  })

  it('labels expanded inner nodes with their authored inner id', () => {
    const events = [ev({ type: 'node_end', node_id: 'inv1__hil', data: { duration_ms: 1 } })]
    const out = summarize(events, new Map())
    expect(out[0].label).toBe('hil')
    expect(out[0].color).toBe('#71717a')
  })

  it('uses NODE_META label/color for known node types', () => {
    const events = [ev({ type: 'node_end', node_id: 'x', data: {} })]
    const out = summarize(events, new Map([['x', 'start']]))
    expect(out[0].label).toBe('Start')
  })

  it('folds tool_call/tool_result pairs into per-node lists', () => {
    const events = [
      ev({ type: 'tool_call', node_id: 'c', data: { name: 'run_shell_command', args: { command: 'git ls-remote …' } } }),
      ev({ type: 'tool_result', node_id: 'c', data: { name: 'run_shell_command', success: true } }),
      ev({ type: 'tool_call', node_id: 'c', data: { name: 'read_file', args: { path: '/tmp/a' } } }),
      // No result yet — in flight.
    ]
    const out = summarize(events, new Map())
    expect(out[0].toolCalls).toEqual([
      { name: 'run_shell_command', args: { command: 'git ls-remote …' }, success: true },
      { name: 'read_file', args: { path: '/tmp/a' } },
    ])
  })

  it('resolves repeated same-name calls in start order', () => {
    const events = [
      ev({ type: 'tool_call', node_id: 'c', data: { name: 'shell', args: 1 } }),
      ev({ type: 'tool_call', node_id: 'c', data: { name: 'shell', args: 2 } }),
      ev({ type: 'tool_result', node_id: 'c', data: { name: 'shell', success: false } }),
      ev({ type: 'tool_result', node_id: 'c', data: { name: 'shell', success: true } }),
    ]
    const out = summarize(events, new Map())
    expect(out[0].toolCalls?.map((t) => t.success)).toEqual([false, true])
  })
})

describe('groupExecutions', () => {
  it('nests {inv}__* rows under their executed parent', () => {
    const events = [
      ev({ type: 'node_end', node_id: 'start1', data: {} }),
      ev({ type: 'node_end', node_id: 'inv1', data: { duration_ms: 1 } }),
      ev({ type: 'node_end', node_id: 'inv1__agent_a', data: { duration_ms: 2 } }),
      ev({ type: 'node_end', node_id: 'inv1__exit', data: { duration_ms: 1 } }),
      ev({ type: 'node_end', node_id: 'end1', data: {} }),
    ]
    const rows = groupExecutions(summarize(events, new Map()))
    expect(rows.map((r) => r.ex.nodeId)).toEqual(['start1', 'inv1', 'end1'])
    expect(rows[1].children.map((c) => c.nodeId)).toEqual(['inv1__agent_a', 'inv1__exit'])
  })

  it('keeps orphaned prefixed rows flat when the parent never executed', () => {
    const events = [ev({ type: 'node_end', node_id: 'ghost__inner', data: {} })]
    const rows = groupExecutions(summarize(events, new Map()))
    expect(rows).toHaveLength(1)
    expect(rows[0].ex.nodeId).toBe('ghost__inner')
    expect(rows[0].children).toEqual([])
  })

  it('does not nest top-level ids that merely contain __', () => {
    const events = [ev({ type: 'node_end', node_id: 'a__b', data: {} })]
    const rows = groupExecutions(summarize(events, new Map()))
    expect(rows).toHaveLength(1)
    expect(rows[0].children).toEqual([])
  })
})

describe('regionStats', () => {
  it('aggregates durations (including the parent gate), tokens, and first error', () => {
    const events = [
      ev({ type: 'node_end', node_id: 'inv1', data: { duration_ms: 5 } }),
      ev({ type: 'llm_call', node_id: 'inv1__agent_a', data: { tokens_input: 10, tokens_output: 4 } }),
      ev({ type: 'node_end', node_id: 'inv1__agent_a', data: { duration_ms: 20 } }),
      ev({ type: 'node_error', node_id: 'inv1__cf_b', data: { error: 'inner boom' } }),
    ]
    const rows = groupExecutions(summarize(events, new Map()))
    const [row] = rows.filter((r) => r.ex.nodeId === 'inv1')
    const stats = regionStats(row.ex, row.children)
    expect(stats.durationMs).toBe(25)
    expect(stats.tokensIn).toBe(10)
    expect(stats.tokensOut).toBe(4)
    expect(stats.llmCalls).toBe(1)
    expect(stats.error).toBe('inner boom')
  })

  it('leaves everything undefined when nothing ran', () => {
    const events = [ev({ type: 'node_start', node_id: 'inv1' })]
    const rows = groupExecutions(summarize(events, new Map()))
    const stats = regionStats(rows[0].ex, rows[0].children)
    expect(stats).toEqual({ durationMs: undefined, tokensIn: undefined, tokensOut: undefined, llmCalls: undefined, error: undefined })
  })
})
