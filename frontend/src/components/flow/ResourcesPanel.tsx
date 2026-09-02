import { useState } from 'react'
import { Cpu, Layers, PackagePlus, Plus, Trash2, Wrench, X } from 'lucide-react'

import type { ModelConfig, ToolDefinition } from '@/lib/workflowTypes'
import type { UpdateStatus } from '@/lib/capabilityUpdates'
import CapabilityVersionBadge from './CapabilityVersionBadge'
import ToolForm, { IMPL_LABEL } from './ToolForm'
import ModelForm from './ModelForm'

interface Props {
  tools: ToolDefinition[]
  models: ModelConfig[]
  updates?: UpdateStatus[]
  onToolsChange: (tools: ToolDefinition[]) => void
  onModelsChange: (models: ModelConfig[]) => void
  onOpenRegistry: (kind: 'tool' | 'model_profile') => void
  onClose: () => void
}

const sectionCls = 'mb-4 rounded-md border border-zinc-800 bg-zinc-950/60 p-3 last:mb-0'
const rowBtnCls = 'rounded p-1 text-zinc-400 hover:text-indigo-400'
const linkBtnCls = 'flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300'

export default function ResourcesPanel({
  tools,
  models,
  updates,
  onToolsChange,
  onModelsChange,
  onOpenRegistry,
  onClose,
}: Props) {
  const [editingTool, setEditingTool] = useState<ToolDefinition | null>(null)
  const [addingTool, setAddingTool] = useState(false)
  const [editingModel, setEditingModel] = useState<ModelConfig | null>(null)
  const [addingModel, setAddingModel] = useState(false)

  const saveTool = (t: ToolDefinition) => {
    onToolsChange(
      editingTool ? tools.map((x) => (x.id === t.id ? t : x)) : [...tools, t],
    )
    setEditingTool(null)
    setAddingTool(false)
  }

  const saveModel = (m: ModelConfig) => {
    onModelsChange(
      editingModel ? models.map((x) => (x.id === m.id ? m : x)) : [...models, m],
    )
    setEditingModel(null)
    setAddingModel(false)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="max-h-[80vh] w-[560px] overflow-y-auto rounded-lg border border-zinc-700 bg-zinc-900 p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-100">
            <Layers size={16} /> Workflow resources
          </h2>
          <button onClick={onClose} className="rounded p-1 text-zinc-400 hover:text-zinc-100">
            <X size={16} />
          </button>
        </div>

        {/* Tools */}
        <div className={sectionCls}>
          <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
            <Wrench size={12} /> Tools
          </p>

          {tools.length === 0 && !addingTool && !editingTool && (
            <p className="mb-2 text-sm text-zinc-500">No tools defined. Add one to give agents capabilities.</p>
          )}

          <div className="space-y-2">
            {tools.map((t) => {
              const tu = updates?.find((u) => u.kind === 'tool' && u.where === t.id)
              return (
                <div key={t.id} className={`rounded-md border ${editingTool?.id === t.id ? 'border-indigo-500' : 'border-zinc-800'} bg-zinc-950 px-3 py-2`}>
                  <div className="flex items-center justify-between">
                    <div className="min-w-0">
                      <p className="flex items-center gap-1.5 text-sm font-medium text-zinc-200">
                        <span className="truncate">{t.name}</span>
                        {tu && <CapabilityVersionBadge current={tu.currentVersion} latest={tu.latestVersion} breaking={tu.isBreaking} />}
                      </p>
                      <p className="truncate text-[11px] text-zinc-500">
                        {IMPL_LABEL[t.implementation.type]}
                        {Object.keys(t.parameters).length > 0 && ` · ${Object.keys(t.parameters).length} param(s)`}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <button
                        onClick={() => { setEditingTool(editingTool?.id === t.id ? null : t); setAddingTool(false) }}
                        className={rowBtnCls}
                      >
                        {editingTool?.id === t.id ? 'Close' : 'Edit'}
                      </button>
                      <button
                        onClick={() => onToolsChange(tools.filter((x) => x.id !== t.id))}
                        className="rounded p-1 text-zinc-500 hover:text-red-400"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                  {editingTool?.id === t.id && (
                    <div className="mt-2">
                      <ToolForm initial={t} onSave={saveTool} onCancel={() => setEditingTool(null)} />
                    </div>
                  )}
                </div>
              )
            })}

            {addingTool && (
              <ToolForm initial={null} onSave={saveTool} onCancel={() => setAddingTool(false)} />
            )}
          </div>

          <div className="mt-2 flex items-center gap-4">
            <button onClick={() => { setAddingTool(true); setEditingTool(null) }} className={linkBtnCls}>
              <Plus size={13} /> New custom tool
            </button>
            <button onClick={() => onOpenRegistry('tool')} className={linkBtnCls}>
              <PackagePlus size={13} /> From registry
            </button>
          </div>
        </div>

        {/* Models */}
        <div className={sectionCls}>
          <p className="mb-2 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
            <Cpu size={12} /> Models
          </p>

          {models.length === 0 && !addingModel && !editingModel && (
            <p className="mb-2 text-sm text-zinc-500">No models configured. Add one to use agent nodes.</p>
          )}

          <div className="space-y-2">
            {models.map((m) => {
              const mu = updates?.find((u) => u.kind === 'model_profile' && u.where === m.id)
              return (
                <div key={m.id} className={`rounded-md border ${editingModel?.id === m.id ? 'border-indigo-500' : 'border-zinc-800'} bg-zinc-950 px-3 py-2`}>
                  <div className="flex items-center justify-between">
                    <div className="min-w-0">
                      <p className="flex items-center gap-1.5 text-sm font-medium text-zinc-200">
                        <span className="truncate">{m.name}</span>
                        {mu && <CapabilityVersionBadge current={mu.currentVersion} latest={mu.latestVersion} breaking={mu.isBreaking} />}
                      </p>
                      <p className="truncate text-[11px] text-zinc-500">
                        {m.model}
                        {m.base_url ? ` · ${m.base_url}` : ''}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <button
                        onClick={() => { setEditingModel(editingModel?.id === m.id ? null : m); setAddingModel(false) }}
                        className={rowBtnCls}
                      >
                        {editingModel?.id === m.id ? 'Close' : 'Edit'}
                      </button>
                      <button
                        onClick={() => onModelsChange(models.filter((x) => x.id !== m.id))}
                        className="rounded p-1 text-zinc-500 hover:text-red-400"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                  {editingModel?.id === m.id && (
                    <div className="mt-2">
                      <ModelForm initial={m} onSave={saveModel} onCancel={() => setEditingModel(null)} />
                    </div>
                  )}
                </div>
              )
            })}

            {addingModel && (
              <ModelForm initial={null} onSave={saveModel} onCancel={() => setAddingModel(false)} />
            )}
          </div>

          <div className="mt-2 flex items-center gap-4">
            <button onClick={() => { setAddingModel(true); setEditingModel(null) }} className={linkBtnCls}>
              <Plus size={13} /> New custom model
            </button>
            <button onClick={() => onOpenRegistry('model_profile')} className={linkBtnCls}>
              <PackagePlus size={13} /> From registry
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
