"""Order lifecycle state machine.

Enforces valid transitions and records full audit trail.
Every state change must go through this machine — no direct status mutations.

State diagram:
  PENDING ──→ SUBMITTING ──→ SUBMITTED ──→ PARTIAL ──→ FILLED
                  │               │            │
                  ▼               ▼            ▼
             SUBMIT_FAILED   CANCELLING    FILLED
                              │    │
                              ▼    ▼
                         CANCELLED  CANCEL_FAILED
                                        │
                                        ▼
                                   CANCELLING (retry)

  SUBMITTED ──→ REJECTED (venue rejects after accept — rare)
  SUBMITTED ──→ EXPIRED  (TTL or FOK/IOC timeout)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from cte.execution.adapter import VenueOrderStatus

# Valid transitions: {from_state: {to_states}}
VALID_TRANSITIONS: dict[VenueOrderStatus, set[VenueOrderStatus]] = {
    VenueOrderStatus.PENDING: {
        VenueOrderStatus.SUBMITTING,
        VenueOrderStatus.REJECTED,       # pre-validation reject
    },
    VenueOrderStatus.SUBMITTING: {
        VenueOrderStatus.SUBMITTED,
        VenueOrderStatus.FILLED,          # market order fills instantly
        VenueOrderStatus.PARTIAL,         # partial fill on submit
        VenueOrderStatus.REJECTED,
        VenueOrderStatus.SUBMIT_FAILED,   # network error
    },
    VenueOrderStatus.SUBMITTED: {
        VenueOrderStatus.PARTIAL,
        VenueOrderStatus.FILLED,
        VenueOrderStatus.CANCELLING,
        VenueOrderStatus.CANCELLED,       # venue auto-cancel
        VenueOrderStatus.REJECTED,        # late reject
        VenueOrderStatus.EXPIRED,
    },
    VenueOrderStatus.PARTIAL: {
        VenueOrderStatus.FILLED,
        VenueOrderStatus.CANCELLING,
        VenueOrderStatus.CANCELLED,
    },
    VenueOrderStatus.CANCELLING: {
        VenueOrderStatus.CANCELLED,
        VenueOrderStatus.FILLED,          # filled before cancel arrived
        VenueOrderStatus.CANCEL_FAILED,
    },
    VenueOrderStatus.CANCEL_FAILED: {
        VenueOrderStatus.CANCELLING,      # retry cancel
        VenueOrderStatus.FILLED,          # filled while we retry
        VenueOrderStatus.CANCELLED,       # eventually cancelled
    },
    # Terminal states — no further transitions
    VenueOrderStatus.FILLED: set(),
    VenueOrderStatus.CANCELLED: set(),
    VenueOrderStatus.REJECTED: set(),
    VenueOrderStatus.EXPIRED: set(),
    VenueOrderStatus.SUBMIT_FAILED: {
        VenueOrderStatus.SUBMITTING,      # retry submit
    },
}

TERMINAL_STATES = {
    VenueOrderStatus.FILLED,
    VenueOrderStatus.CANCELLED,
    VenueOrderStatus.REJECTED,
    VenueOrderStatus.EXPIRED,
}


@dataclass
class StateTransition:
    """Record of a single state transition."""
    from_state: VenueOrderStatus
    to_state: VenueOrderStatus
    timestamp: str
    reason: str = ""
    venue_data: dict = field(default_factory=dict)


@dataclass
class OrderStateMachine:
    """Tracks order lifecycle with enforced valid transitions.

    Every order gets its own state machine instance.
    All transitions are recorded for audit.
    """

    client_order_id: str = ""
    current_state: VenueOrderStatus = VenueOrderStatus.PENDING
    transitions: list[StateTransition] = field(default_factory=list)
    created_at: str = ""

    def transition(
        self,
        to_state: VenueOrderStatus,
        timestamp: datetime,
        reason: str = "",
        venue_data: dict | None = None,
    ) -> bool:
        """Attempt a state transition. Returns True if valid, False if invalid.

        Invalid transitions are logged but not applied (defensive).
        """
        valid_next = VALID_TRANSITIONS.get(self.current_state, set())

        if to_state not in valid_next:
            self.transitions.append(StateTransition(
                from_state=self.current_state,
                to_state=to_state,
                timestamp=timestamp.isoformat(),
                reason=f"INVALID: {reason}",
                venue_data=venue_data or {},
            ))
            return False

        self.transitions.append(StateTransition(
            from_state=self.current_state,
            to_state=to_state,
            timestamp=timestamp.isoformat(),
            reason=reason,
            venue_data=venue_data or {},
        ))
        self.current_state = to_state
        return True

    @property
    def is_terminal(self) -> bool:
        return self.current_state in TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        return not self.is_terminal

    @property
    def can_cancel(self) -> bool:
        return self.current_state in {
            VenueOrderStatus.SUBMITTED,
            VenueOrderStatus.PARTIAL,
        }

    @property
    def transition_count(self) -> int:
        return len(self.transitions)
