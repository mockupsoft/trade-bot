"""Tests for configuration settings."""
from __future__ import annotations

import pytest

from cte.core.settings import (
    CTESettings,
    EngineMode,
    ExecutionMode,
)


class TestCTESettings:
    def test_default_settings_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CTE_ENGINE_MODE", "paper")
        monkeypatch.setenv("CTE_EXECUTION_MODE", "paper")
        settings = CTESettings()
        assert settings.engine.mode == EngineMode.PAPER
        assert "BTCUSDT" in settings.engine.symbols
        assert "ETHUSDT" in settings.engine.symbols
        assert settings.engine.max_leverage == 3
        assert "stream.binancefuture.com" in settings.binance.ws_combined_url

    def test_paper_mode_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CTE_ENGINE_MODE", "paper")
        monkeypatch.setenv("CTE_EXECUTION_MODE", "paper")
        settings = CTESettings()
        assert settings.execution.mode == ExecutionMode.PAPER

    def test_mismatched_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="paper"):
            CTESettings(
                engine={"mode": "paper"},
                execution={"mode": "live"},
            )

    def test_database_dsn(self):
        settings = CTESettings()
        dsn = settings.database.dsn
        assert "postgresql://" in dsn
        assert "cte" in dsn

    def test_risk_defaults(self):
        settings = CTESettings()
        assert settings.risk.max_position_pct == 0.05
        assert settings.risk.max_total_exposure_pct == 0.15
        assert settings.risk.emergency_stop_drawdown_pct == 0.05

    def test_leverage_cap(self):
        with pytest.raises((TypeError, ValueError)):
            CTESettings(engine={"max_leverage": 10})
