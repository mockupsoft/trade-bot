# Signal Engine — Design Document

**v1 strategy scope:** the production `ScoringSignalEngine` emits **long entries only** (`SignalAction.OPEN_LONG` after tiering). Short-side strategy signals are **not** implemented; see [SHORT_STRATEGY_ROADMAP.md](SHORT_STRATEGY_ROADMAP.md) for a post–v1 checklist. Do not confuse adapter REST short capability with strategy support.

## Table of Contents

1. [Composite Formula](#1-composite-formula)
2. [Gate Conditions](#2-gate-conditions)
3. [Tier Thresholds](#3-tier-thresholds)
4. [JSON Schema for Signal Outputs](#4-json-schema-for-signal-outputs)
5. [Example Signal Records](#5-example-signal-records)
6. [Determinism and Auditability](#6-determinism-and-auditability)
7. [Avoiding Overfitting in v1](#7-avoiding-overfitting-in-v1)

---

## 1. Composite Formula

### Pipeline

```
StreamingFeatureVector
  │
  ├── Hard Gates (any fail → immediate REJECT, no scoring)
  │     • stale_feed
  │     • max_spread
  │     • max_divergence
  │     • execution_feasibility
  │     • warmup
  │
  ├── 5 Primary Sub-scores [0, 1]
  │     • momentum_score       (w = 0.35)
  │     • orderflow_score      (w = 0.25)
  │     • liquidation_score    (w = 0.10)
  │     • microstructure_score (w = 0.20)
  │     • cross_venue_score    (w = 0.10)
  │                             ──────
  │                             Σ = 1.00
  │
  ├── Context Modifier [0, 1]
  │     • whale_risk_flag:  × 0.7 if active
  │     • urgent_news_flag: × 0.7 if active
  │     • warmup incomplete: × 0.0 (hard block)
  │
  ├── Composite
  │     primary_score = Σ(weight_i × sub_score_i)
  │     composite     = primary_score × context_multiplier
  │
  └── Tier Mapping
        A:      composite ≥ 0.72
        B:      composite ≥ 0.55
        C:      composite ≥ 0.40
        REJECT: composite < 0.40
```

### Sub-score Formulas

#### 1. Momentum Score (weight: 0.35)

Multi-timeframe weighted combination of returns_z and momentum_z.

```
For each timeframe (10s/30s/60s/5m):
  combined_z = 0.6 × returns_z + 0.4 × momentum_z
  tf_score   = clamp((combined_z + 3) / 6, 0, 1)

momentum = Σ(tf_score × tf_weight)
```

| Timeframe | Weight | Rationale |
|---|---|---|
| 10s | 0.15 | Noisy, but captures immediate momentum |
| 30s | 0.25 | Good balance of responsiveness and stability |
| 60s | 0.35 | Sweet spot — filters tick noise, stays responsive |
| 5m  | 0.25 | Captures trend context, slightly lagging |

Returns_z captures price movement magnitude; momentum_z captures flow pressure.
The 60/40 split prioritizes price action over flow because price is what you get filled at.

#### 2. Orderflow Score (weight: 0.25)

Multi-timeframe taker flow imbalance.

```
For each timeframe:
  tf_score = (TFI + 1) / 2     # map [-1, +1] → [0, 1]

orderflow = Σ(tf_score × tf_weight)   # same timeframe weights
```

TFI = +1 (all buy-taker) → score 1.0
TFI = 0 (balanced) → score 0.5
TFI = -1 (all sell-taker) → score 0.0

#### 3. Liquidation Score (weight: 0.10)

Liquidation imbalance from 60s and 5m windows (shorter windows are usually empty).

```
For long-only positions:
  score = (-LI + 1) / 2    # invert: short squeeze = bullish

liquidation = 0.4 × score_60s + 0.6 × score_5m
```

Low weight (0.10) because liquidations are sparse and noisy.
When no liquidations exist: score = 0.5 (neutral).

#### 4. Microstructure Score (weight: 0.20)

Three components from the 60s timeframe:

```
spread_tightness = max(0, 1 - spread_bps / 15)
widening_health  = 1.0 if widening ≤ 1.0 else max(0, 1 - (widening - 1) / 3)
ob_favorability  = (OBI + 1) / 2

microstructure = 0.4 × spread + 0.3 × widening + 0.3 × OB
```

Spread tightness is weighted highest because it directly affects fill quality.

#### 5. Cross-Venue Score (weight: 0.10)

```
score = clamp(0.5 + divergence_bps / 40, 0, 1)
```

Binance higher than Bybit → mildly bullish (primary venue leads).
Low weight because divergence is a weak signal and often noise.

#### 6. Context Modifier (multiplier, not weighted)

```
multiplier = 1.0
if whale_risk_flag:  multiplier *= 0.7
if urgent_news_flag: multiplier *= 0.7
if !warmup_complete: multiplier = 0.0
```

Context can only **reduce** the composite. It never increases it.
Both flags active: 0.7 × 0.7 = 0.49 — nearly halves the score.

### None Handling

When a feature value is None (insufficient data for that window):
- The sub-score component uses a **neutral** value (0.5)
- The `imputed_count` field tracks how many features were missing
- High imputed counts signal unreliable scoring

---

## 2. Gate Conditions

Gates are binary pass/fail checks. They run **before** sub-score computation.
If any gate fails, the signal is immediately rejected with no scoring overhead.

| Gate | Condition | Threshold | Rationale |
|---|---|---|---|
| `stale_feed` | `freshness.composite ≥ T` | T = 0.50 | Trading on stale data is gambling |
| `max_spread` | `spread_bps ≤ T` | T = 15.0 bps | Wide spread eats expected profit |
| `max_divergence` | `|divergence_bps| ≤ T` | T = 50.0 bps | Extreme divergence = data issue or flash crash |
| `execution_feasibility` | `feasibility ≥ T` | T = 0.30 | Can't get reasonable fill below this |
| `warmup` | `warmup_complete == true` | – | Features unreliable during warmup |

### Gate Design Principles

1. **Gates reject, they don't approve.** Passing all gates is necessary but not sufficient.
2. **None values pass spread/divergence gates** (no data ≠ bad data). But None feasibility fails.
3. **Gates are checked before scoring** to avoid wasting CPU on doomed signals.
4. **Multiple failures are all reported** so operators can diagnose compound issues.

---

## 3. Tier Thresholds

| Tier | Threshold | Meaning | Recommended Action |
|---|---|---|---|
| **A** | ≥ 0.72 | Strong signal, favorable conditions | Full position size |
| **B** | ≥ 0.55 | Moderate signal, acceptable conditions | Reduced size (50-75%) |
| **C** | ≥ 0.40 | Weak signal, marginal conditions | Minimum size, tight stops |
| **REJECT** | < 0.40 | Below minimum quality | Do not trade |

### Why These Thresholds

- **A at 0.72**: With all-neutral features (0.5 across the board), the primary score is 0.5.
  Tier A requires meaningful conviction beyond neutral. At 0.72, you need most sub-scores
  above 0.65 with no severe penalties.

- **B at 0.55**: Just above neutral. Achievable with moderate positive signals in momentum
  and orderflow, even if other dimensions are neutral.

- **C at 0.40**: Below neutral. This catches cases where one dimension is strongly positive
  but others are slightly negative. These are marginal opportunities.

- **Thresholds are conservative for v1.** We'd rather miss good trades than take bad ones.
  Tune after 30+ days of paper trading data.

---

## 4. JSON Schema for Signal Outputs

```json
{
  "event_id": "uuid",
  "timestamp": "ISO 8601",
  "source": "scoring_signal_engine",
  "symbol": "BTCUSDT | ETHUSDT",
  "action": "open_long",

  "composite_score": 0.0-1.0,
  "primary_score": 0.0-1.0,
  "context_multiplier": 0.0-1.0,
  "tier": "A | B | C | REJECT",

  "sub_scores": {
    "momentum": 0.0-1.0,
    "orderflow": 0.0-1.0,
    "liquidation": 0.0-1.0,
    "microstructure": 0.0-1.0,
    "cross_venue": 0.0-1.0
  },

  "weights": {
    "momentum": 0.35,
    "orderflow": 0.25,
    "liquidation": 0.10,
    "microstructure": 0.20,
    "cross_venue": 0.10
  },

  "sub_score_details": {
    "momentum": {
      "score": 0.0-1.0,
      "components": {"tf_10s": ..., "tf_30s": ..., "tf_60s": ..., "tf_300s": ...},
      "imputed_count": 0,
      "description": "..."
    }
  },

  "gates_passed": true,
  "gate_results": [
    {"name": "stale_feed", "passed": true, "value": 0.98, "threshold": 0.5, "reason": ""},
    {"name": "max_spread", "passed": true, "value": 1.2, "threshold": 15.0, "reason": ""},
    {"name": "max_divergence", "passed": true, "value": 1.5, "threshold": 50.0, "reason": ""},
    {"name": "execution_feasibility", "passed": true, "value": 0.95, "threshold": 0.3, "reason": ""},
    {"name": "warmup", "passed": true, "value": 1.0, "threshold": 1.0, "reason": ""}
  ],

  "reason": {
    "primary_trigger": "composite_score_A",
    "supporting_factors": ["momentum=0.85", "orderflow=0.73", "microstructure=0.88"],
    "context_flags": {
      "whale_risk": false,
      "urgent_news": false,
      "warmup_complete": true,
      "context_multiplier": 1.0
    },
    "human_readable": "Composite 0.81 (Tier A). Strongest: momentum at 0.85. Primary 0.81 × context 1.00. Imputed 2 features."
  },

  "features_used": {
    "tf_60s_returns_z": 2.5,
    "tf_60s_momentum_z": 2.2,
    "tf_60s_tfi": 0.20,
    "tf_60s_spread_bps": 0.8,
    "freshness_composite": 0.99,
    "..."
  },

  "total_imputed_features": 2
}
```

---

## 5. Example Signal Records

### Example 1: Tier A — Strong Bullish (BTCUSDT)

```json
{
  "symbol": "BTCUSDT",
  "action": "open_long",
  "composite_score": 0.81,
  "primary_score": 0.81,
  "context_multiplier": 1.0,
  "tier": "A",
  "sub_scores": {
    "momentum": 0.85,
    "orderflow": 0.73,
    "liquidation": 0.62,
    "microstructure": 0.88,
    "cross_venue": 0.55
  },
  "reason": {
    "primary_trigger": "composite_score_A",
    "supporting_factors": ["momentum=0.85", "microstructure=0.88", "orderflow=0.73", "liquidation=0.62", "cross_venue=0.55"],
    "human_readable": "Composite 0.81 (Tier A). Strongest: microstructure at 0.88. Primary 0.81 × context 1.00. Imputed 2 features."
  }
}
```

**Reading**: Strong multi-timeframe momentum (z-scores ~2.0+), buy-dominant orderflow,
tight spread with no widening, some short liquidations (bullish). Clean context.
Recommended: full position size.

### Example 2: Tier B — Whale Flag Active (ETHUSDT)

```json
{
  "symbol": "ETHUSDT",
  "action": "open_long",
  "composite_score": 0.57,
  "primary_score": 0.81,
  "context_multiplier": 0.70,
  "tier": "B",
  "sub_scores": {
    "momentum": 0.85,
    "orderflow": 0.73,
    "liquidation": 0.62,
    "microstructure": 0.88,
    "cross_venue": 0.55
  },
  "reason": {
    "primary_trigger": "composite_score_B",
    "context_flags": {"whale_risk": true, "urgent_news": false, "context_multiplier": 0.70},
    "human_readable": "Composite 0.57 (Tier B). Strongest: microstructure at 0.88. Primary 0.81 × context 0.70. Imputed 2 features."
  }
}
```

**Reading**: Identical primary conditions to Example 1, but a whale transfer was detected.
Context multiplier drops from 1.0 → 0.7, pulling composite from A (0.81) to B (0.57).
The whale flag doesn't cancel the signal — it dampens it. Recommended: reduced size.

### Example 3: Gate Rejection — Stale Data

No `ScoredSignalEvent` is emitted. The engine logs:

```json
{
  "event": "signal_gated",
  "symbol": "BTCUSDT",
  "reasons": ["Data freshness 0.15 < 0.5 threshold", "Feasibility 0.08 < 0.3 threshold"]
}
```

**Reading**: Data is stale and execution conditions are poor. No signal generated.
This is correct behavior — the system refuses to trade when it can't trust its data.

---

## 6. Determinism and Auditability

### Determinism Guarantees

1. **No randomness anywhere.** No `random.random()`, no `time.time()` in score computation,
   no jitter. Every function is pure: `f(features) → score`.

2. **Same StreamingFeatureVector → same ScoredSignalEvent.** Verified by explicit
   replay test: run the same vector through 3 independent engine instances, assert identical
   composite scores, tiers, and sub-scores.

3. **Cooldown uses monotonic clock**, which is deterministic for a single process
   (though not across replays — but cooldown is a rate-limit, not a scoring concern).

4. **No adaptive weights.** Weights are fixed in configuration. They don't change based
   on past performance. Any weight change requires a config commit (tracked in git).

5. **No state leakage between symbols.** Each symbol is scored independently. BTCUSDT
   features never influence ETHUSDT scoring.

### Auditability

Every `ScoredSignalEvent` carries:

| Field | Purpose |
|---|---|
| `composite_score` | Final decision number |
| `primary_score` | Pre-context composite |
| `context_multiplier` | How much context dampened |
| `sub_scores` | Each of 5 primary dimensions |
| `weights` | Exact weights used (from config) |
| `sub_score_details[*].components` | Per-timeframe breakdown within each sub-score |
| `sub_score_details[*].imputed_count` | How many features were missing |
| `gate_results` | Pass/fail + value + threshold for each gate |
| `reason.human_readable` | Plain-English explanation |
| `features_used` | Raw feature values that went into scoring |
| `total_imputed_features` | Total missing data count |

To reproduce any signal:
1. Read `features_used` from the stored event
2. Feed them through the scorer, gates, and composite functions
3. Verify you get the same `composite_score` and `tier`

This is possible because all functions are pure and the weights are in the event payload.

---

## 7. Avoiding Overfitting in v1

### What We Do NOT Do

| Anti-pattern | Why It's Dangerous | Our Approach |
|---|---|---|
| Optimize weights on backtests | Fits past data, fails on new regimes | Fixed weights from domain knowledge |
| Add interaction terms (w₁ × w₂) | Exponential parameter space, overfits | Strictly additive linear combination |
| Use ML models for scoring | Black box, hard to audit, needs training data | Transparent weighted formula |
| Tune thresholds to maximize past PnL | Overfits to specific market conditions | Conservative fixed thresholds |
| Add more features to "improve" the model | Kitchen-sink fallacy; noise overwhelms signal | 10 features, no more in v1 |
| Auto-adjust weights based on performance | Adapts to noise, drifts from sound logic | Manual review + config change only |

### What We DO

1. **Fixed weights based on domain reasoning:**
   - Momentum and orderflow get the most weight because they represent the two fundamental
     dimensions of alpha: price movement and flow pressure.
   - Liquidation and cross-venue get low weight because they're noisy and sparse.
   - These weights are set once and don't change without a deliberate review.

2. **Simple linear combination:** `Σ(weight × score)`. No polynomial terms, no feature
   interactions, no non-linear transformations beyond the initial z-score mapping.

3. **Conservative thresholds:** Tier A at 0.72 means we're picky. We'd rather miss marginal
   trades than take bad ones. This is especially important during paper trading validation.

4. **Neutral-is-safe:** When features are missing (None), they contribute 0.5 (neutral).
   This means missing data pulls the score toward 0.5, not toward any extreme. The system
   becomes conservative when it has less information.

5. **Context can only hurt, never help:** The whale/news flags can only reduce the composite
   score (multiply by < 1.0). They can never push a borderline signal over a threshold.
   This prevents "whale chasing" where context events trigger trades.

6. **Mandatory paper trading period:** No weight changes are allowed until 30+ days of
   paper trading data is analyzed. Changes are then reviewed against:
   - Does the change improve Sharpe, not just win rate?
   - Does it work across both BTC and ETH?
   - Is the change robust to different market regimes (trending, ranging, volatile)?

7. **All weight changes require git commits.** Weights live in `config/defaults.toml`.
   Any change produces a diff, a commit message, and a PR. No silent tuning.

### The V1 Weight Selection Rationale

```
momentum:       0.35  ← Price movement is the primary alpha signal
orderflow:      0.25  ← Taker flow confirms or denies price moves
microstructure: 0.20  ← Market quality affects fill quality
liquidation:    0.10  ← Sparse but informative when present
cross_venue:    0.10  ← Weak signal, mostly confirmatory
                ────
                1.00
```

These weights will likely need adjustment after live observation. The first adjustment
should happen after Phase 3 (paper trading) collects 30+ days of data. The adjustment
process:

1. Export all `ScoredSignalEvent` records from DB
2. Join with actual outcomes (PnL per signal)
3. Compute per-sub-score correlation with outcome
4. Adjust weights proportionally — but never by more than ±0.05 per cycle
5. Validate on held-out time period (walk-forward, not random split)
6. Commit new weights with analysis rationale in the PR description
