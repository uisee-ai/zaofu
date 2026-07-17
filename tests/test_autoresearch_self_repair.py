from __future__ import annotations

from pathlib import Path

from zf.autoresearch.self_repair import (
    candidate_from_trigger_event,
    candidate_with_diagnosis,
    repair_task_payload_from_candidate,
    validate_repair_metric_delta,
    write_candidate_artifact,
)
from zf.autoresearch.triggers import TriggerPolicy, scan_trigger_decisions
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.maintenance import enter_maintenance


def test_scan_trigger_decisions_skips_when_maintenance_active(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    log = EventLog(state_dir / "events.jsonl")
    enter_maintenance(
        state_dir,
        trigger_id="ar-active",
        reason="repair in progress",
        emit_events=False,
    )
    log.append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="zf-cli",
        payload={"reason": "tmux dead"},
    ))

    decisions = scan_trigger_decisions(
        state_dir,
        policy=TriggerPolicy(max_triggers_per_hour=10, max_daily_runs=10),
    )

    assert decisions
    assert all(decision.decision == "skipped" for decision in decisions)
    assert {decision.skip_reason for decision in decisions} == {"self_repair_active"}


def test_trigger_event_builds_unverified_candidate_before_repair_payload(
    tmp_path: Path,
) -> None:
    event = ZfEvent(
        type="autoresearch.trigger.accepted",
        id="evt-trigger",
        actor="zf-autoresearch",
        payload={
            "trigger_id": "ar-123",
            "severity": "critical",
            "reason": "dispatch loop detected",
            "fingerprint": "bug:dispatch-loop",
            "signal_ids": ["sig-1"],
            "evidence_paths": ["records/ar-123.md"],
            "metric_impacts": {"failure_count": 3},
        },
    )

    candidate = candidate_from_trigger_event(event)
    path = write_candidate_artifact(tmp_path / ".zf", candidate)

    assert path.exists()
    assert candidate.trigger_id == "ar-123"
    assert candidate.status == "unverified"
    assert repair_task_payload_from_candidate(candidate, candidate_path=path) is None

    confirmed = candidate_with_diagnosis(
        candidate,
        status="confirmed",
        diagnosis_evidence_paths=["records/ar-123-diagnosis.md"],
        repair_scope=["src/zf/runtime/dispatch.py", "tests/test_dispatch.py"],
        resolution_reason="reproduced from deterministic fixture",
    )
    payload = repair_task_payload_from_candidate(
        confirmed,
        candidate_path=write_candidate_artifact(tmp_path / ".zf", confirmed),
    )

    assert payload is not None
    assert payload["task_id"].startswith("TASK-AR-")
    assert payload["contract"]["phase"] == "zaofu_self_repair"
    evidence = payload["contract"]["evidence_contract"]
    assert evidence["candidate_id"] == candidate.candidate_id
    assert evidence["source_signals"] == ["sig-1"]
    assert evidence["target_metrics"] == ["failure_count"]
    assert payload["contract"]["validation"]["requires_baseline_candidate_metrics"] is True
    assert payload["contract"]["scope"] == [
        "src/zf/runtime/dispatch.py",
        "tests/test_dispatch.py",
    ]


def test_repair_metric_delta_requires_baseline_and_candidate_metrics() -> None:
    missing = validate_repair_metric_delta({}, {"failure_count": 0})
    passed = validate_repair_metric_delta(
        {"failure_count": 3},
        {"failure_count": 0},
        required_metrics=["failure_count"],
    )

    assert missing.passed is False
    assert "baseline_metrics are required" in missing.errors
    assert passed.passed is True
    assert passed.deltas["failure_count"] == -3
