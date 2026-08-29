import { useState } from 'react'
import { Plus, Trash2, X, Wrench } from 'lucide-react'

import type {
  ToolDefinition,
  ToolImplementationType,
  JsonSchemaParam,
  ToolParamRow,
} from '@/lib/workflowTypes'

interface Props {
  tools: ToolDefinition[]
  onChange: (tools: ToolDefinition[]) => void
  onClose: () => void
}

const inputCls =
  'w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100 outline-none focus:border-indigo-500'
const labelCls = 'mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500'

const IMPL_LABEL: Record<ToolImplementationType, string> = {
  builtin: 'builtin',
  custom_function: 'custom function',
  http: 'http',
}

// Convert a stored parameters record into editable rows (and back).
function toRows(params: Record<string, JsonSchemaParam>): ToolParamRow[] {
  return Object.entries(params ?? {}).map(([key, value]) => ({ key, value }))
}

function rowsToParams(rows: ToolParamRow[]): Record<string, JsonSchemaParam> {
  const out: Record<string, JsonSchemaParam> = {}
  for (const r of rows) {
    const k = r.key.trim()
    if (!k) continue
    const param: JsonSchemaParam = { type: r.value.type, required: r.value.required }
    if (r.value.description?.trim()) param.description = r.value.description.trim()
    if (r.value.enum && r.value.enum.length) param.enum = r.value.enum
    out[k] = param
  }
  return out
}

interface HeaderRow { name: string; value: string }

// Convert a stored headers object into editable rows (and back).
function toHeaderRows(headers: unknown): HeaderRow[] {
  if (!headers || typeof headers !== 'object') return []
  return Object.entries(headers as Record<string, unknown>).map(([name, value]) => ({
    name,
    value: String(value ?? ''),
  }))
}

function ToolForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: ToolDefinition | null
  onSave: (t: ToolDefinition) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [rows, setRows] = useState<ToolParamRow[]>(toRows(initial?.parameters ?? {}))
  const [implType, setImplType] = useState<ToolImplementationType>(initial?.implementation.type ?? 'custom_function')

  // builtin
  const [builtinFn, setBuiltinFn] = useState(
    (initial?.implementation.config.function as string) ?? '',
  )
  // custom_function
  const [code, setCode] = useState((initial?.implementation.config.code as string) ?? '')
  const [timeout, setTimeout_] = useState(
    (initial?.implementation.config.timeout_seconds as number) ?? 30,
  )
  // http
  const [url, setUrl] = useState((initial?.implementation.config.url as string) ?? '')
  const [method, setMethod] = useState(
    ((initial?.implementation.config.method as string) ?? 'GET').toUpperCase(),
  )
  const [httpTimeout, setHttpTimeout] = useState(
    (initial?.implementation.config.timeout_seconds as number) ?? 30,
  )
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>(
    toHeaderRows(initial?.implementation.config.headers),
  )

  const updateRow = (i: number, patch: Partial<ToolParamRow>) => {
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  }
  const updateParam = (i: number, patch: Partial<JsonSchemaParam>) => {
    setRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, value: { ...r.value, ...patch } } : r)))
  }

  const canSave = name.trim() !== '' && description.trim() !== ''

  const handleSubmit = () => {
    if (!canSave) return
    let config: Record<string, unknown> = {}
    if (implType === 'builtin') {
      config = { function: builtinFn.trim() }
    } else if (implType === 'custom_function') {
      config = { code, timeout_seconds: timeout }
    } else {
      const headers: Record<string, string> = {}
      for (const h of headerRows) {
        const n = h.name.trim()
        if (n) headers[n] = h.value
      }
      config = { url: url.trim(), method, timeout_seconds: httpTimeout }
      if (Object.keys(headers).length > 0) config.headers = headers
    }
    onSave({
      id: initial?.id ?? crypto.randomUUID(),
      name: name.trim(),
      description: description.trim(),
      parameters: rowsToParams(rows),
      implementation: { type: implType, config },
    })
  }

  return (
    <div className="space-y-3 rounded-md border border-zinc-700 bg-zinc-900 p-3">
      <p className="text-sm font-medium text-zinc-200">{initial ? 'Edit tool' : 'Add tool'}</p>

      <div className="grid grid-cols-1 gap-2">
        <label className="block">
          <span className={labelCls}>Name</span>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="lookup_order" />
        </label>
        <label className="block">
          <span className={labelCls}>Description (shown to the LLM)</span>
          <textarea className={`${inputCls} min-h-[56px]`} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What this tool does and when to use it" />
        </label>
      </div>

      {/* Parameters */}
      <div>
        <span className={labelCls}>Parameters</span>
        {rows.map((r, i) => (
          <div key={i} className="mb-2 rounded-md border border-zinc-800 p-2">
            <div className="flex items-center gap-1">
              <input
                className={inputCls}
                value={r.key}
                placeholder="param name"
                onChange={(e) => updateRow(i, { key: e.target.value })}
              />
              <button
                onClick={() => setRows((rs) => rs.filter((_, idx) => idx !== i))}
                className="shrink-0 text-zinc-500 hover:text-red-400"
              >
                <Trash2 size={13} />
              </button>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-2">
              <label className="block">
                <span className={labelCls}>Type</span>
                <select
                  className={inputCls}
                  value={r.value.type}
                  onChange={(e) => updateParam(i, { type: e.target.value as JsonSchemaParam['type'] })}
                >
                  <option value="string">string</option>
                  <option value="number">number</option>
                  <option value="boolean">boolean</option>
                  <option value="array">array</option>
                  <option value="object">object</option>
                </select>
              </label>
              <label className="flex items-end pb-1.5 text-sm text-zinc-300">
                <input
                  type="checkbox"
                  checked={r.value.required}
                  onChange={(e) => updateParam(i, { required: e.target.checked })}
                />
                <span className="ml-2">required</span>
              </label>
            </div>
            <label className="mt-2 block">
              <span className={labelCls}>Description</span>
              <input
                className={inputCls}
                value={r.value.description ?? ''}
                onChange={(e) => updateParam(i, { description: e.target.value })}
                placeholder="What this parameter is"
              />
            </label>
          </div>
        ))}
        <button
          onClick={() => setRows((rs) => [...rs, { key: '', value: { type: 'string', required: false } }])}
          className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300"
        >
          <Plus size={13} /> Add parameter
        </button>
      </div>

      {/* Implementation */}
      <div>
        <span className={labelCls}>Implementation</span>
        <select
          className={inputCls}
          value={implType}
          onChange={(e) => setImplType(e.target.value as ToolImplementationType)}
        >
          <option value="custom_function">custom function (sandboxed python)</option>
          <option value="builtin">builtin</option>
          <option value="http">http request</option>
        </select>

        {implType === 'builtin' && (
          <label className="mt-2 block">
            <span className={labelCls}>Function name</span>
            <input className={inputCls} value={builtinFn} onChange={(e) => setBuiltinFn(e.target.value)} placeholder="echo" />
            <span className="mt-1 block text-[11px] text-zinc-600">Available builtins: echo</span>
          </label>
        )}

        {implType === 'custom_function' && (
          <div className="mt-2 space-y-2">
            <label className="block">
              <span className={labelCls}>Python code (sandboxed)</span>
              <textarea
                className={`${inputCls} min-h-[100px] font-mono text-xs`}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder={'order_id = state["arguments"]["order_id"]\nresult["found"] = ...'}
              />
              <span className="mt-1 block text-[11px] text-zinc-600">
                Read inputs from <code>state[''arguments'']</code>; set outputs on <code>result</code>.
              </span>
            </label>
            <label className="block w-40">
              <span className={labelCls}>Timeout (s)</span>
              <input type="number" className={inputCls} value={timeout} min={1} max={300} onChange={(e) => setTimeout_(Number(e.target.value))} />
            </label>
          </div>
        )}

        {implType === 'http' && (
          <div className="mt-2 space-y-2">
            <label className="block">
              <span className={labelCls}>URL</span>
              <input className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://api.github.com/repos/{owner}/{repo}" />
              <span className="mt-1 block text-[11px] text-zinc-600">
                Use {'{param}'} to fill the path or query from tool arguments.
              </span>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="block">
                <span className={labelCls}>Method</span>
                <select className={inputCls} value={method} onChange={(e) => setMethod(e.target.value.toUpperCase())}>
                  <option value="GET">GET</option>
                  <option value="POST">POST</option>
                  <option value="PUT">PUT</option>
                  <option value="PATCH">PATCH</option>
                  <option value="DELETE">DELETE</option>
                </select>
              </label>
              <label className="block">
                <span className={labelCls}>Timeout (s)</span>
                <input type="number" className={inputCls} value={httpTimeout} min={1} max={300} onChange={(e) => setHttpTimeout(Number(e.target.value))} />
              </label>
            </div>

            {/* Headers */}
            <div>
              <span className={labelCls}>Headers</span>
              {headerRows.map((h, i) => (
                <div key={i} className="mb-1 flex items-center gap-1">
                  <input
                    className={inputCls}
                    value={h.name}
                    placeholder="Authorization"
                    onChange={(e) => setHeaderRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, name: e.target.value } : r)))}
                  />
                  <input
                    className={inputCls}
                    value={h.value}
                    placeholder="Bearer ${GITHUB_TOKEN}"
                    onChange={(e) => setHeaderRows((rs) => rs.map((r, idx) => (idx === i ? { ...r, value: e.target.value } : r)))}
                  />
                  <button
                    onClick={() => setHeaderRows((rs) => rs.filter((_, idx) => idx !== i))}
                    className="shrink-0 text-zinc-500 hover:text-red-400"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
              <button
                onClick={() => setHeaderRows((rs) => [...rs, { name: '', value: '' }])}
                className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300"
              >
                <Plus size={13} /> Add header
              </button>
              <span className="mt-1 block text-[11px] text-zinc-600">
                Values support {'{param}'} (arguments) and {'${ENV_VAR}'} for secrets read from the environment.
              </span>
            </div>

            <span className="block text-[11px] text-zinc-600">Remaining arguments are sent as query params (GET) or JSON body (other methods).</span>
          </div>
        )}
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button onClick={onCancel} className="rounded-md border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-800">
          Cancel
        </button>
        <button
          onClick={handleSubmit}
          disabled={!canSave}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {initial ? 'Save' : 'Add'}
        </button>
      </div>
    </div>
  )
}

export default function ToolsPanel({ tools, onChange, onClose }: Props) {
  const [editing, setEditing] = useState<ToolDefinition | null>(null)
  const [adding, setAdding] = useState(false)

  const handleSave = (t: ToolDefinition) => {
    if (editing) {
      onChange(tools.map((x) => (x.id === t.id ? t : x)))
    } else {
      onChange([...tools, t])
    }
    setEditing(null)
    setAdding(false)
  }

  const handleDelete = (id: string) => {
    onChange(tools.filter((t) => t.id !== id))
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="max-h-[80vh] w-[520px] overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-900 p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-100">
            <Wrench size={16} /> Tools
          </h2>
          <button onClick={onClose} className="rounded p-1 text-zinc-400 hover:text-zinc-100">
            <X size={16} />
          </button>
        </div>

        {tools.length > 0 && (
          <div className="mb-3 space-y-2">
            {tools.map((t) => (
              <div key={t.id} className="flex items-center justify-between rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-zinc-200">{t.name}</p>
                  <p className="truncate text-[11px] text-zinc-500">
                    {IMPL_LABEL[t.implementation.type]}
                    {Object.keys(t.parameters).length > 0 && ` · ${Object.keys(t.parameters).length} param(s)`}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button onClick={() => { setEditing(t); setAdding(false) }} className="rounded p-1 text-zinc-400 hover:text-indigo-400">
                    Edit
                  </button>
                  <button onClick={() => handleDelete(t.id)} className="rounded p-1 text-zinc-500 hover:text-red-400">
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {tools.length === 0 && !adding && !editing && (
          <p className="mb-3 text-sm text-zinc-500">No tools defined. Add one to give agents capabilities.</p>
        )}

        {(adding || editing) ? (
          <ToolForm
            initial={editing}
            onSave={handleSave}
            onCancel={() => { setAdding(false); setEditing(null) }}
          />
        ) : (
          <button
            onClick={() => { setAdding(true); setEditing(null) }}
            className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300"
          >
            <Plus size={13} /> Add tool
          </button>
        )}
      </div>
    </div>
  )
}
