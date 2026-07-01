"""R22 no-livelock guard: once candidate rework for a pdd has escalated.

The candidate-rework sweep correctly caps retries and escalates via
``human.escalate`` + an owner-visible message when reviewer/integration findings
stay unresolved (orchestrator ``_run_candidate_rework_sweep``). But escalation
was not *terminal*: a spurious re-emitted ``task_map.ready`` (e.g. a fanout
aggregate re-firing its ``success_event`` after the rework chain ended) would
re-arm ``_maybe_start_writer_fanout`` and restart the whole impl→integration→
rework cycle from attempt 0 — an unbounded OUTER loop the per-chain cap can't
see (observed: cj-min R22, 4× task_map.ready / 3× integration.failed, never
converging after the cap escalated).

This predicate makes escalation terminal: an escalated pdd is *quarantined* and
must not auto-resume impl. Resuming requires an explicit operator authorization
(a ``candidate.rework.cleared`` event, or a ``task_map.ready`` carrying
``operator_authorized``). Bounded sweep retriggers (which carry ``rework_of``)
and operator-authorized re-plans are NOT quarantined — the orchestrator lets
those through before consulting this predicate.

Pure function over the event log so it is trivially unit-tested; the
orchestrator guards the writer-fanout chokepoint with it.
"""
from __future__ import annotations

from typing import Iterable

# Candidate-level failures whose exhausted rework escalates the whole candidate
# (task_id=None). These are the ``rework_source`` values the sweep stamps on its
# ``human.escalate`` — keying on them avoids quarantining on unrelated escalates.
CANDIDATE_FAILURE_SOURCES = frozenset({
    "integration.failed",
    "review.rejected",
    "test.failed",
    "judge.failed",
})


def is_pdd_rework_quarantined(events: Iterable, pdd_id: str) -> bool:
    """True if ``pdd_id`` has an unresolved candidate-rework escalation.

    Walks events in order: a candidate-rework ``human.escalate`` quarantines the
    pdd; a later ``candidate.rework.cleared`` or an ``operator_authorized``
    ``task_map.ready`` lifts it (latest wins, so re-escalation after a clear
    re-quarantines). The event log is per-run (state_dir reset on ``zf init``),
    so a prior round's escalate cannot leak across runs that reuse a pdd_id.
    """
    if not pdd_id:
        return False
    quarantined = False
    for e in events:
        etype = getattr(e, "type", None)
        payload = getattr(e, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        if str(payload.get("pdd_id") or "") != pdd_id:
            continue
        if etype == "human.escalate" and (
            str(payload.get("rework_source") or "") in CANDIDATE_FAILURE_SOURCES
        ):
            quarantined = True
        elif etype == "candidate.rework.cleared" or (
            etype == "task_map.ready" and bool(payload.get("operator_authorized"))
        ):
            quarantined = False
    return quarantined
