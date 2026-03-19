"""CTE exception hierarchy.

All CTE exceptions inherit from CTEError for consistent handling.
"""
from __future__ import annotations


class CTEError(Exception):
    """Base exception for all CTE errors."""

    def __init__(self, message: str, context: dict | None = None) -> None:
        super().__init__(message)
        self.context = context or {}


class ConnectionError(CTEError):
    """Raised when a venue connection fails."""


class ReconnectionExhaustedError(CTEError):
    """Raised when all reconnection attempts are exhausted."""


class DataValidationError(CTEError):
    """Raised when incoming market data fails schema validation."""


class NormalizationError(CTEError):
    """Raised when event normalization fails."""


class FeatureCalculationError(CTEError):
    """Raised when a feature calculation produces invalid results."""


class SignalError(CTEError):
    """Raised when signal generation encounters an error."""


class RiskVetoError(CTEError):
    """Raised when risk manager vetoes a signal."""

    def __init__(self, message: str, reason: str, context: dict | None = None) -> None:
        super().__init__(message, context)
        self.reason = reason


class SizingError(CTEError):
    """Raised when position sizing calculation fails."""


class ExecutionError(CTEError):
    """Raised when order execution encounters an error."""


class OrderRejectedError(ExecutionError):
    """Raised when an order is rejected by the venue."""


class RateLimitError(ExecutionError):
    """Raised when a venue rate limit is hit (HTTP 429)."""


class InsufficientBalanceError(OrderRejectedError):
    """Raised when venue rejects due to insufficient margin/balance."""


class InvalidQuantityError(OrderRejectedError):
    """Raised when venue rejects due to invalid lot size or quantity."""


class ReconciliationError(CTEError):
    """Raised when local and venue position states diverge."""


class ExitError(CTEError):
    """Raised when exit logic encounters an error."""


class DatabaseError(CTEError):
    """Raised when a database operation fails."""


class StreamError(CTEError):
    """Raised when Redis Stream operations fail."""


class ConfigurationError(CTEError):
    """Raised when configuration is invalid or missing."""
