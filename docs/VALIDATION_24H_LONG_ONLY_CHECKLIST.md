# 24h validation run — v1 long-only strategy

This checklist supports a **continuous** dashboard run (≥24 hours) to validate **live market data → scoring → risk → sizing → execution (paper or demo/testnet) → exits → analytics** when the **strategy is long-only** (`OPEN_LONG` only).

It does **not** prove short strategy behavior. For venue-only short REST checks, use `scripts/smoke_bybit_demo.py` (see [DIRECTIONAL_VENUE_PROOF_MATRIX.md](DIRECTIONAL_VENUE_PROOF_MATRIX.md)). For future short **strategy** work, see [SHORT_STRATEGY_ROADMAP.md](SHORT_STRATEGY_ROADMAP.md).

---

## Preconditions

| Check | Notes |
|-------|--------|
| Stable host | VM, bare metal, or long-lived Docker host (not a laptop sleep cycle). |
| Repo + commit | Record git commit hash in the report template. |
| `.env` | `CTE_ENGINE_MODE`, `CTE_EXECUTION_MODE`, keys for chosen venue; **no secrets in git**. |
| v1 strategy scope | Expect **long-only** entries from `ScoringSignalEngine`. |
| Proof-window tuning | If using `deploy/docker-compose.yml` analytics overrides (`CTE_SIGNALS_GATE_MAX_SPREAD_BPS`, etc.), treat as **validation-only**; **remove after campaign** for production-like profiles. |
| Epoch | Demo dashboard uses `crypto_v1_demo` for testnet/demo exchange trades; paper uses `crypto_v1_paper` when `CTE_ENGINE_MODE=paper`. |

---

## Start

**Docker (example):**

```bash
docker compose -f deploy/docker-compose.yml up -d analytics
```

**Local:**

```bash
pip install -e .
set PYTHONPATH=src   # Windows PowerShell: $env:PYTHONPATH="src"
cte-dashboard
# or: python -m cte.dashboard
```

Verify: `curl -s http://127.0.0.1:8080/api/dashboard/meta` returns JSON.

---

## During the run (hourly)

Pick **one** per hour (or both if you want redundancy):

```bash
curl -sS -X POST "http://127.0.0.1:8080/api/campaign/snapshot?period=hourly"
```

Or collect a full file bundle:

```bash
BASE_URL=http://127.0.0.1:8080 ./scripts/collect_validation_snapshot.sh ./validation_snapshots
```

The script writes `run_<UTC-timestamp>/` with market, paper, analytics, campaign, ops, readiness JSON — plus **`config.json`** and **`analytics_trades_demo_exchange.json`** (filtered) when using the updated collector.

---

## End of run (≥24h)

| Export | Command |
|--------|---------|
| Campaign summary | `GET /api/campaign/summary` |
| Analytics (demo epoch) | `GET /api/analytics/summary?epoch=crypto_v1_demo` |
| Demo exchange journal | `GET /api/analytics/trades?source=demo_exchange&epoch=crypto_v1_demo&limit=200` |
| Readiness | `GET /api/readiness/campaign` |

Fill [templates/VALIDATION_24H_REPORT_TEMPLATE.md](templates/VALIDATION_24H_REPORT_TEMPLATE.md) and attach snapshot directory paths.

---

## Abort criteria

Stop the run and investigate if any of the following persist (see [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md) for procedures):

- Feed disconnected or chronic staleness (`/api/market/health`).
- Sustained `ops` halt / emergency stop required.
- Unbounded venue errors on every tick (check `/api/paper/status` → `last_error`, `venue_last_error`).
- Reconciliation mismatches trending up (demo exchange path).

---

## Related documents

| Doc | Role |
|-----|------|
| [DEMO_VALIDATION_CAMPAIGN_RUNBOOK.md](DEMO_VALIDATION_CAMPAIGN_RUNBOOK.md) | Campaign API, proof-window warning |
| [VALIDATION_CAMPAIGN_REPORT.md](VALIDATION_CAMPAIGN_REPORT.md) | Evidence report + Phase 9 template |
| [DIRECTIONAL_VENUE_PROOF_MATRIX.md](DIRECTIONAL_VENUE_PROOF_MATRIX.md) | What v1 proves |
