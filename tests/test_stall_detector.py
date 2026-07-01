"""backlog 1329: STRUCTURAL stage-progression stall detector.

Replaces the time-threshold detector — a stall is "trigger fired but the kernel
never started/cancelled/succeeded the stage", derived from the kernel's own
dispatch records, not a wall-clock guess.
"""

from __future__ import annotations

from types import SimpleNamespace

from zf.runtime.stall_detector import (
    detect_structural_stalls,
    emit_stall_invocations,
)

# (stage_id, trigger, success_event)
STAGES = [
    ("cj-min-refactor-scan", "refactor.scan.requested", "refactor.plan.ready"),
    ("cj-min-slice-implementation", "task_map.ready", "candidate.ready"),
    ("cj-min-candidate-verification", "candidate.ready", "test.passed"),
    ("cj-min-final-judge", "test.passed", "judge.passed"),
]


def _ev(etype, payload=None):
    return SimpleNamespace(type=etype, payload=payload or {"feature_id": "CJMIN-R11"})


def _pad(n):
    # filler events to clear the min_events_after grace
    return [_ev("orchestrator.decision.recorded") for _ in range(n)]


def test_no_stall_when_stage_started_even_if_slow():
    # cj-min R11 phantom-stall regression: scan.requested fired, scan fanout
    # STARTED (fanout.started) and is just slow internally (plan.ready not yet).
    # A started-but-slow stage is NOT a structural stall — no false positive.
    events = [
        _ev("refactor.scan.requested"),
        _ev("fanout.started", {"stage_id": "cj-min-refactor-scan"}),
        *_pad(20),
    ]
    assert detect_structural_stalls(events, stages=STAGES) == []


def test_stall_when_stage_never_started():
    # The real R10 blocker: candidate.ready fired but the verify fanout NEVER
    # started (no fanout.started for cj-min-candidate-verification) — the kernel
    # silently skipped the dispatch. That IS a structural stall.
    events = [
        _ev("task_map.ready"),
        _ev("fanout.started", {"stage_id": "cj-min-slice-implementation"}),
        _ev("candidate.ready"),
        *_pad(20),  # kernel kept ticking but never started verify
    ]
    findings = detect_structural_stalls(events, stages=STAGES)
    assert len(findings) == 1
    assert findings[0].trigger == "candidate.ready"
    assert findings[0].stage_id == "cj-min-candidate-verification"


def test_no_stall_when_stage_cancelled():
    events = [
        _ev("candidate.ready"),
        _ev("fanout.cancelled", {"stage_id": "cj-min-candidate-verification"}),
        *_pad(20),
    ]
    assert detect_structural_stalls(events, stages=STAGES) == []


def test_no_stall_when_pipeline_completes():
    # full chain fires (each trigger's stage succeeded) — nothing stalled.
    events = [
        _ev("candidate.ready"), _ev("test.passed"), _ev("judge.passed"), *_pad(20),
    ]
    assert detect_structural_stalls(events, stages=STAGES) == []


def test_only_the_unstarted_stage_is_flagged():
    # verify succeeded (test.passed) so verify is NOT flagged; judge never
    # started, so only judge is the structural stall.
    events = [_ev("candidate.ready"), _ev("test.passed"), *_pad(20)]
    findings = detect_structural_stalls(events, stages=STAGES)
    flagged = {f.stage_id for f in findings}
    assert "cj-min-candidate-verification" not in flagged  # it succeeded
    assert flagged == {"cj-min-final-judge"}  # judge never started


def test_no_stall_within_grace():
    # trigger just fired; kernel hasn't had its turn — don't fire yet.
    events = [_ev("candidate.ready"), _ev("orchestrator.decision.recorded")]
    assert detect_structural_stalls(events, stages=STAGES, min_events_after=5) == []


def test_emit_requests_invocation_for_structural_stall():
    events = [_ev("candidate.ready"), *_pad(20)]
    captured = []
    writer = SimpleNamespace(append=lambda e: captured.append(e) or e)
    n = emit_stall_invocations(events, writer, stages=STAGES)
    assert n == 1
    assert captured[0].type == "autoresearch.invocation.requested"
    assert captured[0].payload.get("level") == "diagnose"
    assert "candidate.ready" in captured[0].payload.get("trigger_reason", "")
