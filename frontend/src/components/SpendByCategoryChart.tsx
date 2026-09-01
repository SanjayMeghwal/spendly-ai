import type { CategorySpend } from '../api/reports'
import { formatMoney } from '../lib/format'

// Ranking magnitude, not distinguishing identity - per the dataviz skill's
// form table ("compare magnitude -> sequential, one hue"), every bar takes
// the same hue; length alone encodes the amount. Coloring bars by their own
// value here would re-encode what the bar length already shows.
const BAR_COLOR = '#2a78d6'

interface SpendByCategoryChartProps {
  data: CategorySpend[]
}

export function SpendByCategoryChart({ data }: SpendByCategoryChartProps) {
  const max = Math.max(1, ...data.map((row) => Number(row.spent)))

  return (
    <ul className="space-y-2.5">
      {data.map((row) => {
        const pct = (Number(row.spent) / max) * 100
        return (
          <li key={row.category_id ?? 'uncategorized'} className="flex items-center gap-3">
            <span className="w-28 shrink-0 truncate text-sm text-slate-700" title={row.category_name}>
              {row.category_name}
            </span>
            {/* Bar capped at 16px thick (under the 24px spec max); rounded
                only at the value end, square at the baseline (left edge). */}
            <div className="h-4 flex-1 overflow-hidden rounded bg-slate-100">
              <div
                className="h-full rounded-r"
                style={{ width: `${pct}%`, backgroundColor: BAR_COLOR }}
              />
            </div>
            <span className="w-20 shrink-0 text-right text-sm text-slate-600">
              ${formatMoney(row.spent)}
            </span>
          </li>
        )
      })}
    </ul>
  )
}
