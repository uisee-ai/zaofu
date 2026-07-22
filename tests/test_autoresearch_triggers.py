from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zf.autoresearch.failure_signals import (
    FailureSignal,
    detect_fanout_failures,
    detect_semantic_flow_failures,
)
from zf.autoresearch.triggers import (
    TriggerPolicy,
    decide_trigger_for_signal,
    scan_trigger_decisions,
    trigger_policy_from_config,
    write_trigger_decision,
)
from zf.cli.start import _run_autoresearch_trigger_scan
from zf.core.config.schema import (
    AutoresearchConfig,
    AutoresearchTriggerPolicyConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


def _signal() -> FailureSignal:
    return FailureSignal(
        signal_id="sig-a",
        source_kind="event_log",
        source_path=".zf/events.jsonl",
        fingerprint="fatal:dispatch",
        category="runtime_fatal",
        severity="high",
        summary="dispatch failed",
        evidence_paths=[".zf/events.jsonl"],
    )


def test_trigger_policy_accepts_high_severity_signal(tmp_path: Path) -> None:
    decision = decide_trigger_for_signal(
        _signal(),
        state_dir=tmp_path / ".zf",
        policy=TriggerPolicy(),
        now=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )

    assert decision.decision == "accepted"
    assert decision.fingerprint == "fatal:dispatch"
    assert decision.failure_class == "runtime_fatal"


def test_trigger_policy_accepts_eligible_low_severity_signal(tmp_path: Path) -> None:
    signal = FailureSignal(
        signal_id="sig-low",
        source_kind="event_log",
        source_path=".zf/events.jsonl",
        fingerprint="worker:stuck",
        category="worker_stuck",
        severity="low",
        summary="worker heartbeat missing",
    )

    decision = decide_trigger_for_signal(
        signal,
        state_dir=tmp_path / ".zf",
        policy=TriggerPolicy(),
        now=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )

    assert decision.decision == "accepted"
    assert decision.failure_class == "worker_stuck"


def test_trigger_policy_skips_ineligible_failure_class_even_when_high(
    tmp_path: Path,
) -> None:
    signal = FailureSignal(
        signal_id="sig-cosmetic",
        source_kind="event_log",
        source_path=".zf/events.jsonl",
        fingerprint="cosmetic:noise",
        category="cosmetic_noise",
        severity="critical",
        summary="non-actionable cosmetic issue",
    )

    decision = decide_trigger_for_signal(
        signal,
        state_dir=tmp_path / ".zf",
        policy=TriggerPolicy(eligible_failure_classes=("worker_stuck",)),
        now=datetime(2026, 5, 22, tzinfo=timezone.utc),
    )

    assert decision.decision == "skipped"
    assert decision.skip_reason == "failure_class_not_eligible"
    assert decision.failure_class == "cosmetic_noise"


def test_trigger_policy_cooldown_skips_duplicate(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    first = decide_trigger_for_signal(
        _signal(),
        state_dir=state_dir,
        policy=TriggerPolicy(),
        now=datetime(2026, 5, 22, 7, 0, tzinfo=timezone.utc),
    )

    second = decide_trigger_for_signal(
        _signal(),
        state_dir=state_dir,
        policy=TriggerPolicy(),
        history=[first],
        now=datetime(2026, 5, 22, 7, 10, tzinfo=timezone.utc),
    )

    assert second.decision == "skipped"
    assert second.skip_reason == "cooldown"


def test_trigger_policy_cooldown_uses_problem_fingerprint(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    first_signal = FailureSignal(
        signal_id="sig-fanout-a",
        source_kind="event_log",
        source_path=".zf/events.jsonl",
        fingerprint="fanout_child_pending:fanout-a:verify-1",
        category="fanout_runtime_pending",
        severity="high",
        summary="fanout child has no terminal event",
        evidence_paths=[".zf/events.jsonl"],
    )
    second_signal = FailureSignal(
        signal_id="sig-fanout-b",
        source_kind="event_log",
        source_path=".zf/events.jsonl",
        fingerprint="fanout_child_pending:fanout-b:verify-2",
        category="fanout_runtime_pending",
        severity="high",
        summary="fanout child has no terminal event",
        evidence_paths=[".zf/events.jsonl"],
    )
    first = decide_trigger_for_signal(
        first_signal,
        state_dir=state_dir,
        policy=TriggerPolicy(max_triggers_per_hour=5000, max_daily_runs=5000),
        now=datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc),
    )

    second = decide_trigger_for_signal(
        second_signal,
        state_dir=state_dir,
        policy=TriggerPolicy(max_triggers_per_hour=5000, max_daily_runs=5000),
        history=[first],
        now=datetime(2026, 7, 7, 14, 5, tzinfo=timezone.utc),
    )

    assert first.fingerprint != second.fingerprint
    assert first.problem_fingerprint == second.problem_fingerprint
    assert second.decision == "skipped"
    assert second.skip_reason == "cooldown"
    assert second.dedupe_decision == "cooldown_duplicate"


def test_write_trigger_decision_emits_event(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    decision = decide_trigger_for_signal(
        _signal(),
        state_dir=state_dir,
        policy=TriggerPolicy(),
    )

    write_trigger_decision(state_dir, decision)

    events = EventLog(state_dir / "events.jsonl").read_all()
    assert events[-1].type == "autoresearch.trigger.accepted"


def test_autoresearch_trigger_wake_policy() -> None:
    from zf.runtime.wake_patterns import WAKE_PATTERNS

    assert "run.manager.autoresearch.requested" in WAKE_PATTERNS
    assert "autoresearch.invocation.requested" in WAKE_PATTERNS
    assert "autoresearch.trigger.accepted" in WAKE_PATTERNS
    assert "autoresearch.trigger.skipped" not in WAKE_PATTERNS


def test_trigger_policy_from_config_uses_yaml_policy_and_cli_overrides() -> None:
    cfg = ZfConfig(
        autoresearch=AutoresearchConfig(
            trigger_policy=AutoresearchTriggerPolicyConfig(
                mode="continuous",
                cooldown_minutes=15,
                max_triggers_per_hour=5000,
                max_daily_runs=5000,
            )
        )
    )

    policy = trigger_policy_from_config(cfg, cooldown_minutes=1)

    assert policy.mode == "continuous"
    assert policy.cooldown_minutes == 1
    assert policy.max_triggers_per_hour == 5000
    assert policy.max_daily_runs == 5000


def test_start_tick_autoresearch_scan_requires_continuous_mode(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        payload={"reason": "missing role"},
    ))

    accepted = _run_autoresearch_trigger_scan(state_dir, ZfConfig())

    assert accepted == 0
    assert not (state_dir / "autoresearch" / "triggers" / "decisions.jsonl").exists()


def test_start_tick_autoresearch_scan_writes_accepted_in_continuous_mode(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        payload={"reason": "missing role"},
    ))
    cfg = ZfConfig(
        autoresearch=AutoresearchConfig(
            trigger_policy=AutoresearchTriggerPolicyConfig(
                mode="continuous",
                max_triggers_per_hour=5000,
                max_daily_runs=5000,
            )
        )
    )

    accepted = _run_autoresearch_trigger_scan(state_dir, cfg)

    assert accepted == 1
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert events[-1].type == "autoresearch.trigger.accepted"


def test_scan_trigger_decisions_uses_failure_signals(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        payload={"reason": "missing role"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions
    assert decisions[0].decision == "accepted"


def test_fanout_pending_signal_skips_superseded_fanout(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    events = [
        ZfEvent(
            type="fanout.started",
            actor="zf-cli",
            payload={
                "fanout_id": "fanout-old",
                "stage_id": "verify",
                "target_ref": "candidate/F-1",
                "pdd_id": "F-1",
            },
        ),
        ZfEvent(
            type="fanout.child.dispatched",
            actor="zf-cli",
            payload={
                "fanout_id": "fanout-old",
                "child_id": "verify-1",
                "stage_id": "verify",
                "target_ref": "candidate/F-1",
                "pdd_id": "F-1",
            },
        ),
        ZfEvent(
            type="fanout.started",
            actor="zf-cli",
            payload={
                "fanout_id": "fanout-new",
                "stage_id": "verify",
                "target_ref": "candidate/F-1",
                "pdd_id": "F-1",
            },
        ),
    ]

    signals = detect_fanout_failures(events, state_dir=state_dir)

    assert not [
        signal for signal in signals
        if signal.fingerprint == "fanout_child_pending:fanout-old:verify-1"
    ]


def test_scan_trigger_decisions_skips_fanout_pending_after_judge_passed(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    for event in [
        ZfEvent(
            type="fanout.started",
            actor="zf-cli",
            payload={
                "fanout_id": "fanout-verify",
                "stage_id": "verify",
                "trace_id": "trace-r2",
                "pdd_id": "F-1",
            },
            correlation_id="trace-r2",
        ),
        ZfEvent(
            type="fanout.child.dispatched",
            actor="zf-cli",
            payload={
                "fanout_id": "fanout-verify",
                "child_id": "verify-1",
                "stage_id": "verify",
                "trace_id": "trace-r2",
                "pdd_id": "F-1",
            },
            correlation_id="trace-r2",
        ),
        ZfEvent(
            type="judge.passed",
            actor="judge",
            payload={"trace_id": "trace-r2", "pdd_id": "F-1"},
            correlation_id="trace-r2",
        ),
    ]:
        log.append(event)

    decisions = scan_trigger_decisions(state_dir)

    assert not [
        decision for decision in decisions
        if decision.fingerprint == "fanout_child_pending:fanout-verify:verify-1"
    ]


def test_detect_fanout_pending_uses_grace_window(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    base = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    events = [
        ZfEvent(
            id="started",
            type="fanout.started",
            actor="zf-cli",
            ts=base.isoformat(),
            payload={"fanout_id": "fanout-verify", "trace_id": "trace-r1"},
            correlation_id="trace-r1",
        ),
        ZfEvent(
            id="child",
            type="fanout.child.dispatched",
            actor="zf-cli",
            ts=(base + timedelta(seconds=5)).isoformat(),
            payload={"fanout_id": "fanout-verify", "child_id": "verify-1", "trace_id": "trace-r1"},
            correlation_id="trace-r1",
        ),
        ZfEvent(
            id="heartbeat",
            type="worker.heartbeat",
            actor="verify-1",
            ts=(base + timedelta(seconds=30)).isoformat(),
            payload={"trace_id": "trace-r1"},
            correlation_id="trace-r1",
        ),
    ]

    assert detect_fanout_failures(events, state_dir=state_dir) == []

    old_events = [
        *events,
        ZfEvent(
            id="later",
            type="run.manager.tick.completed",
            actor="run-manager",
            ts=(base + timedelta(seconds=660)).isoformat(),
            payload={"trace_id": "trace-r1"},
            correlation_id="trace-r1",
        ),
    ]

    assert [
        signal for signal in detect_fanout_failures(old_events, state_dir=state_dir)
        if signal.fingerprint == "fanout_child_pending:fanout-verify:verify-1"
    ]


def test_scan_trigger_decisions_quiesces_after_run_completed(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        payload={"reason": "old pane failure"},
    ))
    log.append(ZfEvent(
        type="run.completed",
        actor="run-manager",
        payload={
            "status": "passed",
            "candidate_ref": "cand/R4",
            "candidate_head_commit": "abc1234",
        },
    ))

    decisions = scan_trigger_decisions(
        state_dir,
        policy=TriggerPolicy(max_triggers_per_hour=10, max_daily_runs=10),
    )

    assert decisions == []


def test_scan_trigger_decisions_reopens_after_post_completion_regression(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="run.completed",
        actor="run-manager",
        payload={
            "status": "passed",
            "candidate_ref": "cand/R4",
            "candidate_head_commit": "abc1234",
        },
    ))
    log.append(ZfEvent(
        type="verify.failed",
        actor="verify",
        payload={"reason": "post-completion regression"},
    ))
    log.append(ZfEvent(
        type="worker.stuck",
        actor="verify-lane-0",
        payload={"worker": "verify-lane-0", "state": "busy"},
    ))

    decisions = scan_trigger_decisions(
        state_dir,
        policy=TriggerPolicy(max_triggers_per_hour=10, max_daily_runs=10),
    )

    assert any(
        decision.decision == "accepted"
        and decision.failure_class == "worker_stuck"
        for decision in decisions
    )


def test_semantic_flow_failure_triggers_autoresearch(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="flow.discovery.failed",
        id="evt-flow-discovery-failed",
        actor="verify",
        payload={"pdd_id": "PDD-1", "reason": "dashboard missing"},
    ))

    signals = detect_semantic_flow_failures(log.read_all(), state_dir=state_dir)
    decisions = scan_trigger_decisions(
        state_dir,
        policy=TriggerPolicy(max_triggers_per_hour=10, max_daily_runs=10),
    )

    assert len(signals) == 1
    assert signals[0].category == "flow_discovery_failed"
    assert any(
        decision.decision == "accepted"
        and decision.failure_class == "flow_discovery_failed"
        for decision in decisions
    )


def test_semantic_flow_failure_superseded_by_gap_plan_does_not_trigger(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="flow.goal.blocked",
        id="evt-flow-goal-blocked",
        actor="verify",
        payload={"pdd_id": "PDD-1", "reason": "dashboard missing"},
    ))
    log.append(ZfEvent(
        type="flow.gap_plan.ready",
        id="evt-flow-gap-ready",
        actor="verify",
        payload={"pdd_id": "PDD-1", "gap_plan_ref": "reports/PDD-1/gap-plan.json"},
    ))

    signals = detect_semantic_flow_failures(log.read_all(), state_dir=state_dir)

    assert signals == []


def test_scan_trigger_decisions_classifies_pane_dead_dispatch(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        payload={
            "role": "verify-lane-2",
            "assignee": "verify-lane-2",
            "dead_reason": "pane_dead",
            "current_command": "node",
            "error": (
                "refusing to send task to verify-lane-2: pane is not "
                "running an agent process (current_command=node, "
                "reason=pane_dead)"
            ),
        },
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions
    assert decisions[0].decision == "accepted"
    assert decisions[0].fingerprint.startswith(
        "pane_dead_dispatch:orchestrator.dispatch_failed:verify-lane-2:"
    )


def test_scan_trigger_decisions_ignores_recovered_dispatch_failed(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        task_id="TASK-RECOVERED",
        payload={"reason": "transient dispatch stall"},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-RECOVERED",
        payload={"role": "critic", "assignee": "critic"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


def test_scan_trigger_decisions_ignores_dispatch_failed_recovered_by_assignment(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        payload={
            "task_id": "TASK-RECOVERED",
            "reason": "kanban assign transient failure",
        },
    ))
    log.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id="TASK-RECOVERED",
        payload={"role": "arch", "assignee": "arch"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


def test_scan_trigger_decisions_ignores_dispatch_failed_recovered_by_progress(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        task_id="TASK-RECOVERED",
        payload={"reason": "dispatch stalled"},
    ))
    log.append(ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="TASK-RECOVERED",
        payload={"evidence_refs": ["git:abc1234"]},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


def test_scan_trigger_decisions_uses_worker_instance_id_for_stuck_signal(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="worker.stuck",
        actor="zf-cli",
        payload={"instance_id": "arch", "reason": "no heartbeat"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions
    assert decisions[0].decision == "accepted"
    assert decisions[0].fingerprint == "worker_stuck:arch"


def test_scan_trigger_decisions_ignores_stuck_recovered_by_later_heartbeat(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.stuck",
        actor="zf-cli",
        payload={"instance_id": "critic", "reason": "stale heartbeat"},
    ))
    log.append(ZfEvent(
        type="worker.heartbeat",
        actor="critic",
        payload={
            "instance_id": "critic",
            "current_task_id": "TASK-1",
            "state": "busy",
        },
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


@pytest.mark.parametrize("activity_type", ["agent.usage", "claude.hook.post_tool_use"])
def test_scan_trigger_decisions_ignores_stuck_recovered_by_objective_activity(
    tmp_path: Path,
    activity_type: str,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.stuck",
        actor="zf-cli",
        payload={"instance_id": "dev-lane-0", "reason": "stale heartbeat"},
    ))
    log.append(ZfEvent(
        type=activity_type,
        actor="dev-lane-0",
        payload={},
    ))

    assert scan_trigger_decisions(state_dir) == []


def test_scan_trigger_decisions_ignores_stuck_recovered_by_later_state_change(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.stuck",
        actor="zf-cli",
        payload={"instance_id": "arch", "reason": "stale heartbeat"},
    ))
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="arch",
        payload={"from": "stuck", "to": "idle"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


def test_scan_trigger_decisions_does_not_use_earlier_recovery_for_later_stuck(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.stuck.recovered",
        actor="arch",
        payload={"instance_id": "arch"},
    ))
    log.append(ZfEvent(
        type="worker.stuck",
        actor="zf-cli",
        payload={"instance_id": "arch", "reason": "new stale heartbeat"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions
    assert decisions[0].decision == "accepted"
    assert decisions[0].fingerprint == "worker_stuck:arch"


def test_scan_trigger_decisions_ignores_stuck_after_worker_became_idle(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.heartbeat",
        actor="critic",
        payload={
            "instance_id": "critic",
            "current_task_id": "TASK-1",
            "state": "busy",
        },
    ))
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="critic",
        payload={"from": "busy", "to": "idle", "reason": "gate completed"},
    ))
    log.append(ZfEvent(
        type="worker.stuck",
        actor="zf-cli",
        payload={"instance_id": "critic", "reason": "stale busy heartbeat"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


def test_scan_trigger_decisions_ignores_stuck_after_worker_awaits_review(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="dev-1",
        payload={
            "from": "busy",
            "to": "awaiting_review",
            "reason": "dev.build.done already recorded",
        },
    ))
    log.append(ZfEvent(
        type="worker.stuck",
        actor="zf-cli",
        payload={"instance_id": "dev-1", "reason": "no heartbeat"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


def test_scan_trigger_decisions_ignores_stuck_after_task_done(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.heartbeat",
        actor="dev-1",
        payload={
            "instance_id": "dev-1",
            "current_task_id": "TASK-DONE",
            "state": "busy",
        },
    ))
    log.append(ZfEvent(
        type="discriminator.passed",
        actor="zf-cli",
        task_id="TASK-DONE",
        payload={"summary": "gate passed"},
    ))
    log.append(ZfEvent(
        type="task.status_changed",
        actor="zf-cli",
        task_id="TASK-DONE",
        payload={"from": "in_progress", "to": "done"},
    ))
    log.append(ZfEvent(
        type="worker.stuck",
        actor="zf-cli",
        payload={"instance_id": "dev-1", "reason": "stale terminal task heartbeat"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


def test_scan_trigger_decisions_reports_stuck_after_worker_became_busy_again(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="worker.state.changed",
        actor="critic",
        payload={"from": "busy", "to": "idle", "reason": "gate completed"},
    ))
    log.append(ZfEvent(
        type="worker.heartbeat",
        actor="critic",
        payload={
            "instance_id": "critic",
            "current_task_id": "TASK-2",
            "state": "busy",
        },
    ))
    log.append(ZfEvent(
        type="worker.stuck",
        actor="zf-cli",
        payload={"instance_id": "critic", "reason": "new stale heartbeat"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions
    assert decisions[0].decision == "accepted"
    assert decisions[0].fingerprint == "worker_stuck:critic"


def test_scan_trigger_decisions_detects_contract_preflight_blocker(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.contract.invalid",
        actor="zf-cli",
        task_id="TASK-BLOCKED",
        payload={
            "source": "dispatch_preflight",
            "role": "critic",
            "errors": [
                "TASK-BLOCKED: contract.behavior is required",
                "TASK-BLOCKED: contract.verification is required",
            ],
        },
    ))
    log.append(ZfEvent(
        type="orchestrator.dispatch_skipped",
        actor="zf-cli",
        task_id="TASK-BLOCKED",
        payload={"reason": "strict_contract_preflight_failed"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions
    assert decisions[0].decision == "accepted"
    assert decisions[0].fingerprint.startswith(
        "dispatch_preflight_blocker:TASK-BLOCKED:critic:"
    )


def test_scan_trigger_decisions_detects_static_gate_skipped_handoff_stall(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="static_gate.skipped",
        actor="zf-cli",
        task_id="TASK-GATE-STALL",
        # Backdate past _HANDOFF_STALL_GRACE (4be5d4f suppresses stall
        # signals for events fresher than 3 minutes).
        ts=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        payload={
            "passed": True,
            "skipped": True,
            "skip_reason": "quality_gates.static.enabled=False",
        },
    ))
    log.append(ZfEvent(
        type="orchestrator.idle",
        actor="orchestrator",
        task_id="TASK-GATE-STALL",
        payload={"reason": "kernel auto-routes review"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions
    assert decisions[0].decision == "accepted"
    assert (
        decisions[0].fingerprint
        == "handoff_stall:TASK-GATE-STALL:static_gate.skipped:review"
    )


def test_scan_trigger_decisions_ignores_static_gate_skipped_after_review_assign(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="static_gate.skipped",
        actor="zf-cli",
        task_id="TASK-GATE-OK",
        payload={"passed": True, "skipped": True},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-GATE-OK",
        payload={"role": "review", "assignee": "review"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


def test_scan_trigger_decisions_ignores_static_gate_passed_after_qa_assign(
    tmp_path: Path,
) -> None:
    """Mini profiles can route static_gate.passed to qa instead of review."""
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="static_gate.passed",
        actor="zf-cli",
        task_id="TASK-GATE-QA",
        payload={"passed": True, "skipped": False},
    ))
    log.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id="TASK-GATE-QA",
        payload={"role": "qa", "assignee": "qa"},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-GATE-QA",
        payload={"role": "qa", "assignee": "qa"},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


def test_scan_trigger_decisions_ignores_non_passed_static_gate_skipped(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="static_gate.skipped",
        actor="zf-cli",
        task_id="TASK-GATE-BAD",
        payload={"passed": False, "skipped": True},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


def test_scan_trigger_decisions_detects_contract_verifier_missing_tool(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="discriminator.failed",
        actor="zf-cli",
        task_id="TASK-RG",
        payload={
            "failed_d": ["ContractD"],
            "details": [
                {
                    "d": "ContractD",
                    "passed": False,
                    "reason": "verification command failed (rc=127)",
                    "evidence": {
                        "verification_returncode": 127,
                        "verification_shell_command": (
                            "zf spec validate docs/plans/x.md && "
                            "rg reference_signals docs/plans/x.md"
                        ),
                        "verification_stderr_tail": (
                            "/bin/sh: 1: rg: not found\n"
                        ),
                    },
                },
            ],
        },
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions
    assert decisions[0].decision == "accepted"
    assert (
        decisions[0].fingerprint
        == "contract_verifier_missing_tool:TASK-RG:rg"
    )


def test_scan_trigger_decisions_ignores_recovered_contract_verifier_missing_tool(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="discriminator.failed",
        actor="zf-cli",
        task_id="TASK-RG",
        payload={
            "failed_d": ["ContractD"],
            "details": [
                {
                    "d": "ContractD",
                    "passed": False,
                    "reason": "verification command failed (rc=127)",
                    "evidence": {
                        "verification_returncode": 127,
                        "verification_shell_command": "rg needle proof.txt",
                        "verification_stderr_tail": "sh: rg: command not found",
                    },
                },
            ],
        },
    ))
    log.append(ZfEvent(
        type="discriminator.passed",
        actor="zf-cli",
        task_id="TASK-RG",
        payload={},
    ))

    decisions = scan_trigger_decisions(state_dir)

    assert decisions == []


def test_scan_trigger_decisions_applies_budget_within_current_batch(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    for reason in ("missing role", "tmux dead", "workdir dirty"):
        log.append(ZfEvent(
            type="orchestrator.dispatch_failed",
            actor="zf-cli",
            payload={"reason": reason},
        ))

    decisions = scan_trigger_decisions(
        state_dir,
        policy=TriggerPolicy(max_triggers_per_hour=2, max_daily_runs=10),
    )

    assert [decision.decision for decision in decisions].count("accepted") == 2
    assert decisions[-1].decision == "skipped"
    assert decisions[-1].skip_reason == "hourly_budget"


def _ago(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat()


def test_fanout_pending_skips_child_with_recent_worker_activity(
    tmp_path: Path,
) -> None:
    """2026-07-08 live 三轮实锚:verify/judge 健康跑 3-6 分钟,纯按派发时长
    判停滞每轮必产假候选(→ proposal → escalate 噪音)。宽限改从该 worker
    最后一次可见活动起算——活跃即非停滞。"""
    state_dir = tmp_path / ".zf"
    events = [
        ZfEvent(type="fanout.started", actor="zf-cli", ts=_ago(400),
                payload={"fanout_id": "fanout-v", "stage_id": "verify"}),
        ZfEvent(type="fanout.child.dispatched", actor="zf-cli", ts=_ago(390),
                payload={"fanout_id": "fanout-v", "child_id": "verify-1",
                         "stage_id": "verify",
                         "role_instance": "verify-lane-0"}),
        # worker 持续活跃(agent.usage / codex hook 事件都以它为 actor)
        ZfEvent(type="agent.usage", actor="verify-lane-0", ts=_ago(15),
                payload={}),
        ZfEvent(type="run.manager.tick.completed", actor="zf-cli", ts=_ago(1),
                payload={}),
    ]

    signals = detect_fanout_failures(events, state_dir=state_dir)

    assert not [
        signal for signal in signals
        if signal.fingerprint == "fanout_child_pending:fanout-v:verify-1"
    ]


def test_fanout_pending_fires_when_worker_goes_quiet(tmp_path: Path) -> None:
    """反向护栏:worker 静默超宽限(而非仅派发久)才是真停滞,候选照产。"""
    state_dir = tmp_path / ".zf"
    events = [
        ZfEvent(type="fanout.started", actor="zf-cli", ts=_ago(900),
                payload={"fanout_id": "fanout-v", "stage_id": "verify"}),
        ZfEvent(type="fanout.child.dispatched", actor="zf-cli", ts=_ago(890),
                payload={"fanout_id": "fanout-v", "child_id": "verify-1",
                         "stage_id": "verify",
                         "role_instance": "verify-lane-0"}),
        ZfEvent(type="agent.usage", actor="verify-lane-0", ts=_ago(700),
                payload={}),
        ZfEvent(type="run.manager.tick.completed", actor="zf-cli", ts=_ago(1),
                payload={}),
    ]

    signals = detect_fanout_failures(events, state_dir=state_dir)

    assert [
        signal for signal in signals
        if signal.fingerprint == "fanout_child_pending:fanout-v:verify-1"
    ]
