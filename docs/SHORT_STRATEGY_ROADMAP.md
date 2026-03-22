# Short strategy support — post–v1 roadmap

**Status:** Planning only. **Not implemented.**  
**Scope:** Real **strategy-level** short entries (`OPEN_SHORT`) with the same rigor as longs.

`AGENTS.md` currently states **“Do NOT implement short selling in v1.”** This document is the checklist for when that policy is lifted or moved to a **v2** milestone. Do not treat any section below as shipped behavior.

---

## 1. Signal design (product + quant)

- **Decision required:** How should a short entry be derived from the same feature surface?
  - Examples (mutually exclusive — pick one and document):
    - Symmetric rule: composite **below** a floor with bearish primary contribution thresholds.
    - Separate bearish primary score / regime flag gated by the same hard gates.
    - Dedicated short-only symbol list (unlikely — conflicts with universe rules).
- **Events:** Emit `ScoredSignalEvent` with `SignalAction.OPEN_SHORT` and `direction="short"` consistently through `SignalEvaluationResult`.
- **Cooldown / hourly limits:** Per-symbol caps should apply to shorts as to longs (or explicitly asymmetric — document).

**Primary code touchpoint:** [`src/cte/signals/engine.py`](../src/cte/signals/engine.py) (today hardcodes `OPEN_LONG` after tiering).

---

## 2. Gate parity

- **Hard gates** (stale feed, max spread, max divergence, execution feasibility, warmup) must run **before** short scoring with identical thresholds unless a written exception exists.
- **Feature vectors:** Confirm streaming features behave for short intent (e.g. momentum sign, orderflow interpretation). Add tests where sign matters.
- **Optional future gates:** Funding pressure, borrow availability — only if product requires (often N/A for USDT-M perps).

---

## 3. Risk and sizing parity

- **PortfolioState / exposure:** [`src/cte/risk/manager.py`](../src/cte/risk/manager.py) — ensure short notional counts toward caps consistently with longs (same symbol opposite direction, net exposure rules).
- **Correlation / drawdown:** Vetoes that assume long bias must be reviewed.
- **Dashboard testnet runner:** [`src/cte/dashboard/testnet_runner.py`](../src/cte/dashboard/testnet_runner.py) — entry path uses `scored.action` / `direction`; verify short branch after signal exists.
- **Sizing:** [`src/cte/sizing/engine.py`](../src/cte/sizing/engine.py) — `open_short` sizing and min notional for both venues.

---

## 4. Exit parity

- **Paper / mirror:** [`src/cte/execution/paper.py`](../src/cte/execution/paper.py) — SL/TP/trailing semantics for shorts; MFE/MAE sign conventions.
- **Exit engine:** Five-layer model — thesis failure and no-progress for shorts; confirm no hidden long bias in layer ordering.
- **Venue close:** Adapters already branch on `direction` for `close_position`; re-verify Binance + Bybit hedge vs one-way modes.

---

## 5. Analytics and reporting

- **Journal:** `CompletedTrade.direction`, `source`, `venue`, `execution_channel` already support `short` rows when recorded.
- **Metrics:** [`src/cte/analytics/metrics.py`](../src/cte/analytics/metrics.py) — `direction_splits` (long vs short counts, expectancy). Validate with mixed-direction samples.
- **Readiness / campaigns:** [`src/cte/ops/readiness.py`](../src/cte/ops/readiness.py) — gates such as “≥25 trades per direction” become meaningful; until shorts exist, treat as **N/A** or **FAIL** with explicit reason in reports.
- **Dashboard:** Filters and copy must not imply “long-only” if shorts are live.

---

## 6. Tests required (non-exhaustive)

| Area | Examples |
|------|----------|
| Signals | `tests/signals/test_signal_engine.py` — short emission, tier mapping, rejection parity |
| Gates | `tests/signals/test_gates.py` — same vectors, short action |
| Risk | `tests/risk/` — exposure with short positions |
| Execution | `tests/execution/` — paper + adapter short open/close |
| Integration | Full pipeline short: signal → risk → size → fill → exit → `record_trade` |
| Dashboard | Runner tick with mocked short signal (if unit tests added) |

---

## 7. Proof requirements (before claiming “short strategy verified”)

Minimum **per venue** (same bar as long proofs):

1. **Paper:** At least one closed trade with `direction=short`, `source=paper_simulated`, correct epoch.
2. **Binance testnet:** At least one closed `demo_exchange` row with `direction=short`, `execution_channel=binance_usdm_testnet`, full lifecycle via dashboard loop (not manual curl).
3. **Bybit demo:** At least one closed `demo_exchange` row with `direction=short`, `venue=bybit_demo`, `execution_channel=bybit_linear_demo`.

REST smoke alone does **not** satisfy strategy proof.

---

## 8. Documentation updates (when implementing)

- [DIRECTIONAL_VENUE_PROOF_MATRIX.md](DIRECTIONAL_VENUE_PROOF_MATRIX.md) — refresh matrices.
- [README.md](../README.md) — v1 / v2 scope statements.
- [AGENTS.md](../AGENTS.md) — remove or narrow the “no short in v1” rule if scope changes.
