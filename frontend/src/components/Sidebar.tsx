const nav = [
  { label: 'Overview', active: true },
  { label: 'Signals', active: false },
  { label: 'Risk', active: false },
  { label: 'Execution', active: false },
] as const

export function Sidebar() {
  return (
    <aside className="flex w-[220px] flex-shrink-0 flex-col border-r border-[var(--cte-border)] bg-[var(--cte-surface)]">
      <div className="flex h-14 items-center gap-2 border-b border-[var(--cte-border)] px-5">
        <div
          className="flex h-8 w-8 items-center justify-center rounded-lg font-mono text-xs font-bold text-white"
          style={{
            background:
              'linear-gradient(135deg, var(--cte-accent) 0%, var(--cte-accent-muted) 100%)',
          }}
        >
          CTE
        </div>
        <div>
          <p className="text-sm font-semibold tracking-tight">Control</p>
          <p className="text-[10px] uppercase tracking-widest text-[var(--cte-muted)]">
            Paper · v1
          </p>
        </div>
      </div>
      <nav className="flex flex-1 flex-col gap-0.5 p-3" aria-label="Primary">
        {nav.map((item) => (
          <button
            key={item.label}
            type="button"
            disabled={!item.active}
            className={
              item.active
                ? 'rounded-lg bg-[var(--cte-elevated)] px-3 py-2.5 text-left text-sm font-medium text-[var(--cte-text)] ring-1 ring-[var(--cte-border)]'
                : 'cursor-not-allowed rounded-lg px-3 py-2.5 text-left text-sm text-[var(--cte-subtle)] opacity-60'
            }
          >
            {item.label}
          </button>
        ))}
      </nav>
      <footer className="border-t border-[var(--cte-border)] p-4 text-[11px] leading-relaxed text-[var(--cte-muted)]">
        Binance USDⓈ-M · Bybit public
        <br />
        <span className="text-[var(--cte-subtle)]">BTCUSDT · ETHUSDT · LONG only</span>
      </footer>
    </aside>
  )
}
