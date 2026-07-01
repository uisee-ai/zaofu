"""doc 80 P3: Tier2 consume — the SM actually dispatches the authorized self-repair.

R20 reproduced the gap live: autoresearch emits ``autoresearch.repair.dispatch_requested``
but nothing consumes it — the chain dead-ends at dispatch_requested, the stall never
self-heals (the "detect→backlog→fix" loop has no fix consumer). P3 wires the SM's
``routed == Tier2 && action == dispatch`` consume step to emit
``autoresearch.repair.dispatched`` (the ``zf self-repair`` CLI spawns on that),
closing the loop. Idempotent + events-derived (no re-dispatch once dispatched exists).
"""
from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.remediation_pipeline import (
    EV_CONSUMED,
    EV_ROUTED,
    SM_CONSUMED,
    TIER1,
    TIER2,
    advance,
    correlation_of,
    remediation_tick,
    state_of,
)
from zf.runtime.repair_dispatch import DISPATCHED

# R20's real fingerprint (autoresearch stall:candidate.ready->cj-min-candidate-review)
FP = "stall:candidate.ready->cj-min-candidate-review:CJMIN-R20"


def _routed(tier: str, action: str, cid: str = FP) -> ZfEvent:
    return ZfEvent(type=EV_ROUTED, payload={"tier": tier, "action": action}, correlation_id=cid)


def _dispatch_requested(fp: str = FP, attempt: int = 0) -> ZfEvent:
    return ZfEvent(
        type="autoresearch.repair.dispatch_requested",
        payload={
            "fingerprint": fp,
            "attempt": attempt,
            "candidate_id": "HIC-R20",
            "candidate_path": "/x/candidate.md",
            "repair_task_payload": {"contract": {"scope": ["src/zf/**"], "verification": "pytest x"}},
        },
    )


# --- SM state-model extensions (DISPATCHED == consumed) ---------------------

def test_dispatched_is_consumed_state():
    """The Tier2 dispatch IS the consume — so the SM must not re-advance it."""
    assert state_of(DISPATCHED) == SM_CONSUMED


def test_dispatched_correlation_uses_correlation_id():
    """The dispatched event is keyed to its SM by correlation_id (= fingerprint),
    not derived via fingerprint_of (which doesn't know repair events)."""
    ev = ZfEvent(type=DISPATCHED, payload={"fingerprint": FP}, correlation_id=FP)
    assert correlation_of(ev) == FP


# --- the P3 core: Tier2 routed → dispatched --------------------------------

def test_tier2_dispatch_emits_repair_dispatched():
    routed = _routed(TIER2, "dispatch")
    events = [_dispatch_requested(), routed]
    t = advance(FP, routed, events, authorized=True)
    assert t is not None
    assert t.type == DISPATCHED, f"Tier2 consume must dispatch, got {t.type}"
    assert t.correlation_id == FP
    assert t.payload["fingerprint"] == FP
    assert t.payload["candidate_id"] == "HIC-R20"
    # carries the planner request info so the CLI spawn can build the worktree
    assert "repair_task_payload" in t.payload


def test_tier2_dispatch_no_pending_falls_back_to_marker():
    """Tier2 dispatch but no matching dispatch_requested → plain marker consume,
    never a spurious dispatched (don't spawn a repair with no request)."""
    routed = _routed(TIER2, "dispatch")
    t = advance(FP, routed, [routed], authorized=True)
    assert t.type == EV_CONSUMED
    assert t.payload["tier"] == TIER2


def test_tier2_dispatch_idempotent_after_dispatched():
    """Once a dispatched exists for the fingerprint, pending excludes it →
    no second dispatch (events-derived idempotency, doc 80 invariant 4)."""
    routed = _routed(TIER2, "dispatch")
    dispatched = ZfEvent(type=DISPATCHED, payload={"fingerprint": FP, "attempt": 0}, correlation_id=FP)
    events = [_dispatch_requested(), dispatched, routed]
    t = advance(FP, routed, events, authorized=True)
    assert t.type == EV_CONSUMED, "must not re-dispatch once dispatched exists"


def test_tier1_consume_unchanged_marker():
    """P3 only changes Tier2-dispatch; Tier1 (and Tier3/skip) stay marker consume."""
    routed = _routed(TIER1, "retry")
    t = advance(FP, routed, [_dispatch_requested(), routed], authorized=True)
    assert t.type == EV_CONSUMED


# --- tick-level: a routed Tier2 SM advances to dispatched -------------------

def test_tick_advances_tier2_sm_to_dispatched():
    routed = _routed(TIER2, "dispatch")
    events = [_dispatch_requested(), routed]
    transitions = remediation_tick(events, authorized=True)
    types = [t.type for t in transitions]
    assert DISPATCHED in types, f"tick should dispatch the Tier2 SM, got {types}"
