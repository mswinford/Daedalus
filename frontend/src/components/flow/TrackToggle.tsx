interface Props {
  checked: boolean
  onChange: (v: boolean) => void
}

export default function TrackToggle({ checked, onChange }: Props) {
  return (
    <label
      className="flex shrink-0 cursor-pointer select-none items-center gap-1 text-[11px] font-medium text-zinc-500 hover:text-zinc-300"
      title="Live ref: at run start this entry is re-resolved from the registry to the newest published version within the same major. Breaking (major) updates still require a manual upgrade."
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-3 w-3 accent-emerald-500"
      />
      live
    </label>
  )
}
