import { useState } from 'react'

import type { ModelConfig } from '@/lib/workflowTypes'

const inputCls =
  'w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100 outline-none focus:border-indigo-500'
const labelCls = 'mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500'

export default function ModelForm({
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
      <p className="text-sm font-medium text-zinc-200">{initial ? 'Edit model' : 'New custom model'}</p>
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className={labelCls}>Name</span>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="My LLM" />
        </label>
        <label className="block">
          <span className={labelCls}>Model</span>
          <input className={inputCls} value={model} onChange={(e) => setModel(e.target.value)} placeholder="llama3, gpt-4o" />
        </label>
      </div>
      <label className="block">
        <span className={labelCls}>Base URL</span>
        <input className={inputCls} value={baseUrl ?? ''} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://localhost:11434/v1" />
      </label>
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className={labelCls}>API Key</span>
          <input className={inputCls} value={apiKey ?? ''} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-... (empty for local)" type="password" />
        </label>
        <label className="block">
          <span className={labelCls}>Temperature</span>
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
