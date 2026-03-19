# Smart Exit Engine — Design Document

## Table of Contents

1. [Exit Rules by Layer](#1-exit-rules-by-layer)
2. [Priority Order](#2-priority-order)
3. [Configuration Example](#3-configuration-example)
4. [Runner Activation Logic](#4-runner-activation-logic)
5. [No-Progress Logic](#5-no-progress-logic)
6. [Explainability Payload](#6-explainability-payload)
7. [Test Scenarios](#7-test-scenarios)

---

## 1. Exit Rules by Layer

### Layer 1: Hard Risk Stop (Priority 1 — highest)

Unconditional safety rails. Non-negotiable. Same for all tiers.

| Check | Condition | Rationale |
|---|---|---|
| Hard stop | `loss_pct ≥ 2.5%` | Absolute max loss — never risked more |
| Stale data | `freshness.composite < 0.3` | Position managed on stale features is gambling |
| Spread blowout | `spread_bps > 20` | Liquidity has evaporated — exit cost will be huge |
| Emergency | Risk manager kill signal | External circuit breaker |

**Key property**: Layer 1 fires instantly on a single check. No confirmation window,
no patience, no tier differentiation. If the building is on fire, you leave.

### Layer 2: Thesis Failure (Priority 2)

The features that justified the entry have inverted.

| Check | Condition | Meaning |
|---|---|---|
| Orderflow flip | `TFI < -0.1` (60s) | Buyers retreated, sellers dominate |
| Momentum collapse | `returns_z < -1.0` (60s) | Price momentum has reversed |
| Liquidation shift | `liq_imbalance > 0.3` (for longs) | Longs are being liquidated en masse |

**Confirmation window**: The thesis must be negative for N consecutive evaluations
before triggering exit. This prevents whipsaw (a single bad tick shouldn't kill a
good position).

| Tier | Confirmations Required | Rationale |
|---|---|---|
| A | 3 | High-conviction entry deserves benefit of doubt |
| B | 2 | Moderate patience |
| C | 1 | Marginal entry — kill fast if thesis fails |

**Reset**: If features recover (TFI goes positive again), the confirmation counter
resets to zero. The thesis gets a fresh start.

### Layer 3: No Progress (Priority 3)

The trade was expected to move in our favor. It didn't within the time budget.

| Tier | Time Budget | Min Progress | Rationale |
|---|---|---|---|
| A | 15 min | 0.3% gain | Strong signal → patient, but not indefinitely |
| B | 8 min | 0.3% gain | Moderate signal → prove yourself in reasonable time |
| C | 4 min | 0.3% gain | Weak signal → tight leash |

**Suspension**: No-progress timer is suspended when the position enters **runner mode**.
A runner that consolidates at +3% shouldn't be killed for "no progress."

### Layer 4: Winner Protection (Priority 4)

Trailing stop for positions that have proved themselves profitable.

**Activation criteria** (either triggers activation):
- R-multiple ≥ 1.0 (earned at least what was risked)
- Gain ≥ 1.0% (fallback if stop distance is zero)

Once activated, a trailing stop from the position's highest price is enforced:

| Tier | Trailing Stop | Rationale |
|---|---|---|
| A | 2.0% from high | Wide — let strong entries breathe |
| B | 1.5% from high | Moderate |
| C | 1.0% from high | Tight — protect marginal wins |

**Deferral**: If runner mode (Layer 5) is active, Layer 4 defers entirely.
Runner mode has its own wider trailing stop.

### Layer 5: Runner Mode (Priority 5 — lowest)

Exceptional winners get maximum room to run.

**Activation criteria** (either triggers):

| Tier | R Threshold | Gain Threshold | Rationale |
|---|---|---|---|
| A | 2.0R | 2.5% | Easier to qualify — trust strong entries |
| B | 2.5R | 3.0% | Moderate bar |
| C | 3.0R | 3.5% | High bar — marginal signals must prove themselves hard |

**Runner trailing stop**:

| Tier | Trailing | vs Winner Trailing |
|---|---|---|
| A | 3.5% from high | 1.75× wider than winner |
| B | 3.0% from high | 2.0× wider |
| C | 2.5% from high | 2.5× wider |

**Runner downgrade**: If `returns_z < -1.5` (momentum has completely died),
the position is downgraded from runner back to winner protection. The runner's
wider trailing is replaced by the tighter winner trailing. This prevents
"zombie runners" — positions that were once great but are now drifting.

---

## 2. Priority Order

```
L1 Hard Risk      ─── fires on danger, no exceptions
│
L2 Thesis Failure  ─── features inverted, thesis dead
│
L5 Runner Mode     ─── wide trailing for exceptional winners
│                      (checked before L4 because runners override winners)
L4 Winner Prot     ─── trailing for proved winners
│
L3 No Progress     ─── time budget exhausted
```

**Why L5 is checked before L4**: If a position qualifies as a runner, its trailing
stop should be the runner's wider one (3.5%), not the winner's tighter one (2.0%).
Checking L5 first lets it claim the position. If L5 doesn't qualify (not enough profit),
L4 gets its chance.

**Why L3 is last**: No-progress is the weakest exit reason. It should only fire when
nothing else has. And it's suspended for runners anyway.

**Override rules**: Higher-priority layers always win. A runner (L5) that suddenly has
stale data will be killed by L1 (hard risk), not protected by L5.

---

## 3. Configuration Example

```toml
# In config/defaults.toml

[exits.tier_a]
hard_stop_pct = 0.025
max_spread_bps = 20.0
min_freshness = 0.3
thesis_confirm_count = 3
thesis_tfi_flip_threshold = -0.1
thesis_momentum_collapse_z = -1.0
thesis_liq_shift_threshold = 0.3
no_progress_timeout_minutes = 15.0
no_progress_min_gain_pct = 0.003
winner_activation_r = 1.0
winner_activation_pct = 0.01
winner_trailing_pct = 0.020
runner_activation_r = 2.0
runner_activation_pct = 0.025
runner_trailing_pct = 0.035
runner_suspend_no_progress = true

[exits.tier_b]
# Same keys, different values
thesis_confirm_count = 2
no_progress_timeout_minutes = 8.0
winner_trailing_pct = 0.015
runner_activation_r = 2.5
runner_trailing_pct = 0.030

[exits.tier_c]
thesis_confirm_count = 1
no_progress_timeout_minutes = 4.0
winner_trailing_pct = 0.010
runner_activation_r = 3.0
runner_trailing_pct = 0.025
```

---

## 4. Runner Activation Logic

```
Position opened at $50,000 (Tier A, stop = 2.5%)
Stop distance = $50,000 × 0.025 × qty = $1,250 per unit

Phase 1: NORMAL mode
  Price: 50000 → 50200 → 50400
  Gain: 0.4% → 0.8%
  No special treatment, normal Layer 3 timer running

Phase 2: WINNER PROTECTION activates
  Price hits 50500 (+1.0% gain)
  Mode: normal → winner_protection
  Layer 4 trailing (2.0% from high) now applies
  Layer 3 timer continues

Phase 3: RUNNER MODE activates
  Price hits 51250 (+2.5% gain = 1.0R)
  Wait — runner needs 2.0R for Tier A
  Price hits 52500 (+5.0% gain = 2.0R)
  Mode: winner_protection → runner
  Layer 5 trailing (3.5% from high) replaces Layer 4
  Layer 3 timer SUSPENDED

Phase 4: Runner consolidation
  Price: 52500 → 52000 → 51800
  Drawdown from high (52500): 1.3% — within 3.5% trailing
  No exit. Runner is allowed to consolidate.

Phase 5: Runner continuation
  Price: 51800 → 53000 → 54000 → 55000
  New high = 55000. Runner trailing = 3.5% from 55000 = 53075
  Position continues running.

Phase 6: Runner trailing triggers
  Price: 55000 → 54000 → 53050
  Drawdown: 3.55% from high (55000) ≥ 3.5% trailing
  EXIT: runner_trailing
  Realized PnL: 53050 - 50000 = $3,050 per unit = 2.44R

Without runner mode:
  Winner trailing (2.0%) would have exited at 52500 × 0.98 = 51450
  PnL would have been $1,450 = 1.16R
  Runner mode captured additional $1,600 = 1.28R extra per unit
```

### Runner downgrade

```
  Price: 52500 (runner active)
  Features: returns_z drops to -2.0 (momentum collapsed)
  Mode: runner → winner_protection (DOWNGRADED)
  Now Layer 4's tighter trailing (2.0% from high) applies

  If momentum recovers (returns_z > -1.5), the position can
  re-qualify for runner mode on the next evaluation.
```

---

## 5. No-Progress Logic

```
Tier A position opened at 12:00:00

12:00:00  Entry at $50,000. Budget = 15 min. Min progress = 0.3%
12:05:00  Price: $50,050 (+0.1%). Budget remaining: 10 min. Patience.
12:10:00  Price: $50,080 (+0.16%). Budget remaining: 5 min. Still patient.
12:15:00  Price: $50,100 (+0.2%). Budget exhausted.
          0.2% < 0.3% min progress. EXIT: no_progress

Tier C position opened at 12:00:00

12:00:00  Entry at $50,000. Budget = 4 min. Min progress = 0.3%
12:02:00  Price: $50,020 (+0.04%). Budget remaining: 2 min.
12:04:00  Price: $50,030 (+0.06%).
          0.06% < 0.3%. EXIT: no_progress
```

### No-progress suspension for runners

```
Runner position (Tier A):

12:00:00  Entry at $50,000
12:05:00  Price: $51,500 (+3.0%). Runner mode activated.
12:20:00  Price: $51,300 (+2.6%). Budget (15 min) would be exhausted...
          But runner_suspend_no_progress = true.
          No-progress check returns: "Suspended — position in runner mode"
          Position continues.
```

---

## 6. Explainability Payload

Every exit evaluation produces an `ExitDecision`:

```json
{
  "should_exit": true,
  "exit_reason": "thesis_failure",
  "exit_layer": 2,
  "exit_layer_name": "thesis_failure",
  "exit_detail": "Thesis invalid for 3 consecutive checks: TFI=-0.25 < -0.1; returns_z=-1.8 < -1.0",
  "position_mode": "winner_protection",
  "was_profitable_at_exit": true,
  "exit_gain_pct": 0.0082,
  "hold_seconds": 420,
  "current_r": 0.33,
  "all_layers": [
    {"layer": 1, "layer_name": "hard_risk", "triggered": false, "detail": "All hard risk checks passed"},
    {"layer": 2, "layer_name": "thesis_failure", "triggered": true, "detail": "Thesis invalid for 3 consecutive checks: TFI=-0.25 < -0.1; returns_z=-1.8 < -1.0"},
    {"layer": 5, "layer_name": "runner", "triggered": false, "detail": "Not a runner: gain=0.820%, R=0.33"},
    {"layer": 4, "layer_name": "winner_protection", "triggered": false, "detail": "Deferred to runner mode (Layer 5)"},
    {"layer": 3, "layer_name": "no_progress", "triggered": false, "detail": "Within budget: 7.0/15 min"}
  ]
}
```

### saved_losers vs killed_winners

After every exit, the engine classifies:

| Metric | Condition | Meaning |
|---|---|---|
| `saved_loser` | Exit by L1 or L2 AND `was_profitable_at_exit == false` | The exit prevented a deeper loss |
| `potential_killed_winner` | Exit by L2 or L3 AND `was_profitable_at_exit == true` | We exited a profitable position on non-price grounds |

`potential_killed_winner` is a hypothesis, not a certainty. To confirm whether the trade
*would* have been a winner requires tracking what the price did after exit. This is an
analytics concern:

```sql
-- Find potential killed winners and check post-exit price action
SELECT p.position_id, p.exit_reason, p.exit_price, p.realized_pnl,
       MAX(t.price) as post_exit_high,
       (MAX(t.price) - p.exit_price) / p.exit_price as missed_gain_pct
FROM cte.positions p
JOIN cte.trades t ON t.symbol = p.symbol
  AND t.time BETWEEN p.closed_at AND p.closed_at + interval '1 hour'
WHERE p.exit_reason IN ('thesis_failure', 'no_progress')
  AND p.realized_pnl > 0
GROUP BY p.position_id, p.exit_reason, p.exit_price, p.realized_pnl;
```

If `missed_gain_pct > 2%` consistently, the thesis failure or no-progress checks
are too aggressive and need loosening.

---

## 7. Test Scenarios

### 41 tests across 2 files

#### Layer Tests (test_layers.py) — 24 tests

| Layer | Test | What It Validates |
|---|---|---|
| L1 | hard_stop_triggers | Loss ≥ 2.5% → immediate exit |
| L1 | within_stop_passes | Loss < 2.5% → no exit |
| L1 | stale_data_triggers | Freshness < 0.3 → immediate exit |
| L1 | spread_blowout_triggers | Spread > 20 bps → immediate exit |
| L1 | no_features_only_checks_price | No features → only price stop checked |
| L2 | tfi_flip_with_confirmation | TFI flip needs 3 confirms for Tier A |
| L2 | tier_c_single_confirm | TFI flip needs only 1 confirm for Tier C |
| L2 | momentum_collapse | returns_z < -1.0 triggers thesis failure |
| L2 | liq_shift_for_long | Liq imbalance shift triggers for longs |
| L2 | reset_on_recovery | Counter resets when features recover |
| L2 | no_features_passes | No features → no thesis check possible |
| L3 | triggers_after_budget | No progress after timeout → exit |
| L3 | within_budget_passes | Within time budget → no exit |
| L3 | sufficient_progress_passes | Enough gain → no exit even past budget |
| L3 | tier_c_shorter_budget | Tier C has 4 min budget (vs A's 15) |
| L3 | suspended_in_runner_mode | Runner mode suspends no-progress |
| L4 | activates_on_profit | ≥1% gain → winner protection mode |
| L4 | trailing_from_high_triggers | Drawdown ≥ trailing % → exit |
| L4 | not_triggered_when_not_winner | Below threshold → no activation |
| L4 | defers_to_runner | Runner mode → L4 defers |
| L5 | activates_on_big_profit | ≥2.5% gain → runner mode |
| L5 | runner_trailing_triggers | Wide trailing hit → exit |
| L5 | momentum_collapse_downgrades | Dead momentum → downgrade to winner |
| L5 | not_runner_yet | Insufficient gain → stays normal |

#### Engine Integration Tests (test_exit_engine.py) — 17 tests

| Category | Test | What It Validates |
|---|---|---|
| Priority | hard_risk_overrides_everything | L1 wins over L2+L3+L5 |
| Priority | thesis_failure_before_no_progress | L2 wins over L3 |
| Priority | no_exit_when_all_clear | All layers pass → no exit |
| Explain | decision_has_all_layers | All evaluated layers in payload |
| Explain | exit_has_reason_detail | Human-readable detail populated |
| Explain | position_mode_tracked | Mode (normal/winner/runner) tracked |
| Mode | normal_to_winner | Mode progression on profit |
| Mode | winner_to_runner | Mode progression on big profit |
| Mode | runner_downgrade | Momentum collapse → downgrade |
| Budget | tier_a_patient | 15 min budget honored |
| Budget | tier_c_impatient | 4 min budget honored |
| Budget | runner_suspends_no_progress | Runner not killed by timer |
| Analytics | profitable_exit_flagged | was_profitable_at_exit correct |
| Analytics | losing_exit_flagged | Losing exit correctly flagged |
| Analytics | hold_seconds_and_r | Hold time and R-multiple populated |
| Replay | same_sequence_same_decisions | Deterministic replay verified |
| Cleanup | cleanup_removes_state | State cleaned after position close |
