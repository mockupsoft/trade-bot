"""Tests for environment safety guards."""
from __future__ import annotations

from cte.ops.safety import SystemMode, validate_environment


class TestLiveModeBlocked:
    def test_live_mode_always_fails(self):
        results = validate_environment("live")
        assert any(not r.passed and r.check == "live_mode_blocked" for r in results)


class TestSeedMode:
    def test_seed_mode_always_passes(self):
        results = validate_environment("seed")
        assert all(r.passed for r in results)


class TestDemoSafetyGuards:
    def test_production_url_blocked(self):
        results = validate_environment(
            "demo",
            binance_rest_url="https://fapi.binance.com",
            binance_api_key="key",
            binance_api_secret="secret",
        )
        blocked = [r for r in results if not r.passed and r.check == "binance_production_guard"]
        assert len(blocked) == 1

    def test_testnet_url_passes(self):
        results = validate_environment(
            "demo",
            binance_rest_url="https://testnet.binancefuture.com",
            binance_api_key="key",
            binance_api_secret="secret",
        )
        guard = [r for r in results if r.check == "binance_production_guard"]
        assert all(r.passed for r in guard)

    def test_missing_api_keys_fails(self):
        results = validate_environment(
            "demo",
            binance_rest_url="https://testnet.binancefuture.com",
            binance_api_key="",
            binance_api_secret="",
        )
        key_check = [r for r in results if r.check == "api_keys_required"]
        assert len(key_check) == 1
        assert not key_check[0].passed

    def test_demo_with_valid_config_passes(self):
        results = validate_environment(
            "demo",
            binance_rest_url="https://testnet.binancefuture.com",
            binance_api_key="test_key_123",
            binance_api_secret="test_secret_456",
        )
        assert all(r.passed for r in results)

    def test_bybit_production_blocked(self):
        results = validate_environment(
            "demo",
            bybit_rest_url="https://api.bybit.com",
            binance_api_key="key",
            binance_api_secret="secret",
        )
        blocked = [r for r in results if not r.passed and r.check == "bybit_production_guard"]
        assert len(blocked) == 1


class TestPaperMode:
    def test_paper_mode_passes(self):
        results = validate_environment("paper")
        assert all(r.passed for r in results)


class TestSystemModeEnum:
    def test_all_modes_exist(self):
        assert SystemMode.SEED.value == "seed"
        assert SystemMode.PAPER.value == "paper"
        assert SystemMode.DEMO.value == "demo"
        assert SystemMode.LIVE.value == "live"
