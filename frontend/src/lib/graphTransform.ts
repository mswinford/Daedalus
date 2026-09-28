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
  label?: string | null
  branchHandles?: string[]
  errorHandling?: boolean
  validation?: 'error' | 'warning'
}

// A React Flow node carrying our data. Used as the generic for useNodesState / NodeProps.
export type FlowNodeType = Node<FlowNodeData>

// Styling for type='error' edges (red dashed) and their source handle.
export const ERROR_EDGE_STYLE = { stroke: '#ef4444', strokeDasharray: '6 3' } as const
export const ERROR_HANDLE_STYLE = { background: '#ef4444', borderColor: '#ef4444' } as const

// Styling for type='conditional' edges (amber) and their condition label.
export const CONDITIONAL_EDGE_STYLE = { stroke: '#f59e0b' } as const
const EDGE_LABEL_STYLE = { fill: '#d4d4d8', fontSize: 10 } as const
const EDGE_LABEL_BG = { fill: '#18181b', stroke: '#3f3f46' } as const

/** Visual props for an edge by semantic type. Conditional edges carry their condition as a label. */
export function edgeVisuals(e: {
  type?: WorkflowEdge['type']
  condition?: WorkflowEdge['condition']
}): Pick<Edge, 'style' | 'label' | 'labelStyle' | 'labelBgStyle'> {
  if (e.type === 'error') return { style: ERROR_EDGE_STYLE, label: undefined, labelStyle: undefined, labelBgStyle: undefined }
  if (e.type === 'conditional' && e.condition) {
    const text = ((e.condition.description ?? '').trim() || e.condition.expression).slice(0, 40)
    return { style: CONDITIONAL_EDGE_STYLE, label: text, labelStyle: EDGE_LABEL_STYLE, labelBgStyle: EDGE_LABEL_BG }
  }
  return { style: undefined, label: undefined, labelStyle: undefined, labelBgStyle: undefined }
}

// Source handles for a node, in render order. Conditional nodes get one handle per
// condition (positionally matched to outgoing branch edges) plus the default/fallback.
// Nodes with error_handling opt in to an extra 'error' handle (never on start/end).
export function sourceHandlesFor(node: WorkflowNode, edges: WorkflowEdge[]): string[] {
  if (node.type === 'end') return []

  const handles = node.type !== 'conditional' ? ['default'] : conditionalHandlesFor(node, edges)

  if (node.type !== 'start' && node.error_handling && !handles.includes('error')) {
    handles.push('error')
  }
  return handles
}

function conditionalHandlesFor(node: WorkflowNode, edges: WorkflowEdge[]): string[] {
  const cfg = node.config as ConditionalNodeConfig
  const branches = edges.filter((e) => e.source_node_id === node.id && e.source_handle !== 'default' && e.type !== 'error')
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
      label: n.label ?? null,
      branchHandles: sourceHandlesFor(n, edges),
      errorHandling: n.error_handling ?? false,
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
    ...edgeVisuals(e),
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
    error_handling: n.data.errorHandling ?? false,
    // Omitted when unset so unlabeled workflows keep their old file shape.
    ...(n.data.label != null ? { label: n.data.label } : {}),
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

