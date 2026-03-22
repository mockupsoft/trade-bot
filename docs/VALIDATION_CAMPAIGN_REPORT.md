# Validation campaign report (real data, demo mode)

This document records **evidence-based** validation of the CTE dashboard in **`CTE_ENGINE_MODE=demo`**. It does not use seed trades. Market data comes from the **Binance USDⓈ-M futures WebSocket** (`stream.binancefuture.com`).

## Scope and limits (read carefully)

| Topic | What this deployment validates |
|--------|--------------------------------|
| Market data | Live combined stream; tickers, spread, book age — **verified** |
| Signal → risk → sizing → **paper** execution | In-process `DashboardPaperRunner`; fills are **simulated** at bid/ask using live quotes (`source=paper_simulated`) — **verified** when trades close |
| Binance testnet **REST** orders | **Not** sent by the dashboard paper loop. Keys satisfy the **safety gate** and enable demo profile; venue order placement requires the separate execution service / `ExecutionMode.TESTNET` wiring — **out of scope for this UI process** |
| Reconciliation | `/api/reconciliation/status` reflects the in-app counter unless a reconciler job updates it — **not** exchange-vs-local proof in this stack |
| Duration | A **24h / 7d** campaign must run on a **persistent host** (VM, systemd, Docker). This report includes a **short pilot** plus metrics from a **longer prior session** on the same environment where applicable |

## Phase 1 — Environment

- **Mode:** `CTE_ENGINE_MODE=demo`
- **Safety:** Startup prints **NO REAL CAPITAL** / testnet banner; `enforce_safety` rejects production URLs and missing keys (see `src/cte/ops/safety.py`).
- **Symbols:** `CTE_ENGINE_SYMBOLS` must be a **JSON array** if set, e.g. `["BTCUSDT","ETHUSDT"]`. Comma-separated `BTCUSDT,ETHUSDT` **fails** parsing.

## Phase 2 — Live feed (pilot run, ~45 s fresh process)

Captured **2026-03-20** (UTC ~17:11–17:12).

| Metric | Value |
|--------|--------|
| `connected` | `true` |
| `messages_total` | ~1991 (growing) |
| `reconnect_count` | `0` |
| `errors_total` | `0` |
| `latency_ms` (feed health) | ~206 ms |
| `last_message_age_ms` | ~11 ms |
| BTC/ETH | `is_stale: false`, spread single-digit bps on typical ticks |

**Abort criteria:** Feed not connected or chronic staleness — **not observed** in pilot.

## Phase 3 — First trade / pipeline (pilot)

On a **fresh** process (~45 s), **no closed trades** yet (in-memory analytics empty). Paper status showed:

- `top_blocker`: `rejected_warmup` (expected until enough mids accumulate)
- `ticks_ok`: increasing; warmup thresholds default early **20** / full **36** mids

**Prior longer session** (same codebase, before restart): **36+ closed** `paper_simulated` trades, non-zero `total_pnl`, `trade_count` in `/api/analytics/summary` — demonstrates end-to-end **paper** pipeline with live data.

**Important:** That does **not** prove testnet REST execution; it proves **paper** journal + analytics.

## Phase 4 — Continuous run (24 h minimum)

**Not executed inside this agent session.** Use:

```bash
export CTE_ENGINE_MODE=demo
export CTE_BINANCE_TESTNET_API_KEY="…"
export CTE_BINANCE_TESTNET_API_SECRET="…"
python3 -m uvicorn cte.dashboard.app:app --host 0.0.0.0 --port 8080
```

Hourly cron:

```bash
curl -s -X POST "http://127.0.0.1:8080/api/campaign/snapshot?period=hourly"
```

Or run `./scripts/collect_validation_snapshot.sh /path/to/logs`.

## Phase 5 — Reconciliation

For **exchange** reconciliation, run the execution/reconciliation services that compare to Binance testnet. The dashboard stub is **not** proof of order parity.

## Phase 6 — Ops (pilot)

| Action | Result |
|--------|--------|
| `POST /api/ops/pause` | `mode: paused` |
| `POST /api/ops/resume` | `mode: active` |

Verified **2026-03-20** (UTC).

## Phase 7 — Summary (evidence from API snapshots)

### Pilot (fresh process, ~45 s)

- **Closed trades:** 0  
- **Net PnL:** 0  
- **Campaign snapshot:** hourly, all zeros  

### Reference session (longer run, prior to restart — illustrative)

From captured API: **36** closed trades, **net PnL** approximately **-14.81 USD**, **max_drawdown_pct** ~**0.0015**, **avg_slippage_bps** **5.0**, **source** `paper_simulated` only, **seed** 0.

These numbers are **not** a 24 h aggregate; they are **real** in-memory analytics for that process lifetime.

### Warmup analysis

- `warmup_phase_breakdown` requires **closed** trades with `warmup_phase` set; **pilot** had none.
- Longer runs with staged warmup will populate `early` / `full` / `promotion_evidence`.

### Readiness (GO / NO-GO)

| Gate | Assessment |
|------|------------|
| Live market + paper pipeline | **GO** for **paper/demo UI** validation |
| 7-day campaign + ≥100 promotion trades | **NO-GO** from pilot alone — insufficient duration and sample |
| Testnet order parity | **NO-GO** — not exercised by dashboard paper loop |

## Phase 8 — README

See repository **README** → section **Validation Campaign (Real Data)**.

---

## Phase 9 — 24h long-only (v1) template

Use this template for a **minimum 24-hour** continuous run when validating the **long-only** strategy (`OPEN_LONG`).

**Operator checklist:** [VALIDATION_24H_LONG_ONLY_CHECKLIST.md](VALIDATION_24H_LONG_ONLY_CHECKLIST.md)

**Blank report:** [templates/VALIDATION_24H_REPORT_TEMPLATE.md](templates/VALIDATION_24H_REPORT_TEMPLATE.md)

### What to capture

| Artifact | Endpoint or script |
|----------|-------------------|
| Hourly snapshots | `POST /api/campaign/snapshot?period=hourly` |
| File bundle | `BASE_URL=http://127.0.0.1:8080 ./scripts/collect_validation_snapshot.sh ./validation_snapshots` |
| End-of-run summary | `GET /api/campaign/summary` |
| Demo epoch metrics | `GET /api/analytics/summary?epoch=crypto_v1_demo` |
| Exchange-backed journal | `GET /api/analytics/trades?source=demo_exchange&epoch=crypto_v1_demo&limit=200` |

### Classification line for v1

- **Strategy:** long-only (no `OPEN_SHORT` in `ScoringSignalEngine`).
- **Proof-window env:** if used from `deploy/docker-compose.yml`, document as **validation-only** and schedule removal after the campaign.
