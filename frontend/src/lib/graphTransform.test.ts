import { describe, it, expect } from 'vitest'
import { type Edge } from '@xyflow/react'

import { sourceHandlesFor, nodesToRF, edgesToRF, rfToNodes, rfToEdges, ERROR_EDGE_STYLE } from './graphTransform'
import type { WorkflowNode, WorkflowEdge, StartNode, AgentNode, ConditionalNode, EndNode } from './workflowTypes'

const startNode: StartNode = {
  id: 's',
  type: 'start',
  position: { x: 0, y: 0 },
  error_handling: false,
  config: { input_fields: [] },
}

const agentNode: AgentNode = {
  id: 'a1',
  type: 'agent',
  position: { x: 100, y: 0 },
  error_handling: true,
  config: { model_id: 'm1', system_prompt: 'sys', tool_ids: ['t1'], max_iterations: 3 },
}

const condNode: ConditionalNode = {
  id: 'c1',
  type: 'conditional',
  position: { x: 200, y: 0 },
  error_handling: false,
  config: {
    conditions: [
      { type: 'json_path', expression: '$.ok' },
      { type: 'regex', expression: '^err' },
    ],
    default_branch: null,
  },
}

const endNode: EndNode = {
  id: 'e1',
  type: 'end',
  position: { x: 300, y: 0 },
  error_handling: false,
  config: { output_fields: [] },
}

const nodes: WorkflowNode[] = [startNode, agentNode, condNode, endNode]

describe('sourceHandlesFor', () => {
  it('end node has no handles', () => {
    expect(sourceHandlesFor(endNode, [])).toEqual([])
  })

  it('plain agent node gets the default handle', () => {
    const plain: AgentNode = { ...agentNode, error_handling: false }
    expect(sourceHandlesFor(plain, [])).toEqual(['default'])
  })

  it('agent with error_handling gets an extra error handle', () => {
    expect(sourceHandlesFor(agentNode, [])).toEqual(['default', 'error'])
  })

  it('start node never gets an error handle', () => {
    const startWithErr: StartNode = { ...startNode, error_handling: true }
    expect(sourceHandlesFor(startWithErr, [])).toEqual(['default'])
  })

  it('conditional node maps branch edges positionally plus fallback', () => {
    const edges: WorkflowEdge[] = [
      { id: 'e1', source_node_id: 'c1', source_handle: 'a', target_node_id: 'a1', type: 'conditional' },
      { id: 'e2', source_node_id: 'c1', source_handle: 'b', target_node_id: 'e1', type: 'conditional' },
    ]
    expect(sourceHandlesFor(condNode, edges)).toEqual(['a', 'b', 'default'])
  })

  it('duplicate branch handle names get a dedup suffix', () => {
    const edges: WorkflowEdge[] = [
      { id: 'e1', source_node_id: 'c1', source_handle: 'x', target_node_id: 'a1', type: 'conditional' },
      { id: 'e2', source_node_id: 'c1', source_handle: 'x', target_node_id: 'e1', type: 'conditional' },
    ]
    expect(sourceHandlesFor(condNode, edges)).toEqual(['x', 'x_', 'default'])
  })

  it('does not duplicate a default_branch name already used by a branch', () => {
    const node: ConditionalNode = {
      ...condNode,
      config: { conditions: [{ type: 'json_path', expression: '$.ok' }], default_branch: 'fallback' },
    }
    const edges: WorkflowEdge[] = [
      { id: 'e1', source_node_id: 'c1', source_handle: 'fallback', target_node_id: 'a1', type: 'conditional' },
    ]
    expect(sourceHandlesFor(node, edges)).toEqual(['fallback'])
  })

  it('falls back to positional branch_N names without branch edges', () => {
    expect(sourceHandlesFor(condNode, [])).toEqual(['branch_1', 'branch_2', 'default'])
  })
})

describe('round-trips', () => {
  const edges: WorkflowEdge[] = [
    { id: 'e1', source_node_id: 's', source_handle: 'default', target_node_id: 'a1', type: 'static', condition: null },
    { id: 'e2', source_node_id: 'c1', source_handle: 'a', target_node_id: 'a1', type: 'conditional', condition: { type: 'json_path', expression: '$.ok' } },
  ]

  it('rfToNodes(nodesToRF(...)) preserves id/type/position/config/error_handling', () => {
    expect(rfToNodes(nodesToRF(nodes, edges))).toEqual(nodes)
  })

  it('rfToEdges(edgesToRF(...)) preserves id/source/target/handle/type/condition', () => {
    expect(rfToEdges(edgesToRF(edges))).toEqual(edges)
  })

  it('error edges get ERROR_EDGE_STYLE and keep semanticType error', () => {
    const errEdge: WorkflowEdge = {
      id: 'e3',
      source_node_id: 'a1',
      source_handle: 'error',
      target_node_id: 's',
      type: 'error',
      condition: null,
    }
    const rf = edgesToRF([errEdge])
    expect(rf[0].style).toBe(ERROR_EDGE_STYLE)
    expect(rfToEdges(rf)[0].type).toBe('error')
  })

  it('null sourceHandle becomes default', () => {
    const e: Edge = { id: 'e9', source: 'a1', target: 's', sourceHandle: null }
    expect(rfToEdges([e])).toEqual([
      { id: 'e9', source_node_id: 'a1', source_handle: 'default', target_node_id: 's', type: 'static', condition: null },
    ])
  })
})
