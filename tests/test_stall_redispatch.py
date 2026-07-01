"""B1 (R20): bounded re-dispatch of a structurally-stalled stage trigger.

R20 dead-ended at candidate.ready→cj-min-candidate-review: the trigger fired
while the upstream affinity manifest / review lanes were not yet ready, so
_maybe_start_reader_fanout skipped it — and event-driven dispatch never retried
(candidate.ready fires once). A manual re-emit of candidate.ready un-stuck it
(a NEW event bypasses the kernel's per-trigger ``_fanout_started`` dedup).

stall_redispatch_event automates that: re-emit the stalled stage's trigger,
bounded by a per-fingerprint cap; at the cap, return None so the caller escalates
to autoresearch self-repair (no-dead-end: retry → retry → … → escalate).
"""
from __future__ import annotations

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.stall_detector import (
    StallFinding,
    detect_structural_stalls,
    emit_stall_recoveries,
    stall_redispatch_event,
)

STAGES = [("cj-min-candidate-review", "candidate.ready", "review.approved")]
FP = "stall:candidate.ready->cj-min-candidate-review:F1"


def _ev(t: str, **p) -> ZfEvent:
    return ZfEvent(type=t, payload=p)


def _stalled() -> list[ZfEvent]:
    # candidate.ready fired, then >=5 kernel events with no fanout.started for the stage
    return [
        _ev("candidate.ready", feature_id="F1", candidate_ref="cand/F1", fanout_id="impl-1"),
        *[_ev("orchestrator.decision.recorded") for _ in range(6)],
    ]


def _finding() -> StallFinding:
    return StallFinding(
        trigger="candidate.ready",
        stage_id="cj-min-candidate-review",
        success_event="review.approved",
        feature_id="F1",
        fingerprint=FP,
    )


def test_redispatch_reemits_trigger_with_same_payload_under_cap():
    evs = _stalled()
    finding = detect_structural_stalls(evs, stages=STAGES)[0]
    re = stall_redispatch_event(finding, evs, cap=3)
    assert re is not None
    assert re.type == "candidate.ready"  # re-emits the trigger → bypasses _fanout_started
    assert re.payload["candidate_ref"] == "cand/F1"  # carries the original payload
    assert re.payload["fanout_id"] == "impl-1"
    assert re.payload["redispatch_fingerprint"] == FP
    assert re.payload["redispatch_attempt"] == 1


def test_redispatch_returns_none_at_cap():
    finding = _finding()
    evs = [_ev("candidate.ready", candidate_ref="x")]
    evs += [_ev("candidate.ready", redispatch_fingerprint=FP) for _ in range(3)]
    assert stall_redispatch_event(finding, evs, cap=3) is None  # cap → escalate


def test_redispatch_attempt_increments():
    finding = _finding()
    evs = [_ev("candidate.ready", candidate_ref="x"),
           _ev("candidate.ready", redispatch_fingerprint=FP)]
    re = stall_redispatch_event(finding, evs, cap=3)
    assert re is not None and re.payload["redispatch_attempt"] == 2


def test_emit_stall_recoveries_redispatches_first(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    writer = EventWriter(log)
    n = emit_stall_recoveries(_stalled(), writer, stages=STAGES, redispatch_cap=3)
    assert n == 1
    types = [e.type for e in log.read_all()]
    assert "candidate.ready" in types  # re-dispatched (retry the stalled stage)
    assert "autoresearch.invocation.requested" not in types  # not escalated yet


def test_emit_stall_recoveries_escalates_at_cap(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    writer = EventWriter(log)
    evs = _stalled() + [_ev("candidate.ready", feature_id="F1", redispatch_fingerprint=FP)
                        for _ in range(3)]
    # latest trigger is a re-dispatch; push it back from the tail so it's still detectable
    evs += [_ev("orchestrator.decision.recorded") for _ in range(6)]
    n = emit_stall_recoveries(evs, writer, stages=STAGES, redispatch_cap=3)
    assert n == 1
    types = [e.type for e in log.read_all()]
    assert "autoresearch.invocation.requested" in types  # cap reached → escalate


# --- B-FIX-06 (R32 双派发): trigger 已起 active fanout 则抑制重发 ---

def test_redispatch_suppressed_when_trigger_already_has_active_fanout():
    trigger = _ev("candidate.ready", feature_id="F1", candidate_ref="cand/F1", fanout_id="impl-1")
    events = [
        trigger,
        # 该 trigger 已起 fanout(只是慢,未 terminal)
        _ev("fanout.started", fanout_id="fo-1", trigger_event_id=trigger.id),
        *[_ev("orchestrator.decision.recorded") for _ in range(6)],
    ]
    # 重发会双派发同一组 task → 必须抑制
    assert stall_redispatch_event(_finding(), events) is None


def test_redispatch_fires_when_trigger_fanout_already_terminal():
    trigger = _ev("candidate.ready", feature_id="F1", candidate_ref="cand/F1", fanout_id="impl-1")
    events = [
        trigger,
        _ev("fanout.started", fanout_id="fo-1", trigger_event_id=trigger.id),
        _ev("fanout.cancelled", fanout_id="fo-1"),  # fanout 已 terminal → 真停滞
        *[_ev("orchestrator.decision.recorded") for _ in range(6)],
    ]
    # fanout 已结束、stage 仍停滞 → 照常重发恢复
    assert stall_redispatch_event(_finding(), events) is not None


def test_redispatch_fires_when_trigger_started_no_fanout():
    # 原始恢复场景:trigger 触发但 NO fanout 起 → 照常重发(不被新 dedup 误伤)
    assert stall_redispatch_event(_finding(), _stalled()) is not None
