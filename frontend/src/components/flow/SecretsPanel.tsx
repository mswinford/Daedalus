import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, X, KeyRound } from 'lucide-react'

import { apiErrorMessage, secretsApi, type SecretInfo } from '@/lib/api'

interface Props {
  onClose: () => void
}

const inputCls =
  'w-full rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1.5 text-sm text-zinc-100 outline-none focus:border-indigo-500'

function SecretForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: { name: string; value?: string } | null
  onSave: (name: string, value: string) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [value, setValue] = useState(initial?.value ?? '')

  const handleSubmit = () => {
    if (!name.trim() || !value) return
    onSave(name.trim(), value)
  }

  return (
    <div className="space-y-3 rounded-md border border-zinc-700 bg-zinc-900 p-3">
      <p className="text-sm font-medium text-zinc-200">{initial ? 'Update secret' : 'Add secret'}</p>
      <label className="block">
        <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500">Name</span>
        <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="GITHUB_TOKEN" />
      </label>
      <label className="block">
        <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-zinc-500">Value</span>
        <input className={inputCls} value={value} onChange={(e) => setValue(e.target.value)} placeholder="sk-..." type="password" />
      </label>
      <div className="flex justify-end gap-2 pt-1">
        <button onClick={onCancel} className="rounded-md border border-zinc-700 px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-800">
          Cancel
        </button>
        <button
          onClick={handleSubmit}
          disabled={!name.trim() || !value}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {initial ? 'Save' : 'Add'}
        </button>
      </div>
    </div>
  )
}

export default function SecretsPanel({ onClose }: Props) {
  const qc = useQueryClient()
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)

  const { data: secrets, isLoading, error } = useQuery({
    queryKey: ['secrets'],
    queryFn: secretsApi.list,
  })

  const upsertMut = useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) => secretsApi.upsert(name, value),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['secrets'] })
      setAdding(false)
      setEditing(null)
    },
  })

  const deleteMut = useMutation({
    mutationFn: (name: string) => secretsApi.remove(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['secrets'] }),
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="max-h-[80vh] w-[480px] overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-900 p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-100">
            <KeyRound size={16} /> Secrets
          </h2>
          <button onClick={onClose} className="rounded p-1 text-zinc-400 hover:text-zinc-100">
            <X size={16} />
          </button>
        </div>

        <p className="mb-3 text-xs leading-relaxed text-zinc-500">
          Secrets are stored in <code className="rounded bg-zinc-800 px-1 py-0.5 text-[11px]">~/.daedalus/secrets.json</code>.
          Reference them in http tool headers or custom functions via{' '}
          <code className="rounded bg-zinc-800 px-1 py-0.5 text-[11px]">{'${NAME}'}</code> or{' '}
          <code className="rounded bg-zinc-800 px-1 py-0.5 text-[11px]">get_secret('NAME')</code>.
          Environment variables take precedence over file values.
        </p>

        {isLoading ? (
          <p className="text-sm text-zinc-500">Loading…</p>
        ) : error ? (
          <p className="mb-3 text-sm text-red-400">Failed to load secrets: {apiErrorMessage(error)}</p>
        ) : secrets && secrets.length > 0 ? (
          <div className="mb-3 space-y-1.5">
            {secrets.map((s: SecretInfo) => (
              <div key={s.name} className="flex items-center justify-between rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2">
                <div>
                  <p className="font-mono text-sm text-zinc-200">{s.name}</p>
                  <p className="text-[11px] text-zinc-500">
                    {s.source === 'env' ? 'environment variable' : 'secrets file'}
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => { setEditing(s.name); setAdding(false) }}
                    className="rounded p-1 text-xs text-zinc-400 hover:text-indigo-400"
                  >
                    Update
                  </button>
                  <button
                    onClick={() => deleteMut.mutate(s.name)}
                    className="rounded p-1 text-zinc-500 hover:text-red-400"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          !adding && !editing && (
            <p className="mb-3 text-sm text-zinc-500">No secrets configured.</p>
          )
        )}

        {(adding || editing) ? (
          <SecretForm
            initial={editing ? { name: editing } : null}
            onSave={(name, value) => upsertMut.mutate({ name, value })}
            onCancel={() => { setAdding(false); setEditing(null) }}
          />
        ) : (
          <button
            onClick={() => { setAdding(true); setEditing(null) }}
            className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300"
          >
            <Plus size={13} /> Add secret
          </button>
        )}
      </div>
    </div>
  )
}
