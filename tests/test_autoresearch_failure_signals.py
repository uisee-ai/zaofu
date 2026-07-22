from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.autoresearch.failure_signals import (
    collect_failure_signals,
    completed_run_quiesced,
    detect_semantic_flow_failures,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent


def _old_ts(minutes: int = 10) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def test_collect_failure_signals_flags_readonly_gate_mutation(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="test.passed",
        actor="test",
        task_id="TASK-1",
        payload={"changed_files": ["proof.txt"]},
    ))

    signals = collect_failure_signals(state_dir)

    assert len(signals) == 1
    signal = signals[0]
    assert signal.category == "evaluator_drift"
    assert signal.severity == "high"
    assert signal.event_ids
    assert "proof.txt" in signal.actual


def test_ship_terminal_quiesces_only_until_same_run_reopens() -> None:
    events = [
        ZfEvent(
            type="run.goal.started",
            correlation_id="run-a",
            payload={"run_id": "run-a"},
        ),
        ZfEvent(
            type="ship.completed",
            correlation_id="run-a",
            payload={"run_id": "run-a"},
        ),
    ]
    assert completed_run_quiesced(events) is True
    events.append(ZfEvent(
        type="verify.failed",
        correlation_id="run-a",
        payload={"run_id": "run-a"},
    ))
    assert completed_run_quiesced(events) is False


def test_plan_admission_failure_does_not_become_semantic_source_repair_signal(
    tmp_path: Path,
) -> None:
    event = ZfEvent(
        type="prd.plan.failed",
        correlation_id="run-plan",
        payload={
            "workflow_run_id": "run-plan",
            "failure_scope": "plan_admission",
            "plan_admission_incident_id": "plan-admission-1",
            "expected_fault": True,
            "reason": "task map intentionally omitted a required ref",
        },
    )

    assert detect_semantic_flow_failures([event], state_dir=tmp_path / ".zf") == []


def test_fanout_pending_without_lifecycle_becomes_signal(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    base = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
    log.append(ZfEvent(
        id="evt-dispatch",
        type="fanout.child.dispatched",
        actor="zf-cli",
        ts=base.isoformat(),
        payload={
            "fanout_id": "fanout-impl-1",
            "child_id": "dev-lane-0-TASK-X",
            "role_instance": "dev-lane-0",
            "stage_id": "impl",
        },
    ))
    log.append(ZfEvent(
        id="evt-later",
        type="orchestrator.round.complete",
        actor="zf-cli",
        ts=(base + timedelta(minutes=15)).isoformat(),
        payload={},
    ))

    signals = collect_failure_signals(state_dir)

    assert any(signal.fingerprint == "fanout_child_pending:fanout-impl-1:dev-lane-0-TASK-X" for signal in signals)


def test_fanout_pending_with_pane_lifecycle_recovery_is_suppressed(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    base = datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc)
    log.append(ZfEvent(
        id="evt-dispatch",
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id="TASK-X",
        ts=base.isoformat(),
        payload={
            "fanout_id": "fanout-impl-1",
            "child_id": "dev-lane-0-TASK-X",
            "role_instance": "dev-lane-0",
            "stage_id": "impl",
        },
    ))
    log.append(ZfEvent(
        id="evt-pane-dead",
        type="worker.pane.dead_observed",
        actor="dev-lane-0",
        task_id="TASK-X",
        ts=(base + timedelta(minutes=3)).isoformat(),
        payload={
            "instance_id": "dev-lane-0",
            "role": "dev-lane-0",
            "source": "dead_watchdog",
        },
    ))
    log.append(ZfEvent(
        id="evt-later",
        type="orchestrator.round.complete",
        actor="zf-cli",
        ts=(base + timedelta(minutes=5)).isoformat(),
        payload={},
    ))

    signals = collect_failure_signals(state_dir)

    assert not any(
        signal.fingerprint == "fanout_child_pending:fanout-impl-1:dev-lane-0-TASK-X"
        for signal in signals
    )


def test_collect_failure_signals_flags_terminal_without_gate(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="task.status_changed",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"to": "done"},
    ))

    signals = collect_failure_signals(state_dir)

    assert [signal.category for signal in signals] == ["self_declared_completion"]


def test_collect_failure_signals_reads_web_bind_log(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    log_path = tmp_path / "web.log"
    log_path.write_text("listening on http://127.0.0.1:8003\n", encoding="utf-8")

    signals = collect_failure_signals(state_dir, web_log_paths=[log_path])

    assert signals[0].category == "operator_access_bug"
    assert signals[0].evidence_paths == [str(log_path)]


def test_collect_failure_signals_flags_fanout_timeout(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        payload={"fanout_id": "F1", "child_id": "review-a"},
    ))
    log.append(ZfEvent(
        type="fanout.timed_out",
        actor="zf-cli",
        payload={"fanout_id": "F1", "pending_children": ["review-a"]},
    ))

    signals = collect_failure_signals(state_dir)

    assert signals[0].category == "fanout_runtime_failure"
    assert signals[0].fingerprint == "fanout_timed_out:F1:review-a"


def test_collect_failure_signals_flags_stale_task_map_child_failure(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="f1",
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "F1",
            "child_id": "dev-lane-0-TASK-1",
            "pdd_id": "PDD-1",
            "reason": "stale_task_map",
            "stale_task_ids": ["TASK-1"],
            "suggested_action": "use_latest_product_delivery_wave_ready",
        },
    ))

    signals = collect_failure_signals(state_dir)

    assert signals[0].category == "fanout_runtime_failure"
    assert signals[0].fingerprint == (
        "stale_task_map_writer_fanout:PDD-1:TASK-1"
    )


def test_collect_failure_signals_skips_recovered_stale_task_map_child_failure(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        id="f1",
        type="fanout.child.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "F1",
            "child_id": "dev-lane-0-TASK-1",
            "pdd_id": "PDD-1",
            "reason": "stale_task_map",
            "stale_task_ids": ["TASK-1"],
        },
    ))
    log.append(ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "PDD-1",
            "rework_of": "f1",
            "rework_source": "fanout.child.failed",
        },
    ))

    signals = collect_failure_signals(state_dir)

    assert not any(
        signal.fingerprint.startswith("stale_task_map_writer_fanout:")
        for signal in signals
    )


def test_collect_failure_signals_flags_child_emitted_aggregate_event(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        payload={
            "fanout_id": "F1",
            "aggregate": {
                "success_event": "review.approved",
                "failure_event": "review.rejected",
                "child_success_event": "workflow.child.completed",
                "child_failure_event": "workflow.child.failed",
            },
        },
    ))
    log.append(ZfEvent(
        type="review.approved",
        actor="review-a",
        payload={
            "fanout_id": "F1",
            "child_id": "review-a",
            "status": "approved",
        },
    ))

    signals = collect_failure_signals(state_dir)

    assert signals[0].category == "fanout_event_contract"
    assert signals[0].fingerprint == (
        "fanout_child_emitted_aggregate:F1:review-a:review.approved"
    )


def test_collect_failure_signals_allows_kernel_aggregate_with_child_identity(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        origin="kernel",
        payload={
            "fanout_id": "F1",
            "aggregate": {
                "success_event": "lane.stage.completed",
                "failure_event": "lane.stage.failed",
                "child_success_event": "impl.child.completed",
                "child_failure_event": "impl.child.failed",
            },
        },
    ))
    log.append(ZfEvent(
        type="lane.stage.completed",
        actor="zf-cli",
        origin="kernel",
        payload={
            "fanout_id": "F1",
            "child_id": "dev-lane-0-T1",
            "status": "completed",
        },
    ))

    signals = collect_failure_signals(state_dir)

    assert not any(
        signal.fingerprint.startswith("fanout_child_emitted_aggregate:")
        for signal in signals
    )


def test_handoff_stall_skipped_in_candidate_integration_flow(tmp_path: Path) -> None:
    """R18: a writer's per-task static_gate.passed hands off to review via the
    candidate aggregate (all slices integrate → candidate.ready → review reviews
    the CANDIDATE, never per-task). In a candidate-integration flow (task_map.ready
    present), the per-task static_gate→review handoff expectation is inapplicable
    and must not fire (R18: 5× false handoff_stall during the integration window).
    """
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="task_map.ready", actor="zf-cli",
                       payload={"pdd_id": "CJMIN"}))
    log.append(ZfEvent(type="static_gate.passed", actor="zf-cli", task_id="SLICE-1"))
    # no per-task task.dispatched-to-review / review terminal follows
    signals = collect_failure_signals(state_dir)
    assert not any(s.category == "handoff_stall" for s in signals)


def test_handoff_stall_still_fires_in_direct_non_candidate_flow(tmp_path: Path) -> None:
    """Guard: without a candidate-integration flow, a static_gate.passed with no
    review handoff is a real per-task handoff_stall — the detector still works."""
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="static_gate.passed",
        actor="zf-cli",
        task_id="T1",
        ts=_old_ts(),
    ))
    signals = collect_failure_signals(state_dir)
    assert any(s.category == "handoff_stall" for s in signals)


def test_handoff_stall_skips_fresh_static_gate_window(tmp_path: Path) -> None:
    """A just-emitted gate success is still inside the normal handoff window."""

    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="static_gate.passed", actor="zf-cli", task_id="T1"))

    signals = collect_failure_signals(state_dir)

    assert not any(s.category == "handoff_stall" for s in signals)


def test_handoff_stall_skips_when_later_stage_progress_exists(
    tmp_path: Path,
) -> None:
    """If the task has already reached test/judge, the handoff was not stalled."""

    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="static_gate.passed",
        actor="zf-cli",
        task_id="T1",
        ts=_old_ts(),
    ))
    log.append(ZfEvent(
        type="test.passed",
        actor="test",
        task_id="T1",
        payload={"tests_run": ["uv run pytest tests/test_example.py"]},
    ))

    signals = collect_failure_signals(state_dir)

    assert not any(s.category == "handoff_stall" for s in signals)
