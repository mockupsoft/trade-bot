"""Epoch management for analytics isolation.

Each epoch represents a deployment phase with separate performance tracking.
Trades are tagged with their epoch at creation time. Analytics can be
filtered and compared across epochs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class EpochMode(str, Enum):
    PAPER = "paper"
    DEMO = "demo"
    LIVE = "live"
    SHADOW = "shadow"


PREDEFINED_EPOCHS = [
    "crypto_v1_paper",
    "crypto_v1_demo",
    "crypto_v1_live",
    "crypto_v1_shadow_short",
]


@dataclass
class Epoch:
    """A named analytics epoch with time boundaries."""

    name: str
    mode: EpochMode
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    description: str = ""
    config_snapshot: dict = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.ended_at is None

    @property
    def duration_hours(self) -> float:
        end = self.ended_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds() / 3600

    def close(self) -> None:
        self.ended_at = datetime.now(timezone.utc)


class EpochManager:
    """Manages the lifecycle of analytics epochs."""

    def __init__(self) -> None:
        self._epochs: dict[str, Epoch] = {}
        self._active_epoch: str | None = None

    def create_epoch(
        self, name: str, mode: EpochMode, description: str = ""
    ) -> Epoch:
        if name in self._epochs:
            raise ValueError(f"Epoch '{name}' already exists")
        epoch = Epoch(name=name, mode=mode, description=description)
        self._epochs[name] = epoch
        return epoch

    def activate(self, name: str) -> Epoch:
        """Set the active epoch. Only one epoch is active at a time."""
        if name not in self._epochs:
            raise ValueError(f"Epoch '{name}' not found")
        if self._active_epoch and self._active_epoch != name:
            self._epochs[self._active_epoch].close()
        self._active_epoch = name
        return self._epochs[name]

    @property
    def active(self) -> Epoch | None:
        if self._active_epoch:
            return self._epochs.get(self._active_epoch)
        return None

    @property
    def active_name(self) -> str:
        return self._active_epoch or "unknown"

    def get(self, name: str) -> Epoch | None:
        return self._epochs.get(name)

    def list_epochs(self) -> list[Epoch]:
        return list(self._epochs.values())
