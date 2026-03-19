"""Centralized Prometheus metric definitions and helpers.

Collects metrics across all CTE services for unified monitoring.
Individual modules define their own metrics; this module provides
shared utilities and the metrics registry setup.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Info, generate_latest

CTE_REGISTRY = CollectorRegistry(auto_describe=True)

cte_info = Info(
    "cte_build",
    "CTE build and version information",
    registry=CTE_REGISTRY,
)
cte_info.info({
    "version": "0.1.0",
    "python_version": "3.12",
    "engine_mode": "paper",
})


def get_metrics_text() -> bytes:
    """Generate Prometheus text format for all registered metrics."""
    return generate_latest()
