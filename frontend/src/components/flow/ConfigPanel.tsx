import { Plus, Trash2 } from 'lucide-react'
import type { Edge } from '@xyflow/react'

import {
  type WorkflowNode,
  type NodeConfig,
  type ModelConfig,
  type ToolDefinition,
  type AgentNodeConfig,
  type ConditionalNodeConfig,
  type TransformNodeConfig,
  type CustomFunctionNodeConfig,
  type StartNodeConfig,
  type EndNodeConfig,
  type FieldMapping,
} from '@/lib/workflowTypes'

interface Props {
  node: WorkflowNode | null
  models: ModelConfig[]
  tools: ToolDefinition[]
  onConfigChange: (nodeId: string, config: NodeConfig) => void
  onDeleteNode: (nodeId: string) => void
  edges: Edge[]
}

// ─── small form primitives ──────────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500">{label}</span>
      {children}
    </label>
  )
}

const inputCls =
  'w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100 outline-none focus:border-indigo-500'

function ListField({ value, onChange, placeholder }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  return (
    <input
      className={inputCls}
      value={value.join(', ')}
      placeholder={placeholder ?? 'comma, separated'}
      onChange={(e) => onChange(e.target.value.split(',').map((s) => s.trim()).filter(Boolean))}
    />
  )
}

function NumberInput({ value, onChange, min, max }: { value: number | null | undefined; onChange: (v: number) => void; min?: number; max?: number }) {
  return (
    <input
      type="number"
      className={inputCls}
      value={value ?? ''}
      min={min}
      max={max}
      onChange={(e) => onChange(Number(e.target.value))}
    />
  )
}

// ─── per-type editors ───────────────────────────────────────────────────────

function StartEditor({ config, set }: { config: StartNodeConfig; set: (c: StartNodeConfig) => void }) {
  return (
    <Field label="Input fields">
      <ListField value={config.input_fields} onChange={(v) => set({ ...config, input_fields: v })} placeholder="e.g. query, context" />
    </Field>
  )
}

function EndEditor({ config, set }: { config: EndNodeConfig; set: (c: EndNodeConfig) => void }) {
  return (
    <Field label="Output fields">
      <ListField value={config.output_fields} onChange={(v) => set({ ...config, output_fields: v })} placeholder="e.g. answer" />
    </Field>
  )
}

function AgentEditor({ config, set, models, tools }: { config: AgentNodeConfig; set: (c: AgentNodeConfig) => void; models: ModelConfig[]; tools: ToolDefinition[] }) {
  const toggleTool = (id: string) => {
    const has = config.tool_ids.includes(id)
    set({ ...config, tool_ids: has ? config.tool_ids.filter((t) => t !== id) : [...config.tool_ids, id] })
  }
  return (
    <div className="space-y-3">
      <Field label="Model">
        <select className={inputCls} value={config.model_id} onChange={(e) => set({ ...config, model_id: e.target.value })}>
          <option value="">— select —</option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </select>
      </Field>
      <Field label="System prompt">
        <textarea className={`${inputCls} min-h-[90px] font-mono text-xs`} value={config.system_prompt} onChange={(e) => set({ ...config, system_prompt: e.target.value })} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Temperature">
          <NumberInput value={config.temperature} onChange={(v) => set({ ...config, temperature: v })} min={0} max={2} />
        </Field>
        <Field label="Max iterations">
          <NumberInput value={config.max_iterations} onChange={(v) => set({ ...config, max_iterations: v })} min={1} max={100} />
        </Field>
      </div>
      {tools.length > 0 && (
        <Field label="Tools">
          <div className="space-y-1">
            {tools.map((t) => (
              <label key={t.id} className="flex items-center gap-2 text-sm text-zinc-300">
                <input type="checkbox" checked={config.tool_ids.includes(t.id)} onChange={() => toggleTool(t.id)} />
                {t.name}
              </label>
            ))}
          </div>
        </Field>
      )}
    </div>
  )
}

function ConditionalEditor({ config, set, nodeId, edges }: { config: ConditionalNodeConfig; set: (c: ConditionalNodeConfig) => void; nodeId: string; edges: Edge[] }) {
  const update = (i: number, patch: Partial<ConditionalNodeConfig['conditions'][number]>) => {
    const conditions = config.conditions.map((c, idx) => (idx === i ? { ...c, ...patch } : c))
    set({ ...config, conditions })
  }

  // Derive the handle name for condition index i (matches sourceHandlesFor logic).
  const branches = edges.filter((e) => e.source === nodeId && e.sourceHandle !== 'default')
  const handleFor = (i: number) => branches[i]?.sourceHandle ?? `branch_${i + 1}`
  const hasConnectedEdge = (i: number) => {
    const h = handleFor(i)
    return edges.some((e) => e.source === nodeId && e.sourceHandle === h)
  }

  return (
    <div className="space-y-3">
      {config.conditions.map((c, i) => (
        <div key={i} className="rounded-md border border-zinc-800 p-2">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-medium text-zinc-400">Branch {i + 1}</span>
            <button
              onClick={() => set({ ...config, conditions: config.conditions.filter((_, idx) => idx !== i) })}
              disabled={hasConnectedEdge(i)}
              title={hasConnectedEdge(i) ? 'Disconnect this branch edge first' : undefined}
              className="text-zinc-500 hover:text-red-400 disabled:cursor-not-allowed disabled:opacity-30"
            >
              <Trash2 size={13} />
            </button>
          </div>
          <div className="space-y-2">
            <select className={inputCls} value={c.type} onChange={(e) => update(i, { type: e.target.value as typeof c.type })}>
              <option value="json_path">json_path</option>
              <option value="regex">regex (phase 4)</option>
              <option value="llm">llm (phase 4)</option>
            </select>
            <input className={inputCls} value={c.expression} placeholder='e.g. $.data.score >= 0.8' onChange={(e) => update(i, { expression: e.target.value })} />
          </div>
        </div>
      ))}
      <button onClick={() => set({ ...config, conditions: [...config.conditions, { type: 'json_path', expression: '' }] })} className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300">
        <Plus size={13} /> Add branch
      </button>
      <Field label="Default branch (fallback)">
        <input className={inputCls} value={config.default_branch ?? ''} placeholder="default" onChange={(e) => set({ ...config, default_branch: e.target.value || null })} />
      </Field>
    </div>
  )
}

function TransformEditor({ config, set }: { config: TransformNodeConfig; set: (c: TransformNodeConfig) => void }) {
  const updateMapping = (i: number, patch: Partial<FieldMapping>) => {
    const field_mappings = (config.field_mappings ?? []).map((m, idx) => (idx === i ? { ...m, ...patch } : m))
    set({ ...config, field_mappings })
  }
  return (
    <div className="space-y-3">
      <Field label="Mode">
        <select className={inputCls} value={config.mode} onChange={(e) => set({ ...config, mode: e.target.value as TransformNodeConfig['mode'] })}>
          <option value="template">template</option>
          <option value="mapping">mapping</option>
          <option value="custom_function">custom_function (phase 1 gap)</option>
        </select>
      </Field>
      {config.mode === 'template' && (
        <Field label="Template">
          <textarea className={`${inputCls} min-h-[70px] font-mono text-xs`} value={config.template ?? ''} placeholder="Hello {{name}}" onChange={(e) => set({ ...config, template: e.target.value })} />
        </Field>
      )}
      {config.mode === 'mapping' && (
        <div className="space-y-2">
          {(config.field_mappings ?? []).map((m, i) => (
            <div key={i} className="flex items-center gap-1">
              <input className={inputCls} value={m.source} placeholder="source" onChange={(e) => updateMapping(i, { source: e.target.value })} />
              <span className="text-zinc-600">→</span>
              <input className={inputCls} value={m.target} placeholder="target" onChange={(e) => updateMapping(i, { target: e.target.value })} />
              <button onClick={() => set({ ...config, field_mappings: (config.field_mappings ?? []).filter((_, idx) => idx !== i) })} className="text-zinc-500 hover:text-red-400">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          <button onClick={() => set({ ...config, field_mappings: [...(config.field_mappings ?? []), { source: '', target: '' }] })} className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300">
            <Plus size={13} /> Add mapping
          </button>
        </div>
      )}
      {config.mode === 'custom_function' && (
        <Field label="Custom function node id">
          <input className={inputCls} value={config.custom_function_id ?? ''} onChange={(e) => set({ ...config, custom_function_id: e.target.value || null })} />
        </Field>
      )}
      <Field label="Output field">
        <input className={inputCls} value={config.output_field} onChange={(e) => set({ ...config, output_field: e.target.value })} />
      </Field>
    </div>
  )
}

function CustomFunctionEditor({ config, set }: { config: CustomFunctionNodeConfig; set: (c: CustomFunctionNodeConfig) => void }) {
  return (
    <div className="space-y-3">
      <Field label="Python code (sandboxed)">
        <textarea className={`${inputCls} min-h-[120px] font-mono text-xs`} value={config.code} onChange={(e) => set({ ...config, code: e.target.value })} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Timeout (s)">
          <NumberInput value={config.timeout_seconds} onChange={(v) => set({ ...config, timeout_seconds: v })} min={1} max={300} />
        </Field>
      </div>
      <Field label="Input fields">
        <ListField value={config.input_fields} onChange={(v) => set({ ...config, input_fields: v })} />
      </Field>
      <Field label="Output fields">
        <ListField value={config.output_fields} onChange={(v) => set({ ...config, output_fields: v })} />
      </Field>
    </div>
  )
}

// ─── panel shell ────────────────────────────────────────────────────────────

export default function ConfigPanel({ node, models, tools, onConfigChange, onDeleteNode, edges }: Props) {
  if (!node) {
    return (
      <div className="text-sm text-zinc-500">Select a node to configure it.</div>
    )
  }

  const set = (config: NodeConfig) => onConfigChange(node.id, config)

  return (
    <div className="space-y-3">
      <div className="border-b border-zinc-800 pb-2">
        <p className="text-xs text-zinc-500">Editing node</p>
        <p className="font-mono text-sm text-zinc-200">{node.id}</p>
      </div>

      {node.type === 'start' && <StartEditor config={node.config} set={set} />}
      {node.type === 'end' && <EndEditor config={node.config} set={set} />}
      {node.type === 'agent' && <AgentEditor config={node.config} set={set} models={models} tools={tools} />}
      {node.type === 'conditional' && <ConditionalEditor config={node.config} set={set} nodeId={node.id} edges={edges} />}
      {node.type === 'transform' && <TransformEditor config={node.config} set={set} />}
      {node.type === 'custom_function' && <CustomFunctionEditor config={node.config} set={set} />}
      {node.type === 'human_in_loop' && (
        <p className="text-sm text-zinc-500">Human-in-loop nodes are not editable until Phase 3.</p>
      )}

      <div className="border-t border-zinc-800 pt-3">
        <button
          onClick={() => onDeleteNode(node.id)}
          className="flex w-full items-center justify-center gap-1.5 rounded-md border border-red-900/50 px-3 py-1.5 text-sm font-medium text-red-400 hover:bg-red-950/30"
        >
          <Trash2 size={14} />
          Delete node
        </button>
      </div>
    </div>
  )
}
