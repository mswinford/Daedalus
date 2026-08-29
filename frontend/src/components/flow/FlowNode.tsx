import { useEffect } from 'react'
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from '@xyflow/react'
import { Play, Flag, Bot, GitBranch, Shuffle, User, Code2, AlertTriangle } from 'lucide-react'

import { NODE_META, type NodeType } from '@/lib/workflowTypes'
import { type FlowNodeData } from '@/lib/graphTransform'

const ICONS: Record<NodeType, typeof Play> = {
  start: Play,
  end: Flag,
  agent: Bot,
  conditional: GitBranch,
  transform: Shuffle,
  human_in_loop: User,
  custom_function: Code2,
}

function subtitle(nodeType: NodeType, config: FlowNodeData['config']): string {
  switch (nodeType) {
    case 'start':
      return `${(config as { input_fields: string[] }).input_fields.length} inputs`
    case 'end':
      return `${(config as { output_fields: string[] }).output_fields.length} outputs`
    case 'agent': {
      const c = config as { model_id: string }
      return c.model_id || 'no model'
    }
    case 'conditional': {
      const c = config as { conditions: unknown[]; default_branch?: string | null }
      return `${c.conditions.length} branches → ${c.default_branch ?? 'default'}`
    }
    case 'transform':
      return (config as { mode: string }).mode
    case 'custom_function':
      return 'sandboxed python'
    case 'human_in_loop':
      return (config as { approval_required: boolean }).approval_required ? 'approval gate' : 'human input'
  }
}

export default function FlowNode({ data, selected, id }: NodeProps) {
  const d = data as FlowNodeData
  const meta = NODE_META[d.nodeType]
  const Icon = ICONS[d.nodeType]
  const isStart = d.nodeType === 'start'
  const isEnd = d.nodeType === 'end'
  const handles = d.branchHandles ?? (isEnd ? [] : ['default'])

  const updateNodeInternals = useUpdateNodeInternals()
  const handleKey = handles.join(',')
  useEffect(() => {
    if (!isStart && !isEnd) updateNodeInternals(id)
  }, [handleKey, id, isStart, isEnd, updateNodeInternals])

  const ring =
    d.validation === 'error'
      ? 'ring-2 ring-red-500'
      : d.validation === 'warning'
        ? 'ring-2 ring-amber-400'
        : ''

  return (
    <div
      className={[
        'relative min-w-[168px] rounded-lg border bg-zinc-900 px-3 py-2 shadow-md transition-shadow',
        selected ? 'border-indigo-500' : 'border-zinc-700',
        ring,
      ].join(' ')}
    >
      {!isStart && <Handle type="target" position={Position.Left} id="in" />}

      {d.validation && (
        <AlertTriangle
          size={14}
          className={`absolute -right-2 -top-2 rounded-full bg-zinc-900 p-0.5 ${
            d.validation === 'error' ? 'text-red-500' : 'text-amber-400'
          }`}
        />
      )}

      <div className="flex items-center gap-2">
        <span
          className="flex h-6 w-6 items-center justify-center rounded-md"
          style={{ backgroundColor: `${meta.color}22`, color: meta.color }}
        >
          <Icon size={14} />
        </span>
        <div className="leading-tight">
          <p className="text-sm font-medium text-zinc-100">{meta.label}</p>
          <p className="max-w-[180px] truncate text-[11px] text-zinc-500">{subtitle(d.nodeType, d.config)}</p>
        </div>
      </div>

      {handles.map((h, i) => {
        const top = handles.length > 1 ? `${((i + 1) / (handles.length + 1)) * 100}%` : undefined
        return <Handle key={h} id={h} type="source" position={Position.Right} style={top ? { top } : undefined} />
      })}
    </div>
  )
}
