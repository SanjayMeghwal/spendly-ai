interface StatTileProps {
  label: string
  value: string
  valueClassName?: string
}

// Stat-tile contract per the dataviz skill: label (sentence case, no
// trailing colon), value in the default proportional figures (never
// tabular-nums - that's for columns of aligned numbers, not a standalone
// figure). Delta/trend are optional per that contract and omitted here for
// this first dashboard slice.
export function StatTile({ label, value, valueClassName }: StatTileProps) {
  return (
    <div className="rounded-lg border border-slate-200 p-4">
      <p className="text-sm text-slate-500">{label}</p>
      <p className={`text-2xl font-semibold ${valueClassName ?? 'text-slate-900'}`}>{value}</p>
    </div>
  )
}
