import { useId } from 'react'
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  YAxis,
} from 'recharts'

type Point = { i: number; v: number }

type MiniSparklineProps = {
  data: number[]
  stroke: string
}

/** Tiny trend strip for KPI cards; matches main chart stroke colors. */
export function MiniSparkline({ data, stroke }: MiniSparklineProps) {
  const gradId = useId().replace(/:/g, '')
  const series: Point[] = data.map((v, i) => ({ i, v }))
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={series} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
        <YAxis domain={['dataMin', 'dataMax']} hide />
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity={0.35} />
            <stop offset="100%" stopColor={stroke} stopOpacity={0} />
          </linearGradient>
        </defs>
        <Area
          type="monotone"
          dataKey="v"
          stroke={stroke}
          strokeWidth={1.5}
          fill={`url(#${gradId})`}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
