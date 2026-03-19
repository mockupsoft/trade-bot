/** Demo dashboard payloads; replace with `/api/...` when wired to analytics. */

export type TradeRow = {
  id: string
  symbol: 'BTCUSDT' | 'ETHUSDT'
  qty: string
  entry: string
  exit: string
  pnlUsd: number
  reason: string
  closedAt: string
}

export const mockTrades: TradeRow[] = [
  {
    id: 't-1042',
    symbol: 'BTCUSDT',
    qty: '0.042',
    entry: '96,420.50',
    exit: '97,105.00',
    pnlUsd: 287.45,
    reason: 'signal:ema_cross + risk:approved',
    closedAt: '2025-03-18 14:22 UTC',
  },
  {
    id: 't-1041',
    symbol: 'ETHUSDT',
    qty: '1.20',
    entry: '3,412.10',
    exit: '3,388.40',
    pnlUsd: -28.44,
    reason: 'exit:trailing_stop',
    closedAt: '2025-03-18 11:05 UTC',
  },
  {
    id: 't-1040',
    symbol: 'BTCUSDT',
    qty: '0.038',
    entry: '95,880.00',
    exit: '96,210.25',
    pnlUsd: 125.5,
    reason: 'exit:take_profit',
    closedAt: '2025-03-17 22:18 UTC',
  },
  {
    id: 't-1039',
    symbol: 'ETHUSDT',
    qty: '2.00',
    entry: '3,355.00',
    exit: '3,401.80',
    pnlUsd: 93.6,
    reason: 'signal:rsi_recovery',
    closedAt: '2025-03-17 08:44 UTC',
  },
  {
    id: 't-1038',
    symbol: 'BTCUSDT',
    qty: '0.040',
    entry: '94,200.00',
    exit: '93,950.00',
    pnlUsd: -100.0,
    reason: 'exit:stop_loss',
    closedAt: '2025-03-16 19:30 UTC',
  },
]

export type EquityPoint = { t: string; equity: number }

/** Cumulative equity curve (paper). */
export const mockEquitySeries: EquityPoint[] = (() => {
  const base = 10000
  let v = base
  const out: EquityPoint[] = []
  for (let i = 0; i < 36; i++) {
    v += (Math.sin(i / 3) * 120 + (i % 5) * 18 - 40) * 0.85
    out.push({
      t: `D${String(i + 1).padStart(2, '0')}`,
      equity: Math.round(v * 100) / 100,
    })
  }
  return out
})()

export type VolumeBar = { name: string; notionalM: number; fill: string }

export const mockVolumeBySymbol: VolumeBar[] = [
  { name: 'BTCUSDT', notionalM: 42.8, fill: 'var(--cte-btc)' },
  { name: 'ETHUSDT', notionalM: 28.3, fill: 'var(--cte-eth)' },
]

export const mockKpis = {
  equity: '10,247.32',
  dayPnl: '+184.20',
  winRate: '58.3%',
  maxDd: '4.1%',
  openRisk: 'LOW',
}
