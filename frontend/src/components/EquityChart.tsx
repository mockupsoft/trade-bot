import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { EquityPoint } from '../data/mock'

type EquityChartProps = {
  data: EquityPoint[]
}

const axisTick = { fill: 'var(--cte-muted)', fontSize: 11 }
const gridStroke = 'var(--cte-grid)'

export function EquityChart({ data }: EquityChartProps) {
  return (
    <div className="h-[280px] w-full" role="img" aria-label="Equity curve chart">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--cte-accent)" stopOpacity={0.25} />
              <stop offset="100%" stopColor="var(--cte-accent)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 6" stroke={gridStroke} vertical={false} />
          <XAxis
            dataKey="t"
            axisLine={false}
            tickLine={false}
            tick={axisTick}
            interval="preserveStartEnd"
            dy={6}
          />
          <YAxis
            axisLine={false}
            tickLine={false}
            tick={axisTick}
            domain={['auto', 'auto']}
            width={56}
            tickFormatter={(v) =>
              v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
            }
          />
          <Tooltip
            contentStyle={{
              background: 'var(--cte-tooltip-bg)',
              border: '1px solid var(--cte-border)',
              borderRadius: 8,
            }}
            labelStyle={{ color: 'var(--cte-muted)' }}
            formatter={(value) => {
              const n = typeof value === 'number' ? value : Number(value)
              return [
                `$${n.toLocaleString('en-US', { minimumFractionDigits: 2 })}`,
                'Equity',
              ]
            }}
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke="var(--cte-accent)"
            strokeWidth={2}
            fill="url(#equityFill)"
            dot={false}
            activeDot={{ r: 4, strokeWidth: 0, fill: 'var(--cte-accent)' }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
