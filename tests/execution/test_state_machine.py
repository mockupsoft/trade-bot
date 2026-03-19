"""Tests for order lifecycle state machine."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cte.execution.adapter import VenueOrderStatus
from cte.execution.state_machine import (
    TERMINAL_STATES,
    OrderStateMachine,
)


def _t():
    return datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestValidTransitions:
    def test_happy_path_market_order(self):
        sm = OrderStateMachine(client_order_id="test-1")
        assert sm.current_state == VenueOrderStatus.PENDING

        assert sm.transition(VenueOrderStatus.SUBMITTING, _t())
        assert sm.transition(VenueOrderStatus.FILLED, _t())
        assert sm.is_terminal

    def test_happy_path_limit_order(self):
        sm = OrderStateMachine()
        sm.transition(VenueOrderStatus.SUBMITTING, _t())
        sm.transition(VenueOrderStatus.SUBMITTED, _t())
        sm.transition(VenueOrderStatus.PARTIAL, _t())
        sm.transition(VenueOrderStatus.FILLED, _t())
        assert sm.is_terminal
        assert sm.transition_count == 4

    def test_cancel_flow(self):
        sm = OrderStateMachine()
        sm.transition(VenueOrderStatus.SUBMITTING, _t())
        sm.transition(VenueOrderStatus.SUBMITTED, _t())
        sm.transition(VenueOrderStatus.CANCELLING, _t())
        sm.transition(VenueOrderStatus.CANCELLED, _t())
        assert sm.current_state == VenueOrderStatus.CANCELLED
        assert sm.is_terminal

    def test_reject_from_submitting(self):
        sm = OrderStateMachine()
        sm.transition(VenueOrderStatus.SUBMITTING, _t())
        sm.transition(VenueOrderStatus.REJECTED, _t(), reason="Insufficient margin")
        assert sm.current_state == VenueOrderStatus.REJECTED

    def test_submit_failed_retry(self):
        sm = OrderStateMachine()
        sm.transition(VenueOrderStatus.SUBMITTING, _t())
        sm.transition(VenueOrderStatus.SUBMIT_FAILED, _t(), reason="Network timeout")
        assert not sm.is_terminal
        # Retry
        sm.transition(VenueOrderStatus.SUBMITTING, _t(), reason="Retry attempt 1")
        sm.transition(VenueOrderStatus.FILLED, _t())
        assert sm.is_terminal

    def test_cancel_failed_retry(self):
        sm = OrderStateMachine()
        sm.transition(VenueOrderStatus.SUBMITTING, _t())
        sm.transition(VenueOrderStatus.SUBMITTED, _t())
        sm.transition(VenueOrderStatus.CANCELLING, _t())
        sm.transition(VenueOrderStatus.CANCEL_FAILED, _t())
        # Retry cancel
        sm.transition(VenueOrderStatus.CANCELLING, _t())
        sm.transition(VenueOrderStatus.CANCELLED, _t())
        assert sm.is_terminal

    def test_filled_before_cancel_arrives(self):
        sm = OrderStateMachine()
        sm.transition(VenueOrderStatus.SUBMITTING, _t())
        sm.transition(VenueOrderStatus.SUBMITTED, _t())
        sm.transition(VenueOrderStatus.CANCELLING, _t())
        sm.transition(VenueOrderStatus.FILLED, _t(), reason="Filled before cancel")
        assert sm.current_state == VenueOrderStatus.FILLED

    def test_expired(self):
        sm = OrderStateMachine()
        sm.transition(VenueOrderStatus.SUBMITTING, _t())
        sm.transition(VenueOrderStatus.SUBMITTED, _t())
        sm.transition(VenueOrderStatus.EXPIRED, _t(), reason="FOK unfilled")
        assert sm.is_terminal


class TestInvalidTransitions:
    def test_cannot_go_backwards(self):
        sm = OrderStateMachine()
        sm.transition(VenueOrderStatus.SUBMITTING, _t())
        sm.transition(VenueOrderStatus.SUBMITTED, _t())
        result = sm.transition(VenueOrderStatus.PENDING, _t())
        assert not result
        assert sm.current_state == VenueOrderStatus.SUBMITTED

    def test_cannot_transition_from_terminal(self):
        sm = OrderStateMachine()
        sm.transition(VenueOrderStatus.SUBMITTING, _t())
        sm.transition(VenueOrderStatus.FILLED, _t())
        result = sm.transition(VenueOrderStatus.CANCELLED, _t())
        assert not result
        assert sm.current_state == VenueOrderStatus.FILLED

    def test_invalid_transition_logged(self):
        sm = OrderStateMachine()
        sm.transition(VenueOrderStatus.SUBMITTING, _t())
        sm.transition(VenueOrderStatus.SUBMITTED, _t())
        sm.transition(VenueOrderStatus.PENDING, _t())
        # Invalid transition is still recorded in audit trail
        assert any("INVALID" in t.reason for t in sm.transitions)


class TestProperties:
    def test_can_cancel(self):
        sm = OrderStateMachine()
        assert not sm.can_cancel
        sm.transition(VenueOrderStatus.SUBMITTING, _t())
        assert not sm.can_cancel
        sm.transition(VenueOrderStatus.SUBMITTED, _t())
        assert sm.can_cancel
        sm.transition(VenueOrderStatus.PARTIAL, _t())
        assert sm.can_cancel

    def test_terminal_states(self):
        for state in TERMINAL_STATES:
            sm = OrderStateMachine()
            sm.current_state = state
            assert sm.is_terminal
            assert not sm.is_active

    def test_audit_trail(self):
        sm = OrderStateMachine(client_order_id="audit-test")
        sm.transition(VenueOrderStatus.SUBMITTING, _t(), reason="Initial submit")
        sm.transition(VenueOrderStatus.FILLED, _t(), reason="Market fill",
                      venue_data={"orderId": "12345"})
        assert sm.transitions[0].reason == "Initial submit"
        assert sm.transitions[1].venue_data == {"orderId": "12345"}
