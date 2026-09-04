import { ArrowRight } from 'lucide-react'
import type { Edge } from '@xyflow/react'

import type { ConditionConfig, WorkflowEdge } from '@/lib/workflowTypes'

const inputCls =
  'w-full rounded-md border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none'

interface Props {
  edge: Edge
  sourceName: string
  targetName: string
  onChange: (patch: { type: 'static' | 'conditional'; condition: ConditionConfig | null }) => void
}

export default function EdgeInspector({ edge, sourceName, targetName, onChange }: Props) {
  const semanticType = (edge.data?.semanticType as WorkflowEdge['type']) ?? 'static'
  const condition = (edge.data?.condition as ConditionConfig | null) ?? null
  const isConditional = semanticType === 'conditional'

  const setType = (t: 'static' | 'conditional') => {
    if (t === semanticType) return
    onChange({ type: t, condition: t === 'conditional' ? { type: 'json_path', expression: '' } : null })
  }

  const updateCondition = (patch: Partial<ConditionConfig>) => {
    onChange({ type: 'conditional', condition: { ...(condition ?? { type: 'json_path', expression: '' }), ...patch } })
  }

  return (
    <div className="space-y-3">
      <div>
        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-600">From → To</p>
        <p className="flex items-center gap-1.5 text-sm font-medium text-zinc-200">
          <span className="truncate">{sourceName}</span>
          <ArrowRight size={13} className="shrink-0 text-zinc-500" />
          <span className="truncate">{targetName}</span>
        </p>
      </div>

      <div>
        <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-600">Type</p>
        <div className="flex gap-0.5 rounded-md border border-zinc-800 bg-zinc-950 p-0.5">
          {(['static', 'conditional'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setType(t)}
              className={`flex-1 rounded px-2 py-1 text-xs font-medium capitalize transition-colors ${
                semanticType === t ? 'bg-indigo-500/20 text-indigo-300' : 'text-zinc-500 hover:text-zinc-300'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {isConditional && (
        <div className="space-y-2 rounded-md border border-amber-500/30 bg-zinc-950 p-2">
          <p className="text-[11px] font-medium uppercase tracking-wide text-amber-400/80">Condition</p>
          <select
            className={inputCls}
            value={condition?.type ?? 'json_path'}
            onChange={(e) => updateCondition({ type: e.target.value as ConditionConfig['type'] })}
          >
            <option value="json_path">json_path</option>
            <option value="regex">regex</option>
          </select>
          <input
            className={inputCls}
            value={condition?.expression ?? ''}
            placeholder='e.g. $.data.score >= 0.8'
            onChange={(e) => updateCondition({ expression: e.target.value })}
          />
          <input
            className={inputCls}
            value={condition?.description ?? ''}
            placeholder="Description (shown on the edge)"
            onChange={(e) => updateCondition({ description: e.target.value || null })}
          />
          <p className="text-[11px] leading-snug text-zinc-600">
            Checked in order; if none match, the run follows this node's default (or first static) edge.
          </p>
        </div>
      )}
    </div>
  )
}
