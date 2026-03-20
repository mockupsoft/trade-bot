"""Dashboard paper loop risk overrides."""
from __future__ import annotations

from cte.core.settings import RiskSettings
from cte.dashboard.paper_runner import _dashboard_risk_settings


def test_dashboard_risk_raises_total_exposure_for_ten_symbols() -> None:
    base = RiskSettings()
    tuned = _dashboard_risk_settings(base, 10)
    assert tuned.max_total_exposure_pct >= 0.49
    assert tuned.max_total_exposure_pct <= 1.0


def test_dashboard_risk_keeps_higher_env_total() -> None:
    base = RiskSettings(max_total_exposure_pct=0.6)
    tuned = _dashboard_risk_settings(base, 10)
    assert tuned.max_total_exposure_pct >= 0.6
