import clsx from 'clsx'
import type { TradeRow } from '../data/mock'

type TradeTableProps = {
  rows: TradeRow[]
}

function PnlCell({ value }: { value: number }) {
  const positive = value >= 0
  return (
    <span
      className={clsx(
        'inline-flex rounded-md px-2 py-0.5 font-mono text-sm font-medium tabular-nums',
        positive
          ? 'bg-[var(--cte-positive)]/12 text-[var(--cte-positive)]'
          : 'bg-[var(--cte-negative)]/12 text-[var(--cte-negative)]',
      )}
    >
      {positive ? '+' : ''}
      {value.toLocaleString('en-US', { style: 'currency', currency: 'USD' })}
    </span>
  )
}

function SymbolBadge({ symbol }: { symbol: TradeRow['symbol'] }) {
  const isBtc = symbol === 'BTCUSDT'
  return (
    <span
      className={clsx(
        'rounded-md px-2 py-0.5 font-mono text-xs font-semibold',
        isBtc
          ? 'bg-[var(--cte-btc)]/15 text-[var(--cte-btc)]'
          : 'bg-[var(--cte-eth)]/15 text-[var(--cte-eth)]',
      )}
    >
      {symbol}
    </span>
  )
}

export function TradeTable({ rows }: TradeTableProps) {
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--cte-border)]">
      <table className="w-full min-w-[720px] border-collapse text-left text-sm">
        <thead>
          <tr className="border-b border-[var(--cte-border)] bg-[var(--cte-elevated)]">
            <th
              scope="col"
              className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wider text-[var(--cte-muted)]"
            >
              Id
            </th>
            <th
              scope="col"
              className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wider text-[var(--cte-muted)]"
            >
              Symbol
            </th>
            <th
              scope="col"
              className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wider text-[var(--cte-muted)]"
            >
              Qty
            </th>
            <th
              scope="col"
              className="px-4 py-3 text-right text-[11px] font-semibold uppercase tracking-wider text-[var(--cte-muted)]"
            >
              Entry
            </th>
            <th
              scope="col"
              className="px-4 py-3 text-right text-[11px] font-semibold uppercase tracking-wider text-[var(--cte-muted)]"
            >
              Exit
            </th>
            <th
              scope="col"
              className="px-4 py-3 text-right text-[11px] font-semibold uppercase tracking-wider text-[var(--cte-muted)]"
            >
              PnL
            </th>
            <th
              scope="col"
              className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wider text-[var(--cte-muted)]"
            >
              Reason chain
            </th>
            <th
              scope="col"
              className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wider text-[var(--cte-muted)]"
            >
              Closed
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--cte-border)] bg-[var(--cte-surface)]">
          {rows.map((row) => (
            <tr
              key={row.id}
              className="transition-colors hover:bg-[var(--cte-elevated)]/60"
            >
              <td className="px-4 py-3 font-mono text-xs text-[var(--cte-subtle)]">
                {row.id}
              </td>
              <td className="px-4 py-3">
                <SymbolBadge symbol={row.symbol} />
              </td>
              <td className="px-4 py-3 font-mono tabular-nums text-[var(--cte-text)]">
                {row.qty}
              </td>
              <td className="px-4 py-3 text-right font-mono text-sm tabular-nums text-[var(--cte-text)]">
                {row.entry}
              </td>
              <td className="px-4 py-3 text-right font-mono text-sm tabular-nums text-[var(--cte-text)]">
                {row.exit}
              </td>
              <td className="px-4 py-3 text-right">
                <PnlCell value={row.pnlUsd} />
              </td>
              <td className="max-w-[220px] px-4 py-3 font-mono text-xs leading-relaxed text-[var(--cte-muted)]">
                {row.reason}
              </td>
              <td className="whitespace-nowrap px-4 py-3 text-xs text-[var(--cte-subtle)]">
                {row.closedAt}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
