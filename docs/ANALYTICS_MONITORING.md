# Analytics & Monitoring Layer — Design Document

## Table of Contents

1. [Metrics Definitions](#1-metrics-definitions)
2. [Database Aggregation Plan](#2-database-aggregation-plan)
3. [API Endpoints](#3-api-endpoints)
4. [Grafana Dashboard Sections](#4-grafana-dashboard-sections)
5. [Alert Rules](#5-alert-rules)
6. [Daily Report Schema](#6-daily-report-schema)

---

## 1. Metrics Definitions

### Core Performance Metrics

| Metric | Formula | Meaning |
|---|---|---|
| **Win Rate** | wins / total_trades | Proportion of profitable trades |
| **Expectancy** | Σ(pnl) / total_trades | Average PnL per trade. >0 = profitable system |
| **Profit Factor** | gross_profit / gross_loss | Revenue/cost ratio. >1 = profitable. >2 = strong |
| **Avg Win** | Σ(winning_pnl) / win_count | Mean size of winning trades |
| **Avg Loss** | Σ(losing_pnl) / loss_count | Mean size of losing trades (negative) |
| **Max Drawdown** | max(peak - equity) / peak | Worst peak-to-trough decline |
| **Sharpe Ratio** | mean(pnl) / std(pnl) × √(365 × tpd) | Risk-adjusted return (annualized) |

### Breakdown Metrics

| Metric | Grouping | Purpose |
|---|---|---|
| **PnL by Symbol** | BTCUSDT, ETHUSDT | Which instrument contributes most |
| **PnL by Venue** | binance, bybit, paper | Venue-specific performance |
| **PnL by Tier** | A, B, C | Do Tier A signals actually outperform? |
| **PnL by Exit Reason** | Per ExitReason enum | Which exits are profitable vs destructive |

### Exit Quality Metrics

| Metric | Formula | Meaning |
|---|---|---|
| **Saved Losers** | L1/L2 exits where position was losing | Hard risk / thesis failure prevented deeper loss |
| **Killed Winners** | L2/L3 exits where position was profitable | We cut a trade that was making money |
| **No-Progress Regret** | No-progress exits where MFE > 0.3% | The trade moved, just not fast enough for the budget |
| **Runner Outcomes** | avg R, avg PnL, win rate for runner-mode positions | Are runners actually capturing extra profit? |

### Execution Quality Metrics

| Metric | Formula | Meaning |
|---|---|---|
| **Signal-to-Fill Latency** | fill_time - signal_time (ms) | How long from decision to execution |
| **Avg Slippage** | mean(modeled_slippage_bps) | Execution cost from best touch to fill |
| **Slippage Drift** | live_slippage - paper_slippage | Is our paper fill model realistic? |
| **Feed Stale Count** | Σ(L1 stale_data exits) | How often data quality kills positions |
| **Reconnect Count** | Σ(ws_reconnects_total) | Venue connection stability |

### Epoch Support

All metrics are computed within a named epoch:
- `crypto_v1_paper` — paper trading phase
- `crypto_v1_demo` — Binance testnet / Bybit demo
- `crypto_v1_live` — minimal live trading
- `crypto_v1_shadow_short` — shadow short-selling experiment (no execution)

Metrics can be compared across epochs to validate phase transitions.

---

## 2. Database Aggregation Plan

### Trade-Level Storage (`cte.trade_log`)

Every closed position becomes a row in `trade_log` (TimescaleDB hypertable):

```sql
CREATE TABLE cte.trade_log (
    time, trade_id, epoch, symbol, venue, tier,
    pnl, exit_reason, exit_layer, hold_seconds, r_multiple,
    entry_latency_ms, slippage_bps, mfe_pct, mae_pct,
    was_profitable, position_mode, position_id, signal_id
);
```

Indexes: epoch+time, symbol+time, exit_reason+time.

### Daily Aggregation (`cte.epoch_daily_summary`)

Materialized daily from `trade_log`, grouped by (date, epoch, symbol, venue, tier):

```sql
CREATE TABLE cte.epoch_daily_summary (
    date, epoch, symbol, venue, tier,
    trade_count, win_count, loss_count,
    gross_profit, gross_loss, net_pnl, avg_win, avg_loss,
    win_rate, expectancy, profit_factor, max_drawdown_pct, sharpe_ratio,
    saved_losers, killed_winners, no_progress_count, runner_count,
    avg_hold_seconds, avg_r_multiple, avg_slippage_bps, avg_latency_ms
);
```

35 columns covering every required metric dimension.

### Aggregation Strategy

```
trade_log (per-trade, hypertable)
    ↓ daily cron or continuous aggregate
epoch_daily_summary (daily, grouped)
    ↓ query
API / Grafana
```

For real-time dashboards, Prometheus metrics are used (scraped every 15s).
For historical analysis, `epoch_daily_summary` provides efficient querying.

---

## 3. API Endpoints

### Summary & Overview

| Method | Path | Parameters | Returns |
|---|---|---|---|
| GET | `/api/analytics/summary` | epoch?, symbol?, tier? | Full metrics dict |
| GET | `/api/analytics/pnl/daily` | epoch? | Daily aggregated PnL |
| GET | `/api/analytics/epochs` | — | List all epochs with status |
| GET | `/api/analytics/compare` | epoch_a, epoch_b | Side-by-side metrics + slippage drift |

### Drilldown

| Method | Path | Parameters | Returns |
|---|---|---|---|
| GET | `/api/analytics/trades` | epoch?, symbol?, tier?, exit_reason?, limit | Individual trade records |
| GET | `/api/analytics/breakdown/exit_reason` | epoch? | PnL + count by exit reason |
| GET | `/api/analytics/breakdown/tier` | epoch? | PnL by tier |
| GET | `/api/analytics/breakdown/symbol` | epoch? | PnL by symbol |

### Exit Analysis

| Method | Path | Parameters | Returns |
|---|---|---|---|
| GET | `/api/analytics/exit_analysis/saved_losers` | epoch? | Saved loser count |
| GET | `/api/analytics/exit_analysis/killed_winners` | epoch? | Killed winner count |
| GET | `/api/analytics/exit_analysis/no_progress_regret` | epoch? | Regret analysis (count, had_mfe, regret_rate) |
| GET | `/api/analytics/exit_analysis/runner_outcomes` | epoch? | Runner mode results (count, avg_r, avg_pnl) |

---

## 4. Grafana Dashboard Sections

### Dashboard 1: System Overview

| Panel | Metric Source | Type |
|---|---|---|
| Service Health Grid | `/health` per service | Stat grid |
| WebSocket Connection State | `cte_ws_connection_state` | Stat (colored) |
| Redis Stream Consumer Lag | `cte_redis_consumer_lag` | Time series |
| Feature Engine Staleness | `cte_sf_window_fill_pct` | Gauge |
| Reconnect Events | `cte_ws_reconnects_total` | Counter graph |

### Dashboard 2: Trading Performance

| Panel | Metric Source | Type |
|---|---|---|
| Cumulative PnL | `cte_analytics_pnl_total` by epoch | Time series |
| Daily PnL | `cte_analytics_daily_pnl` | Bar chart |
| Win Rate | `cte_analytics_win_rate` | Gauge |
| Expectancy | `cte_analytics_expectancy` | Stat |
| Profit Factor | `cte_analytics_profit_factor` | Stat |
| Max Drawdown | `cte_analytics_max_drawdown_pct` | Gauge (red >3%) |
| Trade PnL Distribution | `cte_analytics_trade_pnl` | Histogram |

### Dashboard 3: Exit Analysis

| Panel | Metric Source | Type |
|---|---|---|
| Exits by Layer | `cte_exit_decisions_total` by layer | Pie chart |
| Exits by Reason | `cte_exit_decisions_total` by reason | Bar chart |
| Saved Losers | `cte_saved_losers_total` | Counter |
| Killed Winners | `cte_potential_killed_winners_total` | Counter |
| Position Mode | `cte_position_mode` | State timeline |
| No-Progress Regret | API `/exit_analysis/no_progress_regret` | Stat |
| Runner Outcomes | API `/exit_analysis/runner_outcomes` | Table |

### Dashboard 4: Execution Quality

| Panel | Metric Source | Type |
|---|---|---|
| Signal-to-Fill Latency | `cte_paper_fill_slippage_bps` / `cte_binance_request_latency_seconds` | Histogram |
| Slippage Distribution | `cte_paper_fill_slippage_bps` | Histogram |
| Paper vs Demo/Live Slippage | API `/compare` | Time series overlay |
| Rate Limit Remaining | `cte_binance_requests_total` / `cte_bybit_requests_total` | Gauge |
| Order Reject Rate | Requests with status=error | Counter |

### Dashboard 5: Epoch Comparison

| Panel | Metric Source | Type |
|---|---|---|
| Paper vs Demo PnL | API `/compare` | Dual axis |
| Slippage Drift | API `/compare` → slippage_drift | Stat + trend |
| Win Rate by Epoch | `cte_analytics_win_rate` by epoch | Bar |
| Tier Performance by Epoch | API `/breakdown/tier` per epoch | Grouped bar |

---

## 5. Alert Rules

### Prometheus Alert Rules

| Alert | Expression | For | Severity | Action |
|---|---|---|---|---|
| **CTEStaleData** | `cte_sf_window_fill_pct{window="60s"} < 0.5` | 2m | WARNING | Investigate feed |
| **CTEStaleDataCritical** | `cte_sf_window_fill_pct{window="60s"} < 0.3` | 5m | CRITICAL | Auto-close positions |
| **CTEReconnectLoop** | `increase(cte_ws_reconnects_total[5m]) > 5` | — | WARNING | Check network |
| **CTEDailyDrawdownWarning** | `cte_analytics_max_drawdown_pct > 0.02` | — | WARNING | Review |
| **CTEDailyDrawdownHalt** | `cte_analytics_max_drawdown_pct > 0.03` | — | CRITICAL | Halt new positions |
| **CTEDailyDrawdownEmergency** | `cte_analytics_max_drawdown_pct > 0.05` | — | CRITICAL | Close all positions |
| **CTEOrderRejectSpike** | `increase(cte_binance_requests_total{status="error"}[1h]) > 5` | — | WARNING | Check venue status |
| **CTEReconciliationFailure** | `cte_recon_discrepancies_total > 0` | — | CRITICAL | Investigate immediately |
| **CTESlippageDrift** | (computed via API) | — | WARNING | Recalibrate fill model |

### Escalation Path

```
WARNING → Slack notification (#cte-alerts channel)
CRITICAL → Slack + PagerDuty + Auto-action (halt/close)
```

---

## 6. Daily Report Schema

### Report Structure

```json
{
  "report_date": "2024-03-15",
  "epoch": "crypto_v1_paper",
  "generated_at": "2024-03-16T00:05:00Z",

  "summary": {
    "trade_count": 23,
    "win_rate": 0.5652,
    "expectancy": 12.45,
    "profit_factor": 1.82,
    "net_pnl": 286.35,
    "max_drawdown_pct": 0.0187,
    "sharpe_ratio": 1.43
  },

  "by_symbol": {
    "BTCUSDT": {"trades": 15, "pnl": 210.50, "win_rate": 0.60},
    "ETHUSDT": {"trades": 8, "pnl": 75.85, "win_rate": 0.50}
  },

  "by_tier": {
    "A": {"trades": 8, "pnl": 180.00, "win_rate": 0.75, "avg_r": 1.45},
    "B": {"trades": 10, "pnl": 95.35, "win_rate": 0.50, "avg_r": 0.82},
    "C": {"trades": 5, "pnl": 11.00, "win_rate": 0.40, "avg_r": 0.35}
  },

  "by_exit_reason": {
    "winner_trailing": {"count": 8, "pnl": 420.00},
    "runner_trailing": {"count": 3, "pnl": 310.00},
    "hard_stop": {"count": 4, "pnl": -280.00},
    "thesis_failure": {"count": 3, "pnl": -85.00},
    "no_progress": {"count": 5, "pnl": -78.65}
  },

  "exit_analysis": {
    "saved_losers": 3,
    "killed_winners": 2,
    "no_progress_regret": {
      "count": 5,
      "had_positive_mfe": 3,
      "regret_rate": 0.60
    },
    "runner_outcomes": {
      "count": 3,
      "avg_r": 2.8,
      "avg_pnl": 103.33,
      "win_rate": 1.0
    }
  },

  "execution_quality": {
    "avg_latency_ms": 105,
    "avg_slippage_bps": 4.8,
    "avg_hold_seconds": 1820,
    "feed_stale_count": 1,
    "reconnect_count": 2
  },

  "alerts_fired": [
    {"rule": "daily_drawdown_warning", "value": 0.022, "time": "14:35:00Z"}
  ]
}
```

### Report Generation

Daily at 00:05 UTC:
1. Query `cte.trade_log` for previous day's trades
2. Compute all metrics via `compute_all_metrics()`
3. Query Prometheus for operational metrics (reconnects, stale counts)
4. Serialize to JSON
5. Store in `cte.daily_reports` table
6. POST to Slack webhook
