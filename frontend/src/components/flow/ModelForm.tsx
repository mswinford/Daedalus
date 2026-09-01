import { useState } from 'react'

import { useQuery } from '@tanstack/react-query'

import type { ModelConfig } from '@/lib/workflowTypes'
import { secretsApi } from '@/lib/api'

const inputCls =
  'w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100 outline-none focus:border-indigo-500'
const labelCls = 'mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500'

// A secret name is an env-var-style identifier; anything else (dashes, dots,
// long token-like strings) is almost certainly a pasted key value.
const SECRET_NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

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

  const { data: secrets } = useQuery({
    queryKey: ['secrets'],
    queryFn: secretsApi.list,
  })

  const trimmedKey = apiKey.trim()
  const looksLikeRawKey = trimmedKey !== '' && !SECRET_NAME_RE.test(trimmedKey)

  const handleSubmit = () => {
    if (!name.trim() || !model.trim()) return
    onSave({
      id: initial?.id ?? crypto.randomUUID(),
      name: name.trim(),
      provider: 'openai_compatible',
      model: model.trim(),
      base_url: baseUrl.trim() || null,
      api_key_ref: trimmedKey || null,
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
          <span className={labelCls}>API key secret (name)</span>
          <input
            className={inputCls}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="OPENAI_API_KEY (empty for local)"
            list="model-secret-names"
          />
          <datalist id="model-secret-names">
            {(secrets ?? []).map((s) => (
              <option key={s.name} value={s.name} />
            ))}
          </datalist>
        </label>
        <label className="block">
          <span className={labelCls}>Temperature</span>
          <input type="number" className={inputCls} value={temperature} min={0} max={2} step={0.1} onChange={(e) => setTemperature(Number(e.target.value))} />
        </label>
      </div>
      {looksLikeRawKey && (
        <p className="text-xs text-amber-400">
          This looks like a key value, not a secret name. Store the key in the Secrets panel
          (top bar) and enter its name here — e.g. <code className="text-amber-300">OPENAI_API_KEY</code>.
        </p>
      )}
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
