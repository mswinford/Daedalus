import { useState } from 'react'
import { Plus, Trash2, AlertTriangle } from 'lucide-react'
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
  type HumanInLoopNodeConfig,
  type HumanInputField,
  type FieldMapping,
  type PromptDefinition,
  type AgentSkill,
  type InvokeNodeConfig,
} from '@/lib/workflowTypes'
import InvokeCapabilityPicker from './InvokeCapabilityPicker'

interface Props {
  node: WorkflowNode | null
  models: ModelConfig[]
  tools: ToolDefinition[]
  prompts: PromptDefinition[]
  onConfigChange: (nodeId: string, config: NodeConfig) => void
  onErrorHandlingChange: (nodeId: string, enabled: boolean) => void
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

function AgentEditor({ config, set, models, tools, prompts }: { config: AgentNodeConfig; set: (c: AgentNodeConfig) => void; models: ModelConfig[]; tools: ToolDefinition[]; prompts: PromptDefinition[] }) {
  const skills = config.skills ?? []

  const toggleTool = (id: string) => {
    const has = config.tool_ids.includes(id)
    set({ ...config, tool_ids: has ? config.tool_ids.filter((t) => t !== id) : [...config.tool_ids, id] })
  }

  const updateSkill = (i: number, patch: Partial<AgentSkill>) => {
    const next = skills.map((s, idx) => (idx === i ? { ...s, ...patch } : s))
    set({ ...config, skills: next })
  }

  const toggleSkillTool = (i: number, id: string) => {
    const s = skills[i]
    const has = s.tool_ids.includes(id)
    updateSkill(i, { tool_ids: has ? s.tool_ids.filter((t) => t !== id) : [...s.tool_ids, id] })
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
      {prompts.length > 0 && (
        <Field label="Prompt template">
          <select className={inputCls} value={config.prompt_ref ?? ''} onChange={(e) => set({ ...config, prompt_ref: e.target.value || null })}>
            <option value="">— none (use system prompt below) —</option>
            {prompts.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </Field>
      )}
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

      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">Skills</span>
          <button
            onClick={() => set({ ...config, skills: [...skills, { name: null, prompt: '', tool_ids: [] }] })}
            className="flex items-center gap-0.5 text-xs text-indigo-400 hover:text-indigo-300"
          >
            <Plus size={12} /> Add
          </button>
        </div>
        <div className="space-y-2">
          {skills.length === 0 && (
            <p className="text-xs text-zinc-600">None — skills fold extra prompt + tools into this agent.</p>
          )}
          {skills.map((s, i) => (
            <div key={i} className="space-y-1.5 rounded-md border border-zinc-800 p-2">
              <div className="flex items-center gap-1.5">
                <input
                  className={inputCls}
                  placeholder={`skill_${i + 1}`}
                  value={s.name ?? ''}
                  onChange={(e) => updateSkill(i, { name: e.target.value || null })}
                />
                <button
                  onClick={() => set({ ...config, skills: skills.filter((_, j) => j !== i) })}
                  className="text-red-400 hover:text-red-300"
                >
                  <Trash2 size={14} />
                </button>
              </div>
              <textarea
                className={`${inputCls} min-h-[60px] font-mono text-xs`}
                placeholder="Skill prompt (appended to the system prompt)"
                value={s.prompt}
                onChange={(e) => updateSkill(i, { prompt: e.target.value })}
              />
              {tools.length > 0 && (
                <div className="space-y-1">
                  {tools.map((t) => (
                    <label key={t.id} className="flex items-center gap-2 text-sm text-zinc-300">
                      <input type="checkbox" checked={s.tool_ids.includes(t.id)} onChange={() => toggleSkillTool(i, t.id)} />
                      {t.name}
                    </label>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
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

function HumanInLoopEditor({ config, set }: { config: HumanInLoopNodeConfig; set: (c: HumanInLoopNodeConfig) => void }) {
  const updateField = (idx: number, patch: Partial<HumanInputField>) => {
    const fields = config.input_fields.map((f, i) => (i === idx ? { ...f, ...patch } : f))
    set({ ...config, input_fields: fields })
  }
  const addField = () => {
    set({ ...config, input_fields: [...config.input_fields, { name: '', label: '', type: 'text', required: true }] })
  }
  const removeField = (idx: number) => {
    set({ ...config, input_fields: config.input_fields.filter((_, i) => i !== idx) })
  }

  return (
    <div className="space-y-3">
      <Field label="Prompt / message shown to human">
        <textarea className={`${inputCls} min-h-[60px]`} value={config.approval_message ?? ''} onChange={(e) => set({ ...config, approval_message: e.target.value })} placeholder="What should the human review or provide?" />
      </Field>
      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="hil-approval"
          checked={config.approval_required}
          onChange={(e) => set({ ...config, approval_required: e.target.checked })}
          className="rounded border-zinc-700 bg-zinc-950"
        />
        <label htmlFor="hil-approval" className="text-sm text-zinc-300">Requires explicit approval</label>
      </div>
      <Field label="Timeout (seconds)">
        <NumberInput value={config.timeout_seconds} onChange={(v) => set({ ...config, timeout_seconds: v || null })} min={1} max={86400} />
        <p className="mt-0.5 text-[11px] text-zinc-600">Leave empty to wait indefinitely</p>
      </Field>

      {/* Input fields */}
      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">Input fields</span>
          <button onClick={addField} className="flex items-center gap-0.5 text-xs text-indigo-400 hover:text-indigo-300">
            <Plus size={12} /> Add
          </button>
        </div>
        <div className="space-y-2">
          {config.input_fields.length === 0 && (
            <p className="text-xs text-zinc-600">No fields — human only approves/rejects.</p>
          )}
          {config.input_fields.map((f, i) => (
            <div key={i} className="rounded-md border border-zinc-800 p-2 space-y-1.5">
              <div className="grid grid-cols-2 gap-2">
                <input className={inputCls} placeholder="name" value={f.name} onChange={(e) => updateField(i, { name: e.target.value })} />
                <input className={inputCls} placeholder="label" value={f.label} onChange={(e) => updateField(i, { label: e.target.value })} />
              </div>
              <div className="grid grid-cols-3 gap-2 items-center">
                <select className={inputCls} value={f.type} onChange={(e) => updateField(i, { type: e.target.value as HumanInputField['type'] })}>
                  <option value="text">text</option>
                  <option value="textarea">textarea</option>
                  <option value="select">select</option>
                  <option value="boolean">boolean</option>
                </select>
                <label className="flex items-center gap-1 text-xs text-zinc-400">
                  <input type="checkbox" checked={f.required} onChange={(e) => updateField(i, { required: e.target.checked })} />
                  req
                </label>
                <button onClick={() => removeField(i)} className="text-xs text-red-400 hover:text-red-300">remove</button>
              </div>
              {f.type === 'select' && (
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] text-zinc-500">Options</span>
                    <button onClick={() => updateField(i, { options: [...(f.options ?? []), ''] })} className="flex items-center gap-0.5 text-xs text-indigo-400 hover:text-indigo-300">
                      <Plus size={12} /> Add
                    </button>
                  </div>
                  {(f.options ?? []).map((opt, j) => (
                    <div key={j} className="flex items-center gap-1.5">
                      <input
                        className={inputCls}
                        placeholder={`option_${j + 1}`}
                        value={opt}
                        onChange={(e) => {
                          const options = [...(f.options ?? [])]
                          options[j] = e.target.value
                          updateField(i, { options })
                        }}
                      />
                      <button onClick={() => updateField(i, { options: (f.options ?? []).filter((_, k) => k !== j) })} className="text-red-400 hover:text-red-300">
                        <Trash2 size={14} />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Output fields */}
      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">Output fields</span>
          <button onClick={() => set({ ...config, output_fields: [...config.output_fields, ''] })} className="flex items-center gap-0.5 text-xs text-indigo-400 hover:text-indigo-300">
            <Plus size={12} /> Add
          </button>
        </div>
        <div className="space-y-1">
          {config.output_fields.length === 0 && (
            <p className="text-xs text-zinc-600">None — response merged into state as-is.</p>
          )}
          {config.output_fields.map((f, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <input
                className={inputCls}
                placeholder={`field_${i + 1}`}
                value={f}
                onChange={(e) => {
                  const fields = [...config.output_fields]
                  fields[i] = e.target.value
                  set({ ...config, output_fields: fields })
                }}
              />
              <button
                onClick={() => set({ ...config, output_fields: config.output_fields.filter((_, j) => j !== i) })}
                className="text-red-400 hover:text-red-300"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function InvokeEditor({ config, set }: { config: InvokeNodeConfig; set: (c: InvokeNodeConfig) => void }) {
  const [picking, setPicking] = useState(false)

  const updateMapping = (i: number, patch: Partial<FieldMapping>) => {
    const input_mapping = config.input_mapping.map((m, idx) => (idx === i ? { ...m, ...patch } : m))
    set({ ...config, input_mapping })
  }

  return (
    <div className="space-y-3">
      <Field label="Capability">
        <div className="flex items-center gap-1.5">
          <input
            className={inputCls}
            value={config.capability}
            placeholder="owner/name"
            onChange={(e) => set({ ...config, capability: e.target.value })}
          />
          <button
            onClick={() => setPicking(true)}
            className="shrink-0 rounded-md border border-zinc-700 px-2 py-1.5 text-xs font-medium text-zinc-200 hover:bg-zinc-800"
          >
            Browse
          </button>
        </div>
      </Field>
      <Field label="Version">
        <input className={inputCls} value={config.version} placeholder="latest" onChange={(e) => set({ ...config, version: e.target.value })} />
        <p className="mt-0.5 text-[11px] text-zinc-600">'latest' resolves once at run start and is pinned for the run's lifetime.</p>
      </Field>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">Input mapping (parent → sub)</span>
          <button
            onClick={() => set({ ...config, input_mapping: [...config.input_mapping, { source: '', target: '' }] })}
            className="flex items-center gap-0.5 text-xs text-indigo-400 hover:text-indigo-300"
          >
            <Plus size={12} /> Add
          </button>
        </div>
        <div className="space-y-1">
          {config.input_mapping.length === 0 && (
            <p className="text-xs text-zinc-600">None — the sub starts with an empty data frame.</p>
          )}
          {config.input_mapping.map((m, i) => (
            <div key={i} className="flex items-center gap-1">
              <input className={inputCls} value={m.source} placeholder="data.message" onChange={(e) => updateMapping(i, { source: e.target.value })} />
              <span className="text-zinc-600">→</span>
              <input className={inputCls} value={m.target} placeholder="sub field" onChange={(e) => updateMapping(i, { target: e.target.value })} />
              <button onClick={() => set({ ...config, input_mapping: config.input_mapping.filter((_, idx) => idx !== i) })} className="text-zinc-500 hover:text-red-400">
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          <p className="text-[11px] text-zinc-600">Source paths resolve from the state root — run inputs live under data.* (e.g. data.message).</p>
        </div>
      </div>

      <Field label="Output field">
        <input className={inputCls} value={config.output_field} placeholder="result" onChange={(e) => set({ ...config, output_field: e.target.value })} />
        <p className="mt-0.5 text-[11px] text-zinc-600">Where the sub's result lands in the parent data.</p>
      </Field>

      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="invoke-set-output"
          checked={config.set_output}
          onChange={(e) => set({ ...config, set_output: e.target.checked })}
          className="rounded border-zinc-700 bg-zinc-950"
        />
        <label htmlFor="invoke-set-output" className="text-sm text-zinc-300">Also copy the sub's final output into the parent output</label>
      </div>

      {picking && (
        <InvokeCapabilityPicker
          onPick={(name) => {
            set({ ...config, capability: name })
            setPicking(false)
          }}
          onClose={() => setPicking(false)}
        />
      )}
    </div>
  )
}

// ─── panel shell ────────────────────────────────────────────────────────────

export default function ConfigPanel({ node, models, tools, prompts, onConfigChange, onErrorHandlingChange, onDeleteNode, edges }: Props) {
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
      {node.type === 'agent' && <AgentEditor config={node.config} set={set} models={models} tools={tools} prompts={prompts} />}
      {node.type === 'conditional' && <ConditionalEditor config={node.config} set={set} nodeId={node.id} edges={edges} />}
      {node.type === 'transform' && <TransformEditor config={node.config} set={set} />}
      {node.type === 'custom_function' && <CustomFunctionEditor config={node.config} set={set} />}
      {node.type === 'human_in_loop' && <HumanInLoopEditor config={node.config as HumanInLoopNodeConfig} set={(c) => onConfigChange(node.id, c)} />}
      {node.type === 'invoke' && <InvokeEditor config={node.config} set={(c) => onConfigChange(node.id, c)} />}

      {node.type !== 'start' && node.type !== 'end' && (
        <div className="rounded-md border border-zinc-800 p-3">
          <label className="flex cursor-pointer items-start gap-2.5">
            <input
              type="checkbox"
              checked={node.error_handling ?? false}
              onChange={(e) => onErrorHandlingChange(node.id, e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-red-500"
            />
            <span>
              <span className="flex items-center gap-1.5 text-sm font-medium text-zinc-200">
                <AlertTriangle size={13} className={node.error_handling ? 'text-red-400' : 'text-zinc-500'} />
                Error handling
              </span>
              <span className="mt-0.5 block text-xs text-zinc-500">
                {node.error_handling
                  ? 'Failures route to the red error handle instead of stopping the run.'
                  : 'Opt in to add a red error handle for routing failures.'}
              </span>
            </span>
          </label>
        </div>
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
