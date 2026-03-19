import type { ReactNode } from 'react'
import clsx from 'clsx'

type StatCardProps = {
  label: string
  value: string
  hint?: string
  trend?: 'up' | 'down' | 'neutral'
  chart?: ReactNode
}

export function StatCard({ label, value, hint, trend = 'neutral', chart }: StatCardProps) {
  const trendColor =
    trend === 'up'
      ? 'text-[var(--cte-positive)]'
      : trend === 'down'
        ? 'text-[var(--cte-negative)]'
        : 'text-[var(--cte-text)]'

  return (
    <article className="flex min-h-[108px] flex-col justify-between rounded-xl border border-[var(--cte-border)] bg-[var(--cte-surface)] p-4 shadow-[0_1px_0_rgba(255,255,255,0.04)_inset]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wider text-[var(--cte-muted)]">
            {label}
          </p>
          <p
            className={clsx(
              'mt-1 font-mono text-xl font-semibold tabular-nums tracking-tight',
              trendColor,
            )}
          >
            {value}
          </p>
          {hint ? (
            <p className="mt-1 text-xs text-[var(--cte-subtle)]">{hint}</p>
          ) : null}
        </div>
        {chart ? (
          <div className="h-12 w-[88px] flex-shrink-0 opacity-90">{chart}</div>
        ) : null}
      </div>
    </article>
  )
}
