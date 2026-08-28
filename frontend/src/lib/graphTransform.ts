import { type Node, type Edge } from '@xyflow/react'

import {
  type NodeType,
  type WorkflowNode,
  type WorkflowEdge,
  type ConditionalNodeConfig,
} from './workflowTypes'

// Data carried by every React Flow node. `config` is the live (editable) backend config;
// `branchHandles` is derived for conditional nodes; `validation` is a transient highlight flag.
export interface FlowNodeData extends Record<string, unknown> {
  nodeType: NodeType
  config: WorkflowNode['config']
  branchHandles?: string[]
  validation?: 'error' | 'warning'
}

// A React Flow node carrying our data. Used as the generic for useNodesState / NodeProps.
export type FlowNodeType = Node<FlowNodeData>

// Source handles for a node, in render order. Conditional nodes get one handle per
// condition (positionally matched to outgoing branch edges) plus the default/fallback.
export function sourceHandlesFor(node: WorkflowNode, edges: WorkflowEdge[]): string[] {
  if (node.type === 'end') return []
  if (node.type !== 'conditional') return ['default']

  const cfg = node.config as ConditionalNodeConfig
  const branches = edges.filter((e) => e.source_node_id === node.id && e.source_handle !== 'default')
  const handles: string[] = []
  const seen = new Set<string>()
  cfg.conditions.forEach((_cond, i) => {
    let name = branches[i]?.source_handle ?? `branch_${i + 1}`
    while (seen.has(name)) name = `${name}_`
    seen.add(name)
    handles.push(name)
  })
  const fallback = cfg.default_branch ?? 'default'
  if (!seen.has(fallback)) {
    seen.add(fallback)
    handles.push(fallback)
  }
  return handles
}

export function nodesToRF(nodes: WorkflowNode[], edges: WorkflowEdge[]): FlowNodeType[] {
  return nodes.map((n) => ({
    id: n.id,
    type: n.type,
    position: { x: n.position.x, y: n.position.y },
    data: {
      nodeType: n.type,
      config: n.config,
      branchHandles: sourceHandlesFor(n, edges),
    },
  }))
}

export function edgesToRF(edges: WorkflowEdge[]): Edge[] {
  return edges.map((e) => ({
    id: e.id,
    source: e.source_node_id,
    sourceHandle: e.source_handle,
    target: e.target_node_id,
    type: 'default',
    data: { semanticType: e.type, condition: e.condition ?? null },
  }))
}

export function rfToNodes(nodes: FlowNodeType[]): WorkflowNode[] {
  // Reconstruct the discriminated union; the invariant (nodeType matches config) is
  // maintained by the editor, so a cast is safe here.
  return nodes.map((n) => ({
    id: n.id,
    type: n.data.nodeType,
    position: { x: n.position.x, y: n.position.y },
    config: n.data.config,
  })) as WorkflowNode[]
}

export function rfToEdges(edges: Edge[]): WorkflowEdge[] {
  return edges.map((e) => ({
    id: e.id,
    source_node_id: e.source,
    source_handle: e.sourceHandle ?? 'default',
    target_node_id: e.target,
    type: (e.data?.semanticType as WorkflowEdge['type']) ?? 'static',
    condition: (e.data?.condition as WorkflowEdge['condition']) ?? null,
  }))
}

