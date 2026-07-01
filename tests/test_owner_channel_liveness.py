"""doc 79 Tier3: owner-channel liveness + safe-halt floor.

R12: 16x owner.visible_message.failed (dead token) yet the run kept escalating
286x to a channel nobody received. Liveness makes escalation honest — escalate
only if the channel is confirmed reachable; otherwise the cascade floors to a
deterministic safe-halt instead of shouting into the void.
"""

from __future__ import annotations

from types import SimpleNamespace

from zf.runtime.owner_channel_liveness import (
    CHANNEL_DEAD,
    CHANNEL_LIVE,
    CHANNEL_UNKNOWN,
    channel_liveness,
    operator_reachable,
)
from zf.runtime.remediation_cascade import (
    SAFE_HALTED_EVENT,
    build_safe_halt_payload,
)


def _ev(t):
    return SimpleNamespace(type=t)


DELIVERED = "owner.visible_message.delivered"
FAILED = "owner.visible_message.failed"


# --- channel_liveness -------------------------------------------------------

def test_no_signal_is_unknown():
    assert channel_liveness([]) == CHANNEL_UNKNOWN


def test_recent_delivered_is_live():
    assert channel_liveness([_ev(FAILED), _ev(DELIVERED)]) == CHANNEL_LIVE


def test_threshold_consecutive_failures_is_dead():
    # R12: many failures, never a delivery → channel is dead.
    evs = [_ev(FAILED)] * 3
    assert channel_liveness(evs, fail_threshold=3) == CHANNEL_DEAD


def test_a_few_failures_below_threshold_is_unknown():
    assert channel_liveness([_ev(FAILED)], fail_threshold=3) == CHANNEL_UNKNOWN


def test_delivered_then_later_failures_below_threshold_unknown():
    # delivered, then 2 failures (< threshold) → not yet declared dead
    evs = [_ev(DELIVERED), _ev(FAILED), _ev(FAILED)]
    assert channel_liveness(evs, fail_threshold=3) == CHANNEL_UNKNOWN


# --- operator_reachable: what the cascade's liveness param consumes ---------

def test_operator_reachable_true_unless_dead():
    assert operator_reachable([_ev(DELIVERED)]) is True
    assert operator_reachable([], fail_threshold=3) is True          # unknown → try
    assert operator_reachable([_ev(FAILED)] * 3, fail_threshold=3) is False  # dead → floor


# --- safe-halt payload (the cascade floor) ---------------------------------

def test_safe_halt_payload_carries_root_and_evidence():
    p = build_safe_halt_payload(
        root_failure_class="worker_stuck",
        evidence_event_ids=["evt-1", "evt-2"],
        reason="infra retry exhausted, owner channel dead",
    )
    assert p["root_failure_class"] == "worker_stuck"
    assert p["evidence_event_ids"] == ["evt-1", "evt-2"]
    assert p["reason"]
    assert p["resumable"] is True  # safe-halt freezes a resumable state


def test_safe_halted_event_name():
    assert SAFE_HALTED_EVENT == "runtime.safe_halted"
