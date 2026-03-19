import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { VolumeBar } from '../data/mock'

type VolumeChartProps = {
  data: VolumeBar[]
}

const axisTick = { fill: 'var(--cte-muted)', fontSize: 11 }

export function VolumeChart({ data }: VolumeChartProps) {
  return (
    <div className="h-[220px] w-full" role="img" aria-label="Notional volume by symbol">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 6" stroke="var(--cte-grid)" vertical={false} />
          <XAxis dataKey="name" axisLine={false} tickLine={false} tick={axisTick} dy={6} />
          <YAxis
            axisLine={false}
            tickLine={false}
            tick={axisTick}
            width={44}
            tickFormatter={(v) => `${v}M`}
          />
          <Tooltip
            cursor={{ fill: 'rgba(79, 143, 247, 0.08)' }}
            contentStyle={{
              background: 'var(--cte-tooltip-bg)',
              border: '1px solid var(--cte-border)',
              borderRadius: 8,
            }}
            formatter={(value) => {
              const n = typeof value === 'number' ? value : Number(value)
              return [`$${n}M`, '24h notional']
            }}
          />
          <Bar dataKey="notionalM" radius={[6, 6, 0, 0]} maxBarSize={48}>
            {data.map((entry) => (
              <Cell key={entry.name} fill={entry.fill} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
