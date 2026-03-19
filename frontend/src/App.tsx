import { Panel } from './components/Panel'
import { EquityChart } from './components/EquityChart'
import { VolumeChart } from './components/VolumeChart'
import { MiniSparkline } from './components/MiniSparkline'
import { StatCard } from './components/StatCard'
import { TradeTable } from './components/TradeTable'
import { Sidebar } from './components/Sidebar'
import {
  mockEquitySeries,
  mockKpis,
  mockTrades,
  mockVolumeBySymbol,
} from './data/mock'

function sparkFromEquity(values: number[], take = 14) {
  return values.slice(-take)
}

export default function App() {
  const equities = mockEquitySeries.map((p) => p.equity)
  const sparkEquity = sparkFromEquity(equities)
  const sparkDay = sparkEquity.map((v, i) => v * (1 + Math.sin(i) * 0.002))

  return (
    <div className="flex min-h-dvh bg-[var(--cte-canvas)]">
      <Sidebar />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 flex-shrink-0 items-center justify-between border-b border-[var(--cte-border)] bg-[var(--cte-surface)]/80 px-6 backdrop-blur-md">
          <div>
            <h1 className="text-base font-semibold tracking-tight">Dashboard</h1>
            <p className="text-xs text-[var(--cte-muted)]">
              Paper performance · explainable decisions · risk veto
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="rounded-full bg-[var(--cte-positive)]/15 px-2.5 py-1 text-[11px] font-medium text-[var(--cte-positive)]">
              Engine healthy
            </span>
            <time className="font-mono text-xs text-[var(--cte-subtle)]">
              {new Date().toISOString().slice(0, 16).replace('T', ' ')} UTC
            </time>
          </div>
        </header>

        <div className="flex-1 space-y-6 overflow-auto p-6">
          <section aria-label="Key metrics">
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard
                label="Equity"
                value={`$${mockKpis.equity}`}
                hint="Paper account"
                chart={
                  <MiniSparkline data={sparkEquity} stroke="var(--cte-accent)" />
                }
              />
              <StatCard
                label="Today PnL"
                value={mockKpis.dayPnl}
                hint="Realized"
                trend="up"
                chart={
                  <MiniSparkline data={sparkDay} stroke="var(--cte-positive)" />
                }
              />
              <StatCard
                label="Win rate"
                value={mockKpis.winRate}
                hint="Last 30 closed"
                chart={
                  <MiniSparkline
                    data={sparkEquity.map((v) => v * 0.01 + 50)}
                    stroke="var(--cte-btc)"
                  />
                }
              />
              <StatCard
                label="Max drawdown"
                value={mockKpis.maxDd}
                hint="Peak to trough"
                trend="neutral"
                chart={
                  <MiniSparkline
                    data={[...sparkEquity].reverse()}
                    stroke="var(--cte-negative)"
                  />
                }
              />
            </div>
          </section>

          <section className="grid gap-6 lg:grid-cols-5" aria-label="Charts">
            <Panel
              className="lg:col-span-3"
              title="Equity curve"
              subtitle="Cumulative paper equity · UTC"
              noPadding
            >
              <div className="px-5 pb-5 pt-2">
                <EquityChart data={mockEquitySeries} />
              </div>
            </Panel>
            <Panel
              className="lg:col-span-2"
              title="Venue notional"
              subtitle="24h rolling (demo)"
              noPadding
            >
              <div className="px-5 pb-5 pt-2">
                <VolumeChart data={mockVolumeBySymbol} />
              </div>
            </Panel>
          </section>

          <Panel
            title="Recent exits"
            subtitle="Reason payloads align with CTE event model"
            noPadding
          >
            <div className="p-5 pt-2">
              <TradeTable rows={mockTrades} />
            </div>
          </Panel>
        </div>
      </main>
    </div>
  )
}
