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
    stages_from_config,
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
    assert detect_structural_stalls(events, stages=STAGES[:3]) == []


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
    assert detect_structural_stalls(events, stages=STAGES[:3]) == []


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


def test_emit_reports_structural_stall_to_run_manager_owner():
    events = [_ev("candidate.ready"), *_pad(20)]
    captured = []
    writer = SimpleNamespace(append=lambda e: captured.append(e) or e)
    n = emit_stall_invocations(events, writer, stages=STAGES)
    assert n == 1
    assert captured[0].type == "dispatch.silent_stall"
    assert captured[0].actor == "zf-stall-detector"
    assert captured[0].payload["original_trigger_event_id"].startswith("index-")
    assert "candidate.ready" in captured[0].payload["summary"]


def test_verify_success_closes_original_trigger_despite_redispatch_tail():
    events = [
        _ev("candidate.ready"),
        _ev("test.passed"),
        _ev(
            "candidate.ready",
            {
                "feature_id": "CJMIN-R11",
                "redispatch_fingerprint": "legacy-stall",
                "original_trigger_event_id": "legacy",
            },
        ),
        *_pad(20),
    ]
    assert detect_structural_stalls(events, stages=STAGES[:3]) == []


def test_lane_handoff_terminal_is_not_reused_as_its_own_verify_trigger():
    """R15: a final lane handoff must not renew the verify stage's stall clock."""
    stages = [("prd-lanes-verify", "lane.stage.completed", "lane.stage.completed")]
    events = [
        _ev("lane.stage.completed", {
            "stage_id": "prd-lanes-impl",
            "stage_slot": "impl",
            "next_stage_slot": "verify",
            "workflow_run_id": "run-1",
            "task_id": "TASK-1",
        }),
        _ev("fanout.started", {
            "stage_id": "prd-lanes-verify",
            "workflow_run_id": "run-1",
        }),
        _ev("lane.stage.completed", {
            "stage_id": "prd-lanes-verify",
            "stage_slot": "verify",
            "next_stage_slot": "",
            "workflow_run_id": "run-1",
            "task_id": "TASK-1",
        }),
        *_pad(8),
    ]

    assert detect_structural_stalls(events, stages=stages) == []


def test_lane_handoff_only_targets_its_pipeline_in_multi_kind_config():
    stages = [
        ("issue-lanes-verify", "lane.stage.completed", "lane.stage.completed", "issue"),
        ("prd-lanes-verify", "lane.stage.completed", "lane.stage.completed", "prd"),
        (
            "refactor-lanes-verify",
            "lane.stage.completed",
            "lane.stage.completed",
            "refactor",
        ),
    ]
    trigger = _ev("lane.stage.completed", {
        "pipeline_id": "issue-lanes",
        "stage_id": "issue-lanes-impl",
        "stage_slot": "impl",
        "next_stage_slot": "verify",
        "workflow_run_id": "run-1",
        "task_id": "TASK-1",
    })
    findings = detect_structural_stalls([trigger, *_pad(8)], stages=stages)

    assert [finding.stage_id for finding in findings] == ["issue-lanes-verify"]
    assert findings[0].flow_kind == "issue"


def test_stages_from_config_keeps_flow_kind_for_shared_triggers():
    stages = [
        SimpleNamespace(
            id="issue-lanes-verify",
            trigger="lane.stage.completed",
            flow_kind="issue",
            aggregate=SimpleNamespace(success_event="lane.stage.completed"),
        ),
    ]
    config = SimpleNamespace(workflow=SimpleNamespace(stages=stages))

    assert stages_from_config(config) == [
        (
            "issue-lanes-verify",
            "lane.stage.completed",
            "lane.stage.completed",
            "issue",
        ),
    ]
