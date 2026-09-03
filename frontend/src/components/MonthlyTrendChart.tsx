import type { MonthlySummary } from '../api/reports'
import { formatCompactMoney, formatMoney } from '../lib/format'

// Validated categorical palette, slots 1 and 2 (see the dataviz skill's
// palette.md) - income and expenses are genuinely distinct series
// (categorical color job), not a magnitude ramp, so each gets its own
// fixed hue rather than a light/dark step of one color.
const INCOME_COLOR = '#2a78d6'
const EXPENSES_COLOR = '#eb6834'

const CHART_HEIGHT = 120 // px, the plotted area only - excludes labels

interface MonthlyTrendChartProps {
  data: MonthlySummary[]
}

function shortMonth(month: string): string {
  const [year, m] = month.split('-')
  return new Date(Number(year), Number(m) - 1, 1).toLocaleDateString(undefined, {
    month: 'short',
  })
}

export function MonthlyTrendChart({ data }: MonthlyTrendChartProps) {
  const max = Math.max(1, ...data.flatMap((d) => [Number(d.income), Number(d.expenses)]))

  return (
    <div>
      {/* Legend - always present for 2+ series, the dependable identity
          channel independent of the bar-tip labels below. */}
      <div className="mb-4 flex items-center gap-4 text-sm text-slate-600">
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: INCOME_COLOR }}
          />
          Income
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ backgroundColor: EXPENSES_COLOR }}
          />
          Expenses
        </span>
      </div>

      <div className="flex items-end gap-3 overflow-x-auto pb-1">
        {data.map((d) => {
          const incomeHeight = Math.max(2, (Number(d.income) / max) * CHART_HEIGHT)
          const expensesHeight = Math.max(2, (Number(d.expenses) / max) * CHART_HEIGHT)
          return (
            <div key={d.month} className="flex flex-shrink-0 flex-col items-center gap-1">
              <div className="flex items-end gap-1" style={{ height: CHART_HEIGHT + 16 }}>
                <div className="flex flex-col items-center justify-end">
                  {/* Bars -> value at the tip (cap, for a column). Bar
                      thickness capped at 24px per the mark spec; a 4px
                      rounded data-end at the tip, square at the baseline.
                      Zero is never labeled - a "0" on every empty month is
                      exactly the "number on every point" the skill warns
                      against; the near-invisible 2px bar already reads as
                      "nothing happened". */}
                  {Number(d.income) > 0 && (
                    <span
                      className="mb-0.5 text-[10px] text-slate-500"
                      title={`$${formatMoney(d.income)}`}
                    >
                      {formatCompactMoney(d.income)}
                    </span>
                  )}
                  <div
                    className="w-5 rounded-t"
                    style={{ height: incomeHeight, backgroundColor: INCOME_COLOR }}
                  />
                </div>
                <div className="flex flex-col items-center justify-end">
                  {Number(d.expenses) > 0 && (
                    <span
                      className="mb-0.5 text-[10px] text-slate-500"
                      title={`$${formatMoney(d.expenses)}`}
                    >
                      {formatCompactMoney(d.expenses)}
                    </span>
                  )}
                  <div
                    className="w-5 rounded-t"
                    style={{ height: expensesHeight, backgroundColor: EXPENSES_COLOR }}
                  />
                </div>
              </div>
              <span className="text-xs text-slate-500">{shortMonth(d.month)}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
