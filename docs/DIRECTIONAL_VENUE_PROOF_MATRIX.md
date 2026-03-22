# Directional & venue proof matrix

## v1 position (read this first)

**v1 trading strategy is long-only:** `ScoringSignalEngine` emits `SignalAction.OPEN_LONG` only. There is no short-side signal path in production code.

**Multi-venue execution infrastructure** (paper engine, Binance testnet adapter, Bybit demo adapter) exists and can route orders when the dashboard loop receives a signal.

**REST-level short orders** (e.g. `scripts/smoke_bybit_demo.py` with `CTE_SMOKE_DIRECTION=short`) prove **venue/auth/position mechanics only**. They are **not** “short strategy support” and do **not** populate analytics as a strategy outcome unless extended deliberately.

Do **not** describe v1 as “bi-directional strategy” or “short strategy verified” in README or operator docs. See [SHORT_STRATEGY_ROADMAP.md](SHORT_STRATEGY_ROADMAP.md) for a post–v1 implementation checklist.

---

## Taxonomy: four separate layers

Use this table to avoid mixing **strategy**, **venue capability**, **runtime proof**, and **analytics proof**.

| Layer | What it answers | v1 truth |
|-------|-----------------|----------|
| **Strategy support** | What does `ScoringSignalEngine` emit after gates + tiers? | **Long-only** (`OPEN_LONG`). No `OPEN_SHORT`. |
| **Venue capability** | What can adapters / REST do if given an `OrderRequest`? | Long and short order shapes (per venue rules); smoke can exercise shorts without strategy. |
| **Runtime proof (dashboard)** | Feed + loop + venue orders + positions API for **strategy-driven** entries? | Provable for **long** lifecycle per venue when env/keys/loop are correct. |
| **Analytics proof** | Closed trades in journal with `source` / `epoch` / `direction`? | Reflects **strategy** direction; v1 journal entries from the loop are **long** unless/until signal engine changes. |

The matrices below are keyed to **runtime + analytics** for the **dashboard path**, plus a separate **REST smoke** row for venue-only checks.

---

## Definitions

| Term | Meaning |
|------|--------|
| **Strategy path** | `ScoringSignalEngine` → tiered signal → dashboard `PaperRunner` / `DashboardTestnetRunner` |
| **Full lifecycle** | Entry → open position visible in API → venue close → `analytics.record_trade` with correct `source` / `epoch` / `execution_channel` |
| **REST smoke** | `scripts/smoke_bybit_demo.py` (venue-only; does **not** write dashboard analytics) |

## Strategy reality (direction)

The production scoring path **always emits long entries** when a signal clears gates and tiers (see `action = SignalAction.OPEN_LONG` in `src/cte/signals/engine.py`).

There is **no** `OPEN_SHORT` branch in `evaluate_with_reason`. So:

- **LONG** — can be produced end-to-end (paper, Binance testnet, Bybit demo) when execution mode and venue keys are configured.
- **SHORT** — **not** produced by the current strategy/dashboard loop. Downstream code (adapters, paper engine, exits) can handle `direction="short"` if a signal existed, but **no such signal is emitted**.

This is a **strategy constraint**, not a Binance/Bybit API limitation.

## Matrix (dashboard / analytics journal)

| Mode | Venue | LONG full lifecycle | SHORT full lifecycle | Notes |
|------|--------|---------------------|----------------------|--------|
| **Paper** | Simulated (`paper_simulated`) | **Yes** (when signals fire) | **No** (no `OPEN_SHORT` signal) | Journal uses in-process paper engine; direction follows `ScoredSignalEvent.action` (always long in v1). |
| **Demo / testnet** | **Binance USDⓈ-M testnet** | **Yes** (with `CTE_DASHBOARD_EXECUTION_VENUE=binance_testnet`, keys, loop on) | **No** (same strategy) | `source=demo_exchange`, `execution_channel=binance_usdm_testnet`. |
| **Demo** | **Bybit linear demo** | **Yes** (with `bybit_demo` + `CTE_BYBIT_*`, proof symbol optional) | **No** (same strategy) | `source=demo_exchange`, `venue=bybit_demo`, `execution_channel=bybit_linear_demo` when recorded. |

## Matrix (REST / adapter smoke — not analytics)

| Venue | LONG REST smoke | SHORT REST smoke |
|-------|-----------------|------------------|
| **Bybit demo** | `scripts/smoke_bybit_demo.py` (default) | Same script with `CTE_SMOKE_DIRECTION=short` (optional; proves venue + one-way/hedge `positionIdx` behavior) |

REST smoke does **not** prove dashboard analytics; it proves **auth, order accept, position, close** only.

## Why SHORT is blocked for “live” dashboard proof

1. **Strategy constraint** — `SignalAction.OPEN_LONG` only; no bearish entry action is computed from the composite score.
2. **Signal path** — Even with `Direction.BI_DIRECTIONAL` in settings, the scoring engine does not map composite output to short entries.
3. **Venue / account mode** — **Not** the blocker for the dashboard: Bybit adapter supports short (`Sell` + `direction=short`; `CTE_BYBIT_LINEAR_POSITION_MODE` controls `positionIdx` for hedge vs one-way). Binance testnet adapter likewise supports shorts at the REST layer.
4. **Config** — Engine direction (`bi_directional`) does not override the hardcoded `OPEN_LONG` emission.

## Temporary proof-window tuning (validation only)

For end-to-end **LONG** demo proofs under wide testnet spreads, operators may use **dashboard-only** env vars (e.g. in `deploy/docker-compose.yml` under `analytics.environment`):

- `CTE_SIGNALS_GATE_MAX_SPREAD_BPS`
- `CTE_DASHBOARD_PAPER_WARMUP_MIDS_*`
- `CTE_DASHBOARD_PAPER_TIER_C_THRESHOLD`

These are **not** production-grade defaults; they widen the gate so tier-C signals can appear. **Remove or tighten after the validation campaign** — do not treat them as final operator defaults.

See also: [DEMO_VALIDATION_CAMPAIGN_RUNBOOK.md](DEMO_VALIDATION_CAMPAIGN_RUNBOOK.md) § “Temporary proof-window tuning”.

## Recommended next step (directional)

1. **v1 documentation** — Keep strategy/venue/REST/analytics layers distinct (this file + README).
2. **Post–v1 short strategy** — Follow [SHORT_STRATEGY_ROADMAP.md](SHORT_STRATEGY_ROADMAP.md) only after product sign-off and a rule change in `AGENTS.md` / version scope.
