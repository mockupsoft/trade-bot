"""Staged warmup thresholds for dashboard paper loop."""
from __future__ import annotations

from typing import TYPE_CHECKING

from cte.dashboard.paper_runner import _dashboard_warmup_thresholds

if TYPE_CHECKING:
    import pytest


def test_warmup_full_gt_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CTE_DASHBOARD_PAPER_WARMUP_MIDS_EARLY", "25")
    monkeypatch.setenv("CTE_DASHBOARD_PAPER_WARMUP_MIDS_FULL", "25")
    early, full = _dashboard_warmup_thresholds()
    assert early == 25
    assert full == 26
