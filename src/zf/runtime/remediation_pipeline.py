"""Remediation State Machine — the unified interpreter (doc 80 rev1, P0).

doc 79 validated the Remediation Tier architecture (R14: no-dead-end, graceful
safe-halt). doc 80 rev1 found the *implementation* inelegant: 6 tick-sweeps + 5
notify points + N scattered hooks, and a "single entry" that was only a
convention (5 modules, 16 decision functions, 0 ``__all__`` — bypassable in 5
minutes). The fix (cc-arch review) is the Failure-State-Machine framing:

    each failure is a state machine instance; every transition is an event
    (truth in events.jsonl); ``correlation_id = fingerprint``; the pipeline is
    a *single interpreter* that rebuilds incomplete SMs from the event log each
    tick and advances each one step.

The forcing function becomes physical: a hook that calls ``decide_*`` WITHOUT
emitting ``remediation.classified`` never enters an SM → the interpreter never
sees it → the remediation never advances → the bypass surfaces as a stuck SM.

P0 (this module) is the **classify + route pure functions + SM state model**,
plus the r14 equivalence proof (the route step reproduces the scattered
``decide_cascade`` + ``decide_repair`` decisions). Zero wiring — bypass/旁路 —
so it is risk-free. P1+ wires the interpreter into the reactor and retires the
sweeps one at a time (strangler fig).

Reuses (does not rebuild): ``rework_triage`` taxonomy, ``remediation_cascade``,
``repair_authorization``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from zf.core.events.model import ZfEvent
from zf.runtime.remediation_cascade import (
    CASCADE_ESCALATE,
    CASCADE_RETRY,
    CASCADE_SAFE_HALT,
    classify_bucket,
)
from zf.runtime.repair_authorization import DEFAULT_REPAIR_CAP, KERNEL_LOGIC_STRUCTURAL
from zf.runtime.repair_dispatch import DISPATCHED, pending_repair_dispatches

# --- SM states + transition events -----------------------------------------

SM_DETECTED = "detected"
SM_CLASSIFIED = "classified"
SM_ROUTED = "routed"
SM_CONSUMED = "consumed"
SM_TERMINAL = "terminal"

# N4: registry of "decision" functions — the SM-stuck-on-bypass forcing
# function (rev1 § 3 / § 6 inv 1) only holds if these are not invoked outside
# the pipeline interpreter. Locked by
# tests/test_remediation_decision_locked_to_pipeline.py — new entries here =
# automatic enforcement (no separate grep to maintain).
# Tool functions (e.g. recovery_sufficiency.verify_artifact_ref) are NOT
# locked: they have legitimate non-remediation uses, per the "锁决策、不锁工具"
# narrowing in doc 80 rev1 § 6 inv 1.
DECISION_FUNCTIONS: dict[str, frozenset[str]] = {
    "runtime.remediation_pipeline": frozenset({"route"}),
    "runtime.remediation_cascade": frozenset({
        "decide_cascade", "classify_bucket", "build_safe_halt_payload",
        # cascade action verbs are decision constants — exporting them
        # outside the pipeline lets a caller hand-roll a cascade decision
        # bypassing route(); they ride on the lock with the same logic.
        "CASCADE_RETRY", "CASCADE_ESCALATE", "CASCADE_SAFE_HALT",
    }),
    "runtime.repair_authorization": frozenset({"decide_repair"}),
    "runtime.rework_triage": frozenset({"derive_taxonomy_bucket"}),
}

EV_CLASSIFIED = "remediation.classified"
EV_ROUTED = "remediation.routed"
EV_CONSUMED = "remediation.consumed"
EV_SAFE_HALTED = "runtime.safe_halted"
# N1: natural-completion markers — without these the SM only reaches terminal
# via safe-halt (the floor). Tier1 cascade success and Tier3 owner ack both
# need their own terminal event so the interpreter stops re-advancing.
EV_RECOVERED = "remediation.recovered"            # Tier1 cascade success
EV_ESCALATED_ACKED = "remediation.escalated_acked"  # Tier3 owner ack received
# N10: stuck-SM observation — bypassing the pipeline leaves SMs frozen at
# `detected`; this metric event surfaces one signal per tick when the
# incomplete-SM count crosses the threshold, so the "bypass = SM stuck"
# forcing function (rev1 § 6 inv 1) is actually observable instead of
# requiring the operator to grep events.jsonl.
EV_SM_STUCK_OBSERVED = "remediation.sm_stuck_observed"
# G4 (doc 87 rev3 / R24): trace success is ALSO a terminal for the failure
# ledger — judge.passed three minutes before the SM consumed an already-
# resolved failure ("给已愈合的伤口派手术"). A success terminal observed for
# the SM's task after the failure was detected closes the SM.
EV_SUPERSEDED = "remediation.superseded_by_success"
TRACE_SUCCESS_TYPES = frozenset({
    "judge.passed", "task.done", "ship.completed",
})
DEFAULT_STUCK_THRESHOLD = 5
STUCK_THRESHOLD_ENV = "ZF_REMEDIATION_STUCK_THRESHOLD"
_STUCK_SAMPLE_LIMIT = 5

# Failure events that START a remediation SM (the "detected" state). The
# interpreter opens an SM for each, keyed by correlation_id = fingerprint.
FAILURE_DETECTED_TYPES = frozenset({
    "worker.stuck.recovery_failed",
    "review.rejected",
    "test.failed",
    "judge.failed",
    "autoresearch.bug_candidate.created",
})

# --- tiers ------------------------------------------------------------------

TIER1 = "tier1"   # 确定性兜底 (cascade / recover)
TIER2 = "tier2"   # 有界 LLM 自愈
TIER3 = "tier3"   # escalate + liveness
SAFE_HALT = "safe_halt"  # 地板 (含 quiesce)


def state_of(event_type: str) -> str | None:
    """Map an event type to the SM state it represents, or None if it is not a
    remediation-SM event."""
    if event_type == EV_CLASSIFIED:
        return SM_CLASSIFIED
    if event_type == EV_ROUTED:
        return SM_ROUTED
    if event_type == EV_CONSUMED or event_type == DISPATCHED:
        # P3: the Tier2 self-repair dispatch IS the consume — once dispatched,
        # the SM is consumed and must not re-advance.
        return SM_CONSUMED
    if event_type in (EV_SAFE_HALTED, EV_RECOVERED, EV_ESCALATED_ACKED,
                      EV_SUPERSEDED):
        return SM_TERMINAL
    if event_type in FAILURE_DETECTED_TYPES:
        return SM_DETECTED
    return None


def is_terminal(state: str) -> bool:
    return state == SM_TERMINAL


# --- the route step (unifies decide_cascade + decide_repair) ----------------

@dataclass(frozen=True)
class RouteDecision:
    tier: str       # TIER1 / TIER2 / TIER3 / SAFE_HALT
    action: str     # within-tier: retry/escalate/safe_halt | dispatch/skip
    failure_class: str
    bucket: str
    reason: str


def route(
    failure_class: str,
    *,
    attempts: int,
    cap: int = DEFAULT_REPAIR_CAP,
    liveness: bool = True,
    authorized: bool = False,
) -> RouteDecision:
    """Classify a failure then route it to a tier — the single decision point.

    This is the unified form of the scattered ``decide_cascade`` (infra/Tier1)
    and ``decide_repair`` (content+kernel-logic/Tier2) functions; the P0
    equivalence test proves it reproduces their decisions on the r14 classes.

    - ``infra`` (transient, e.g. worker_stuck) → Tier1 cascade: retry under cap;
      exhausted → escalate (liveness) else safe-halt (the R12-limbo fix).
    - ``content`` + kernel-logic-structural (handoff_stall, …) → Tier2 LLM
      self-repair (only when authorized; at cap → Tier3 escalate).
    - ``terminal`` → Tier3 escalate (liveness) else safe-halt.
    - ``unknown`` → safe-halt (no-dead-end fail-safe).
    """
    bucket = classify_bucket(failure_class)

    def _d(tier: str, action: str, reason: str) -> RouteDecision:
        return RouteDecision(tier, action, failure_class, bucket, reason)

    if bucket == "infra":
        # mirror decide_cascade
        if attempts < cap:
            return _d(TIER1, CASCADE_RETRY, f"infra retry {attempts}/{cap}")
        if liveness:
            return _d(TIER1, CASCADE_ESCALATE,
                      "infra retry exhausted → escalate (structural, not a blip)")
        return _d(SAFE_HALT, CASCADE_SAFE_HALT,
                  "infra retry exhausted, no operator reachable → safe-halt (not limbo)")

    if bucket == "content" or failure_class in KERNEL_LOGIC_STRUCTURAL:
        # mirror decide_repair (the Tier2 self-repair gate)
        if not authorized:
            return _d(TIER2, "skip", "auto-repair not authorized (propose-only)")
        if attempts >= cap:
            return _d(TIER3, CASCADE_ESCALATE,
                      f"auto-repair cap {cap} reached → escalate to human")
        kind = "content" if bucket == "content" else f"kernel-logic ({failure_class})"
        return _d(TIER2, "dispatch", f"{kind}, authorized, under cap → Tier2 self-repair")

    if bucket == "terminal":
        if liveness:
            return _d(TIER3, CASCADE_ESCALATE, f"terminal ({failure_class}) → escalate")
        return _d(SAFE_HALT, CASCADE_SAFE_HALT,
                  f"terminal ({failure_class}), no operator → safe-halt")

    # unknown → no-dead-end fail-safe
    return _d(SAFE_HALT, CASCADE_SAFE_HALT,
              f"unrecognised class ({failure_class!r}) → fail-safe safe-halt")


# --- P1: the interpreter (rebuild SMs from events, advance one step) --------
#
# Side-effect-free in P1: it emits ONLY the marker transitions
# (remediation.classified/routed/consumed) — nothing consumes them yet, so it
# runs in parallel with the old sweeps for equivalence observation. P2 moves the
# real effects (cascade/dispatch/owner/safe-halt) into the consume step.

def _etype(event) -> str:
    return event.get("type") if isinstance(event, dict) else getattr(event, "type", "")


def _payload(event) -> dict:
    p = event.get("payload") if isinstance(event, dict) else getattr(event, "payload", None)
    return p if isinstance(p, dict) else {}


def _corr(event) -> str:
    if isinstance(event, dict):
        return str(event.get("correlation_id") or "")
    return str(getattr(event, "correlation_id", "") or "")


def fingerprint_of(event) -> str:
    """The SM key (correlation_id) for a failure event — coalesces same-source
    failures (135 worker.stuck for one worker = one fingerprint = one SM)."""
    t, p = _etype(event), _payload(event)
    if t == "worker.stuck.recovery_failed":
        return f"worker_stuck:{p.get('instance_id') or p.get('role') or ''}"
    if t == "autoresearch.bug_candidate.created":
        cand = p.get("candidate") if isinstance(p.get("candidate"), dict) else {}
        return str(cand.get("fingerprint") or "")
    if t in ("review.rejected", "test.failed", "judge.failed"):
        subj = p.get("fanout_id") or p.get("task_id") or p.get("target_ref") or ""
        return f"{t}:{subj}"
    return ""


def correlation_of(event) -> str:
    """A remediation.* / terminal event carries correlation_id; a failure event
    derives its fingerprint."""
    if _etype(event).startswith("remediation.") or _etype(event) in (EV_SAFE_HALTED, DISPATCHED):
        return _corr(event)
    return fingerprint_of(event)


def failure_class_of(event) -> str:
    """Extract the failure class from a failure event (classify input)."""
    t, p = _etype(event), _payload(event)
    if t == "worker.stuck.recovery_failed":
        return "worker_stuck"
    if t == "autoresearch.bug_candidate.created":
        cand = p.get("candidate") if isinstance(p.get("candidate"), dict) else {}
        fp = str(cand.get("fingerprint") or "")
        parts = fp.split(":")
        if len(parts) >= 2 and parts[0] == "failure":
            return parts[1]
        if parts and parts[0] == "stall":
            return "stall"
        return ""
    if t == "review.rejected":
        return "review_rejected_content"
    if t == "test.failed":
        return "test_failed_real"
    if t == "judge.failed":
        return "design_issue"
    return ""


def attempts_for(correlation_id: str, events) -> int:
    """Cross-incident attempt count for this SM — events-derived (no state
    store, doc 80 invariant 4). Counts prior routed/consumed transitions."""
    n = 0
    for e in events:
        if _etype(e) in (EV_ROUTED, EV_CONSUMED) and _corr(e) == correlation_id:
            n += 1
    return n


@dataclass(frozen=True)
class Transition:
    type: str
    correlation_id: str
    payload: dict


def build_sm_set(events) -> dict:
    """Rebuild the SM set from the event log: correlation_id → latest event.

    Resumable: this is the only state — restart rebuilds it from events.jsonl.
    Coalesce: same fingerprint → same correlation_id → one SM.
    """
    sm: dict[str, object] = {}
    for e in events:
        if state_of(_etype(e)) is None:
            continue
        cid = correlation_of(e)
        if cid:
            sm[cid] = e  # latest wins (events are chronological)
    return sm


def advance(correlation_id, latest_event, events, *, liveness=True, authorized=False,
            cap: int = DEFAULT_REPAIR_CAP) -> Transition | None:
    """Advance one SM one step → the next transition event, or None if terminal."""
    state = state_of(_etype(latest_event))
    if state == SM_DETECTED:
        fc = failure_class_of(latest_event)
        return Transition(EV_CLASSIFIED, correlation_id, {"failure_class": fc})
    if state == SM_CLASSIFIED:
        fc = str(_payload(latest_event).get("failure_class") or "")
        attempts = attempts_for(correlation_id, events)
        rd = route(fc, attempts=attempts, cap=cap, liveness=liveness, authorized=authorized)
        return Transition(EV_ROUTED, correlation_id, {
            "failure_class": fc, "tier": rd.tier, "action": rd.action, "reason": rd.reason,
        })
    if state == SM_ROUTED:
        tier = str(_payload(latest_event).get("tier") or "")
        action = str(_payload(latest_event).get("action") or "")
        # P3 (R20 fix): Tier2 consume = actually dispatch the authorized self-repair.
        # Find the pending dispatch_requested matching this SM's fingerprint and emit
        # autoresearch.repair.dispatched — the `zf self-repair` CLI spawns on that (in
        # a zaofu worktree). pending_repair_dispatches is events-derived + idempotent
        # (a dispatched already present → not pending → no re-dispatch). Tier1/Tier3/
        # skip, or Tier2-dispatch with no matching request, stay marker consume.
        if tier == TIER2 and action == "dispatch":
            for req in pending_repair_dispatches(events):
                if req.fingerprint == correlation_id:
                    return Transition(DISPATCHED, correlation_id, {
                        "fingerprint": req.fingerprint,
                        "attempt": req.attempt,
                        "candidate_id": req.candidate_id,
                        "candidate_path": req.candidate_path,
                        "repair_task_payload": req.repair_task_payload,
                    })
        return Transition(EV_CONSUMED, correlation_id, {"tier": tier, "action": action})
    return None  # consumed/terminal → done


def superseding_success(correlation_id, events):
    """Return the trace-success event that supersedes this SM, or None.

    The SM's origin failure event (detected state) carries the task_id; a
    TRACE_SUCCESS_TYPES event for the same task at/after the failure means
    the failure was resolved by normal pipeline progress — remediating it
    now would operate on an already-healed wound (R24: SM consumed a fixed
    failure 10 minutes after judge.passed).
    """
    origin = None
    for event in events:
        if state_of(_etype(event)) != SM_DETECTED:
            continue
        if correlation_of(event) != correlation_id:
            continue
        origin = event
        break
    if origin is None:
        return None
    task_id = str(
        getattr(origin, "task_id", None)
        or _payload(origin).get("task_id")
        or ""
    )
    if not task_id:
        return None  # conservative: no task linkage → no supersession
    origin_seen = False
    for event in events:
        if event is origin:
            origin_seen = True
            continue
        if not origin_seen:
            continue
        if _etype(event) not in TRACE_SUCCESS_TYPES:
            continue
        event_task = str(
            getattr(event, "task_id", None)
            or _payload(event).get("task_id")
            or ""
        )
        if event_task == task_id:
            return event
    return None


def remediation_tick(events, *, liveness=True, authorized=False) -> list[Transition]:
    """One interpreter step: advance every incomplete SM one transition.

    Idempotent: terminal SMs are skipped; an already-emitted transition is not
    re-emitted (the SM's latest state already reflects it).

    G4: before any advance, a trace-success supersession check closes the SM
    — running every tick means a success landing between ``routed`` and the
    dispatch tick prevents the Tier2 spawn (the route→spawn TOCTOU window
    R24 exposed: routed 01:34, consumed 01:41, judge.passed 01:31).
    """
    sm = build_sm_set(events)
    out: list[Transition] = []
    for cid, latest in sm.items():
        if is_terminal(state_of(_etype(latest))):
            continue
        success = superseding_success(cid, events)
        if success is not None:
            out.append(Transition(EV_SUPERSEDED, cid, {
                "superseded_by_event_id": getattr(success, "id", ""),
                "success_type": _etype(success),
                "task_id": str(getattr(success, "task_id", "") or ""),
                "reason": "trace success is a failure-ledger terminal (G4)",
            }))
            continue
        t = advance(cid, latest, events, liveness=liveness, authorized=authorized)
        if t is not None:
            out.append(t)
    return out


# N10 -----------------------------------------------------------------------

def incomplete_sm_count(events) -> int:
    """Count non-terminal SMs in the event log. The forcing function from
    rev1 § 6 inv 1 says bypassing the pipeline leaves an SM stuck (not
    terminal). This count surfaces stuck SMs to the metric layer."""
    sm = build_sm_set(events)
    return sum(
        1 for latest in sm.values()
        if not is_terminal(state_of(_etype(latest)))
    )


def _stuck_threshold(env: dict[str, str] | None = None) -> int:
    src = os.environ if env is None else env
    raw = str(src.get(STUCK_THRESHOLD_ENV) or "").strip()
    if not raw:
        return DEFAULT_STUCK_THRESHOLD
    try:
        v = int(raw)
        return v if v > 0 else DEFAULT_STUCK_THRESHOLD
    except ValueError:
        return DEFAULT_STUCK_THRESHOLD


def _stuck_samples(events) -> list[str]:
    """Up to _STUCK_SAMPLE_LIMIT correlation_ids of currently incomplete SMs,
    sorted for stable test/diff output."""
    sm = build_sm_set(events)
    stuck = [
        cid for cid, latest in sm.items()
        if not is_terminal(state_of(_etype(latest)))
    ]
    return sorted(stuck)[:_STUCK_SAMPLE_LIMIT]


# --- P1 reactor wiring: shadow/parallel runner (gated, side-effect-free) -----

SHADOW_ENV = "ZF_REMEDIATION_SM_SHADOW"


def remediation_sm_shadow_enabled(env: dict[str, str] | None = None) -> bool:
    """Default OFF. The shadow interpreter only runs when explicitly enabled,
    so P1 adds zero behavior to existing runs until an operator opts in to
    observe the SM in parallel with the old sweeps."""
    src = os.environ if env is None else env
    return str(src.get(SHADOW_ENV) or "").strip().lower() in ("1", "true", "on", "shadow")


def run_remediation_sm_shadow(events, writer, *, liveness=True, authorized=False) -> int:
    """P1 parallel/shadow: advance every SM one step and emit the marker
    transitions for observation. **Side-effect-free** — nothing consumes
    remediation.classified/routed/consumed yet, so this runs alongside the old
    sweeps without changing behavior (P2 moves the real effects into consume).

    N10: also surfaces a single `remediation.sm_stuck_observed` event per tick
    when the incomplete-SM count crosses the threshold, so a bypass of the
    pipeline (the rev1 § 6 inv 1 forcing function leaves frozen SMs)
    produces an observable signal — not just a silent buildup."""
    transitions = remediation_tick(events, liveness=liveness, authorized=authorized)
    n = 0
    for t in transitions:
        try:
            writer.append(ZfEvent(
                type=t.type,
                actor="zf-remediation-sm",
                payload=t.payload,
                correlation_id=t.correlation_id,
            ))
            n += 1
        except Exception:
            pass

    # N10 stuck observation — separate event, doesn't count toward the
    # advance return value (caller can tell shadow progress vs observation).
    count = incomplete_sm_count(events)
    threshold = _stuck_threshold()
    if count > threshold:
        try:
            writer.append(ZfEvent(
                type=EV_SM_STUCK_OBSERVED,
                actor="zf-remediation-sm",
                payload={
                    "count": count,
                    "threshold": threshold,
                    "samples": _stuck_samples(events),
                },
            ))
        except Exception:
            pass
    return n
