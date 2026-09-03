import { semverCompare } from '@/lib/capabilityUpdates'

interface Props {
  current: string | null
  latest?: string | null
  breaking?: boolean
  tracking?: boolean
}

export default function CapabilityVersionBadge({ current, latest, breaking, tracking }: Props) {
  const hasUpdate = !!latest && (current == null || semverCompare(latest, current) > 0)
  if (!hasUpdate && !current) return null
  const live = tracking ? <span className="ml-1 text-emerald-400">· live</span> : null
  if (hasUpdate) {
    return (
      <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] ${breaking ? 'bg-red-500/15 text-red-400' : 'bg-amber-500/15 text-amber-400'}`}>
        → v{latest}
        {live}
      </span>
    )
  }
  return (
    <span className="shrink-0 rounded bg-zinc-800 px-1.5 py-0.5 font-mono text-[11px] text-zinc-500">
      v{current}
      {live}
    </span>
  )
}
