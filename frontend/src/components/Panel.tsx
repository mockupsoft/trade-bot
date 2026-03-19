import type { ReactNode } from 'react'
import clsx from 'clsx'

type PanelProps = {
  title: string
  subtitle?: string
  action?: ReactNode
  children: ReactNode
  className?: string
  noPadding?: boolean
}

/**
 * Card shell shared by charts and tables for visual consistency.
 */
export function Panel({
  title,
  subtitle,
  action,
  children,
  className,
  noPadding,
}: PanelProps) {
  return (
    <section
      className={clsx(
        'flex flex-col overflow-hidden rounded-xl border border-[var(--cte-border)] bg-[var(--cte-surface)] shadow-[0_1px_0_rgba(255,255,255,0.04)_inset]',
        className,
      )}
    >
      <header className="flex flex-shrink-0 items-start justify-between gap-4 border-b border-[var(--cte-border)] bg-[var(--cte-elevated)] px-5 py-4">
        <div>
          <h2 className="text-[15px] font-semibold tracking-tight text-[var(--cte-text)]">
            {title}
          </h2>
          {subtitle ? (
            <p className="mt-0.5 text-xs text-[var(--cte-muted)]">{subtitle}</p>
          ) : null}
        </div>
        {action ? <div className="flex items-center gap-2">{action}</div> : null}
      </header>
      <div className={clsx('min-h-0 flex-1', noPadding ? '' : 'p-5')}>{children}</div>
    </section>
  )
}
