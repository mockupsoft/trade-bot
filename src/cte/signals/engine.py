"""Scoring Signal Engine — weighted composite model with hard gates.

Consumes StreamingFeatureVector events from the streaming feature engine.
Produces ScoredSignalEvent with full auditability.

Pipeline:
  StreamingFeatureVector
    → Hard Gates (any fail → REJECT immediately)
    → 6 Sub-scores (pure functions)
    → Weighted Composite (primary_score × context_multiplier)
    → Tier Mapping (A/B/C/REJECT)
    → Cooldown Check
    → Emit ScoredSignalEvent

Every decision is deterministic and carries a complete reason chain.
"""
from __future__ import annotations

import time
from typing import Any

import structlog
from prometheus_client import Counter, Gauge, Histogram

from cte.core.events import (
    STREAM_KEYS,
    GateCheckResult,
    ScoredSignalEvent,
    SignalAction,
    SignalReason,
    SignalTier,
    StreamingFeatureVector,
    SubScoreBreakdown,
)
from cte.core.settings import SignalSettings
from cte.core.streams import StreamPublisher
from cte.signals.composite import (
    DEFAULT_TIER_THRESHOLDS,
    CompositeResult,
    SignalTier as CompositeTier,
    compute_composite,
)
from cte.signals.gates import GateVerdict, check_all_gates
from cte.signals.scorer import (
    ScoreDetail,
    compute_context_score,
    compute_cross_venue_score,
    compute_liquidation_score,
    compute_microstructure_score,
    compute_momentum_score,
    compute_orderflow_score,
)

logger = structlog.get_logger(__name__)

scored_signal_total = Counter(
    "cte_scored_signal_total", "Total scored signals", ["symbol", "tier"]
)
scored_signal_composite = Histogram(
    "cte_scored_signal_composite", "Composite score distribution", ["symbol"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.72, 0.8, 0.9, 1.0],
)
scored_gate_rejections = Counter(
    "cte_scored_gate_rejections_total", "Gate rejection count", ["symbol", "gate"]
)
scored_cooldown_active = Gauge(
    "cte_scored_cooldown_active", "Cooldown active", ["symbol"]
)


class ScoringSignalEngine:
    """Weighted scoring signal engine with hard gates and full auditability.

    Consumes StreamingFeatureVector, produces ScoredSignalEvent.
    All computation is deterministic — same input always produces same output.
    """

    def __init__(
        self,
        settings: SignalSettings,
        publisher: StreamPublisher,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._last_signal_time: dict[str, float] = {}
        self._signal_count_hour: dict[str, int] = {}
        self._hour_start: dict[str, float] = {}

        self._weights = {
            "momentum": settings.w_momentum,
            "orderflow": settings.w_orderflow,
            "liquidation": settings.w_liquidation,
            "microstructure": settings.w_microstructure,
            "cross_venue": settings.w_cross_venue,
        }

        self._tier_thresholds = {
            CompositeTier.A: settings.tier_a_threshold,
            CompositeTier.B: settings.tier_b_threshold,
            CompositeTier.C: settings.tier_c_threshold,
        }

    async def evaluate(
        self, vector: StreamingFeatureVector
    ) -> ScoredSignalEvent | None:
        """Evaluate a feature vector and produce a scored signal.

        Returns None if:
        - Any hard gate fails
        - Composite score is below tier C threshold (REJECT)
        - Symbol is on cooldown
        - Hourly signal limit reached
        """
        symbol = vector.symbol.value

        # Cooldown check (before expensive scoring)
        if self._is_on_cooldown(symbol):
            scored_cooldown_active.labels(symbol=symbol).set(1)
            return None
        scored_cooldown_active.labels(symbol=symbol).set(0)

        if self._hourly_limit_reached(symbol):
            return None

        # ── Step 1: Hard Gates ────────────────────────────────
        verdict = check_all_gates(
            vector,
            min_freshness=self._settings.gate_min_freshness,
            max_spread_bps=self._settings.gate_max_spread_bps,
            max_divergence_bps=self._settings.gate_max_divergence_bps,
            min_feasibility=self._settings.gate_min_feasibility,
        )

        if not verdict.all_passed:
            for r in verdict.results:
                if not r.passed:
                    scored_gate_rejections.labels(symbol=symbol, gate=r.name).inc()

            await logger.adebug(
                "signal_gated",
                symbol=symbol,
                reasons=verdict.rejection_reasons,
            )
            return None

        # ── Step 2: Sub-scores ────────────────────────────────
        momentum = compute_momentum_score(vector)
        orderflow = compute_orderflow_score(vector)
        liquidation = compute_liquidation_score(vector)
        microstructure = compute_microstructure_score(
            vector, max_spread_bps=self._settings.gate_max_spread_bps,
        )
        cross_venue = compute_cross_venue_score(vector)
        context = compute_context_score(vector)

        # ── Step 3: Composite ─────────────────────────────────
        result = compute_composite(
            momentum=momentum,
            orderflow=orderflow,
            liquidation=liquidation,
            microstructure=microstructure,
            cross_venue=cross_venue,
            context=context,
            weights=self._weights,
            tier_thresholds=self._tier_thresholds,
        )

        scored_signal_composite.labels(symbol=symbol).observe(result.composite_score)

        # ── Step 4: Tier filter ───────────────────────────────
        if result.tier == CompositeTier.REJECT:
            scored_signal_total.labels(symbol=symbol, tier="REJECT").inc()
            await logger.adebug(
                "signal_below_threshold",
                symbol=symbol,
                composite=result.composite_score,
            )
            return None

        # ── Step 5: Build signal event ────────────────────────
        action = SignalAction.OPEN_LONG  # v1: LONG only

        reason = _build_reason(result, vector)
        features_used = _extract_features_used(vector)

        gate_results = [
            GateCheckResult(
                name=r.name,
                passed=r.passed,
                value=r.value,
                threshold=r.threshold,
                reason=r.reason,
            )
            for r in verdict.results
        ]

        sub_score_details = {
            name: SubScoreBreakdown(
                score=detail.score,
                components=detail.components,
                imputed_count=detail.imputed_count,
                description=detail.description,
            )
            for name, detail in result.details.items()
        }

        signal = ScoredSignalEvent(
            symbol=vector.symbol,
            action=action,
            composite_score=result.composite_score,
            primary_score=result.primary_score,
            context_multiplier=result.context_multiplier,
            tier=SignalTier(result.tier.value),
            sub_scores=result.sub_scores,
            weights=result.weights,
            sub_score_details=sub_score_details,
            gates_passed=True,
            gate_results=gate_results,
            reason=reason,
            features_used=features_used,
            total_imputed_features=result.total_imputed,
        )

        # ── Step 6: Publish ───────────────────────────────────
        await self._publisher.publish(STREAM_KEYS["signal_scored"], signal)

        scored_signal_total.labels(symbol=symbol, tier=result.tier.value).inc()

        self._last_signal_time[symbol] = time.monotonic()
        self._increment_hourly_count(symbol)

        await logger.ainfo(
            "scored_signal_emitted",
            symbol=symbol,
            composite=result.composite_score,
            tier=result.tier.value,
            momentum=momentum.score,
            orderflow=orderflow.score,
            imputed=result.total_imputed,
        )

        return signal

    # ── Cooldown & Rate Limiting ──────────────────────────────

    def _is_on_cooldown(self, symbol: str) -> bool:
        last = self._last_signal_time.get(symbol)
        if last is None:
            return False
        return (time.monotonic() - last) < self._settings.cooldown_seconds

    def _hourly_limit_reached(self, symbol: str) -> bool:
        now = time.monotonic()
        start = self._hour_start.get(symbol, 0)
        if now - start > 3600:
            self._hour_start[symbol] = now
            self._signal_count_hour[symbol] = 0
            return False
        return self._signal_count_hour.get(symbol, 0) >= self._settings.max_signals_per_hour

    def _increment_hourly_count(self, symbol: str) -> None:
        self._signal_count_hour[symbol] = self._signal_count_hour.get(symbol, 0) + 1


def _build_reason(result: CompositeResult, vector: StreamingFeatureVector) -> SignalReason:
    """Construct a human-readable + machine-parseable reason payload."""
    top_score_name = max(result.sub_scores, key=result.sub_scores.get)
    top_score_val = result.sub_scores[top_score_name]

    factors = []
    for name, score in sorted(result.sub_scores.items(), key=lambda x: -x[1]):
        if score > 0.55:
            factors.append(f"{name}={score:.2f}")

    context_flags: dict[str, Any] = {
        "whale_risk": vector.whale_risk_flag,
        "urgent_news": vector.urgent_news_flag,
        "warmup_complete": vector.data_quality.warmup_complete,
        "context_multiplier": result.context_multiplier,
    }

    human = (
        f"Composite {result.composite_score:.2f} (Tier {result.tier.value}). "
        f"Strongest: {top_score_name} at {top_score_val:.2f}. "
        f"Primary {result.primary_score:.2f} × context {result.context_multiplier:.2f}. "
        f"Imputed {result.total_imputed} features."
    )

    return SignalReason(
        primary_trigger=f"composite_score_{result.tier.value}",
        supporting_factors=factors,
        context_flags=context_flags,
        human_readable=human,
    )


def _extract_features_used(vector: StreamingFeatureVector) -> dict[str, Any]:
    """Snapshot key features for reproducibility audit."""
    return {
        "tf_10s_returns_z": vector.tf_10s.returns_z,
        "tf_10s_momentum_z": vector.tf_10s.momentum_z,
        "tf_10s_tfi": vector.tf_10s.taker_flow_imbalance,
        "tf_30s_returns_z": vector.tf_30s.returns_z,
        "tf_30s_momentum_z": vector.tf_30s.momentum_z,
        "tf_30s_tfi": vector.tf_30s.taker_flow_imbalance,
        "tf_60s_returns_z": vector.tf_60s.returns_z,
        "tf_60s_momentum_z": vector.tf_60s.momentum_z,
        "tf_60s_tfi": vector.tf_60s.taker_flow_imbalance,
        "tf_60s_spread_bps": vector.tf_60s.spread_bps,
        "tf_60s_ob_imbalance": vector.tf_60s.ob_imbalance,
        "tf_60s_liq_imbalance": vector.tf_60s.liquidation_imbalance,
        "tf_60s_venue_div_bps": vector.tf_60s.venue_divergence_bps,
        "tf_5m_returns_z": vector.tf_5m.returns_z,
        "tf_5m_momentum_z": vector.tf_5m.momentum_z,
        "tf_5m_tfi": vector.tf_5m.taker_flow_imbalance,
        "tf_5m_liq_imbalance": vector.tf_5m.liquidation_imbalance,
        "freshness_composite": vector.freshness.composite,
        "execution_feasibility": vector.execution_feasibility,
        "whale_risk_flag": vector.whale_risk_flag,
        "urgent_news_flag": vector.urgent_news_flag,
        "last_price": str(vector.last_price),
    }
