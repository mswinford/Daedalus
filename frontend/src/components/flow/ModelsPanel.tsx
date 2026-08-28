import { useState } from 'react'
import { Plus, Trash2, X, Cpu } from 'lucide-react'

import type { ModelConfig } from '@/lib/workflowTypes'

interface Props {
  models: ModelConfig[]
  onChange: (models: ModelConfig[]) => void
  onClose: () => void
}

const inputCls =
  'w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100 outline-none focus:border-indigo-500'

function ModelForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: ModelConfig | null
  onSave: (m: ModelConfig) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [model, setModel] = useState(initial?.model ?? '')
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? '')
  const [apiKey, setApiKey] = useState(initial?.api_key_ref ?? '')
  const [temperature, setTemperature] = useState(initial?.default_temperature ?? 0.7)

  const handleSubmit = () => {
    if (!name.trim() || !model.trim()) return
    onSave({
      id: initial?.id ?? crypto.randomUUID(),
      name: name.trim(),
      provider: 'openai_compatible',
      model: model.trim(),
      base_url: baseUrl.trim() || null,
      api_key_ref: apiKey.trim() || null,
      default_temperature: temperature,
      track_cost: initial?.track_cost ?? false,
      pricing: initial?.pricing ?? null,
    })
  }

  return (
    <div className="space-y-3 rounded-md border border-zinc-700 bg-zinc-900 p-3">
      <p className="text-sm font-medium text-zinc-200">{initial ? 'Edit model' : 'Add model'}</p>
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500">Name</span>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="My LLM" />
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500">Model</span>
          <input className={inputCls} value={model} onChange={(e) => setModel(e.target.value)} placeholder="llama3, gpt-4o" />
        </label>
      </div>
      <label className="block">
        <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500">Base URL</span>
        <input className={inputCls} value={baseUrl ?? ''} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://localhost:11434/v1" />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500">API Key</span>
          <input className={inputCls} value={apiKey ?? ''} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-... (empty for local)" type="password" />
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500">Temperature</span>
          <input type="number" className={inputCls} value={temperature} min={0} max={2} step={0.1} onChange={(e) => setTemperature(Number(e.target.value))} />
        </label>
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <button onClick={onCancel} className="rounded-md border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-800">
          Cancel
        </button>
        <button
          onClick={handleSubmit}
          disabled={!name.trim() || !model.trim()}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {initial ? 'Save' : 'Add'}
        </button>
      </div>
    </div>
  )
}

export default function ModelsPanel({ models, onChange, onClose }: Props) {
  const [editing, setEditing] = useState<ModelConfig | null>(null)
  const [adding, setAdding] = useState(false)

  const handleSave = (m: ModelConfig) => {
    if (editing) {
      onChange(models.map((x) => (x.id === m.id ? m : x)))
    } else {
      onChange([...models, m])
    }
    setEditing(null)
    setAdding(false)
  }

  const handleDelete = (id: string) => {
    onChange(models.filter((m) => m.id !== id))
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="max-h-[80vh] w-[480px] overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-900 p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-100">
            <Cpu size={16} /> Models
          </h2>
          <button onClick={onClose} className="rounded p-1 text-zinc-400 hover:text-zinc-100">
            <X size={16} />
          </button>
        </div>

        {models.length > 0 && (
          <div className="mb-3 space-y-2">
            {models.map((m) => (
              <div key={m.id} className="flex items-center justify-between rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2">
                <div>
                  <p className="text-sm font-medium text-zinc-200">{m.name}</p>
                  <p className="text-[11px] text-zinc-500">
                    {m.model}
                    {m.base_url ? ` · ${m.base_url}` : ''}
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  <button onClick={() => { setEditing(m); setAdding(false) }} className="rounded p-1 text-zinc-400 hover:text-indigo-400">
                    Edit
                  </button>
                  <button onClick={() => handleDelete(m.id)} className="rounded p-1 text-zinc-500 hover:text-red-400">
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {models.length === 0 && !adding && !editing && (
          <p className="mb-3 text-sm text-zinc-500">No models configured. Add one to use agent nodes.</p>
        )}

        {(adding || editing) ? (
          <ModelForm
            initial={editing}
            onSave={handleSave}
            onCancel={() => { setAdding(false); setEditing(null) }}
          />
        ) : (
          <button
            onClick={() => { setAdding(true); setEditing(null) }}
            className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300"
          >
            <Plus size={13} /> Add model
          </button>
        )}
      </div>
    </div>
  )
}
