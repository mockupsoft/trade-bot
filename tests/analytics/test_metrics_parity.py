from decimal import Decimal

from cte.analytics.metrics import CompletedTrade, compute_all_metrics


def _trade(direction="long", pnl=10) -> CompletedTrade:
    return CompletedTrade(
        symbol="BTCUSDT", venue="binance", tier="A", epoch="paper",
        direction=direction, source="paper_simulated",
        pnl=Decimal(str(pnl)), exit_reason="tp", exit_layer=4,
        hold_seconds=300, r_multiple=1.0, entry_latency_ms=100,
        modeled_slippage_bps=5.0, mfe_pct=0.02, mae_pct=0.005,
        was_profitable_at_exit=pnl > 0, position_mode="normal",
        warmup_phase="full"
    )

def test_metrics_direction_splits():
    trades = [
        _trade(direction="long", pnl=100),
        _trade(direction="long", pnl=-50),
        _trade(direction="short", pnl=200),
    ]

    metrics = compute_all_metrics(trades)

    assert metrics["trade_count"] == 3
    assert metrics["total_pnl"] == 250.0

    splits = metrics["direction_splits"]
    assert splits["long_trade_count"] == 2
    assert splits["short_trade_count"] == 1

    assert splits["long_pnl"] == 50.0
    assert splits["short_pnl"] == 200.0

    assert splits["long_win_rate"] == 0.5
    assert splits["short_win_rate"] == 1.0
