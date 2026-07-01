from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.cli.main import main
from zf.core.config.loader import ConfigError, load_config
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.long_horizon import (
    WorkUnitContract,
    apply_completion_audit,
    audit_completion,
    build_integration_item,
    build_resume_packet,
    check_split_quality,
    effective_profile_for_task,
    decision_trace_for_task,
    guard_retry_token,
    map_goal_to_work_units,
    task_complexity,
    project_retry_metadata,
    project_skill_set,
    project_stall_status,
    project_workpad,
    project_why_not_done,
    write_resume_packet,
)


def _state(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    return state_dir


def _writer(state_dir: Path) -> EventWriter:
    return EventWriter(EventLog(state_dir / "events.jsonl"))


def _add_task(
    state_dir: Path,
    *,
    task_id: str = "TASK-1",
    feature_id: str = "FEAT-1",
    status: str = "in_progress",
    required_events: list[str] | None = None,
    required_commands: list[str] | None = None,
    affected_files: list[str] | None = None,
) -> Task:
    task = Task(
        id=task_id,
        title="Implement queue recovery",
        status=status,
        assigned_to="dev-1",
        contract=TaskContract(
            feature_id=feature_id,
            behavior="Queue recovery works after worker crash",
            owner_role="dev",
            owner_instance="dev-1",
            affected_files=affected_files or ["src/runtime/queue.py"],
            acceptance_criteria=["crashed worker task can be reclaimed"],
            evidence_contract={
                "required_events": required_events or [],
                "required_commands": required_commands or [],
            },
        ),
    )
    TaskStore(state_dir / "kanban.json").add(task)
    return task


def test_split_quality_warning_mode_does_not_fail_closed_for_design_intake() -> None:
    work_unit = WorkUnitContract(
        id="WU-TASK-1",
        task_id="TASK-1",
        feature_id="FEAT-1",
        title="Design CLI JSON output",
        goal="Design CLI JSON output",
        outcome="A reviewed design proposal",
    )

    findings = check_split_quality(
        work_unit,
        mode="warning",
        require_validation_surface=True,
    )

    by_kind = {item.kind: item.severity for item in findings}
    assert by_kind["missing_acceptance"] == "warning"
    assert by_kind["missing_validation_surface"] == "warning"
    assert "blocking" not in by_kind.values()


def test_split_quality_blocking_mode_fails_closed_for_missing_surfaces() -> None:
    work_unit = WorkUnitContract(
        id="WU-TASK-1",
        task_id="TASK-1",
        feature_id="FEAT-1",
        title="Implement CLI JSON output",
        goal="Implement CLI JSON output",
        outcome="CLI JSON output implemented",
    )

    findings = check_split_quality(
        work_unit,
        mode="blocking",
        require_validation_surface=True,
    )

    by_kind = {item.kind: item.severity for item in findings}
    assert by_kind["missing_acceptance"] == "blocking"
    assert by_kind["missing_validation_surface"] == "blocking"


def test_why_not_done_projects_work_unit_and_missing_evidence(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    _add_task(
        state_dir,
        required_events=["gate.passed"],
        required_commands=["pytest tests/test_queue.py"],
    )

    projection = project_why_not_done(state_dir, "TASK-1")
    data = projection.to_dict()

    assert data["work_unit"]["id"] == "WU-TASK-1"
    assert data["work_unit"]["validation_surface"]["events"] == ["gate.passed"]
    kinds = {item["kind"] for item in data["why_not_done"]}
    assert "missing_acceptance_evidence" in kinds
    assert "missing_evidence" in kinds
    assert data["recommended_action"]["kind"] == "continuation"


def test_completion_audit_routes_done_when_required_evidence_exists(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    task = _add_task(state_dir, required_events=["gate.passed"])
    task.contract.acceptance_evidence = {
        "crashed worker task can be reclaimed": ["evt-accept"]
    }
    TaskStore(state_dir / "kanban.json").update(task.id, contract=task.contract)
    _writer(state_dir).append(ZfEvent(
        type="gate.passed",
        actor="zf-cli",
        task_id=task.id,
        payload={"gate": "unit"},
    ))

    result = audit_completion(state_dir, task.id)

    assert result.route == "done"
    assert result.missing_evidence == []


def test_apply_completion_audit_self_completion_schedules_continuation(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    _add_task(state_dir, required_events=["gate.passed"])
    writer = _writer(state_dir)
    trigger = writer.append(ZfEvent(
        type="worker.completed",
        actor="dev-1",
        task_id="TASK-1",
        payload={"summary": "done"},
    ))

    result = apply_completion_audit(
        state_dir=state_dir,
        task_id="TASK-1",
        event_writer=writer,
        trigger_event=trigger,
    )

    assert result.route == "continuation"
    event_types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
    assert "completion_audit.routed" in event_types
    assert "task.continuation_scheduled" in event_types


def test_context_critical_audit_routes_retry_and_writes_resume_packet(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    task = _add_task(state_dir, required_events=["gate.passed"])
    TaskStore(state_dir / "kanban.json").update(
        task.id,
        active_dispatch_id="disp-context",
    )
    writer = _writer(state_dir)
    trigger = writer.append(ZfEvent(
        type="worker.context.critical",
        actor="dev-1",
        task_id=task.id,
        payload={
            "task_id": task.id,
            "dispatch_id": "disp-context",
            "role": "dev",
            "instance_id": "dev-1",
            "backend": "claude-code",
            "context_usage_ratio": 0.92,
            "session_ref": "session-1",
            "source": "session_reader",
            "reason": "hard_cap_exceeded",
        },
    ))

    result = apply_completion_audit(
        state_dir=state_dir,
        task_id=task.id,
        event_writer=writer,
        trigger_event=trigger,
    )

    assert result.route == "retry"
    assert result.resume_packet_path
    assert Path(result.resume_packet_path).exists()
    events = EventLog(state_dir / "events.jsonl").read_all()
    routed = [event for event in events if event.type == "completion_audit.routed"][-1]
    assert routed.causation_id == trigger.id
    assert routed.payload["trigger_event_type"] == "worker.context.critical"
    assert routed.payload["resume_packet_missing_evidence_count"] >= 1
    assert "context critical" in routed.payload["reason"]
    assert (state_dir / "resume_packets" / f"{task.id}.json").exists()


def test_completion_audit_payload_tracks_dispatch_and_feature_boundary(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    task = _add_task(state_dir, required_events=["gate.passed"])
    task.active_dispatch_id = "disp-1"
    task.contract.acceptance_evidence = {
        "crashed worker task can be reclaimed": ["evt-accept"]
    }
    TaskStore(state_dir / "kanban.json").update(
        task.id,
        active_dispatch_id="disp-1",
        contract=task.contract,
    )
    writer = _writer(state_dir)
    writer.append(ZfEvent(type="gate.passed", actor="gate", task_id=task.id))
    trigger = writer.append(ZfEvent(
        type="worker.completed",
        actor="dev-1",
        task_id=task.id,
        payload={"dispatch_id": "disp-1", "attempt": 2, "boundary": "feature"},
    ))

    result = apply_completion_audit(
        state_dir=state_dir,
        task_id=task.id,
        event_writer=writer,
        trigger_event=trigger,
    )

    assert result.route == "integration_queue"
    assert result.dispatch_id == "disp-1"
    assert result.attempt == 2
    routed = [
        event for event in EventLog(state_dir / "events.jsonl").read_all()
        if event.type == "task.integration_enqueued"
    ][-1]
    assert routed.payload["dispatch_id"] == "disp-1"
    assert routed.payload["attempt"] == 2


def test_resume_packet_writes_short_runtime_fact_packet(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    _add_task(state_dir, required_events=["gate.passed"])
    packet = build_resume_packet(state_dir, "TASK-1", dispatch_id="disp-1")
    path = write_resume_packet(state_dir, packet, dispatch_id="disp-1")

    assert path.exists()
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["task_id"] == "TASK-1"
    assert loaded["work_unit_id"] == "WU-TASK-1"
    assert loaded["missing_evidence"]
    assert "next_required_action" in loaded


def test_resume_packet_includes_artifact_refs_and_sufficiency_requirements(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    task = _add_task(state_dir)
    artifact = tmp_path / "docs" / "plans" / "task-plan.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("plan\n", encoding="utf-8")
    sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    task.contract.plan_ref = "docs/plans/task-plan.md"
    TaskStore(state_dir / "kanban.json").update(task.id, contract=task.contract)
    (state_dir / "refs").mkdir()
    (state_dir / "refs" / "task-index.json").write_text(json.dumps({
        task.id: {
            "task_id": task.id,
            "manifest_event_id": "evt-manifest",
            "contract_refs": {"plan_ref": "docs/plans/task-plan.md"},
            "artifact_refs": [
                {
                    "kind": "plan",
                    "path": "docs/plans/task-plan.md",
                    "sha256": sha,
                    "summary": "plan",
                    "artifact_id": "plan-task-1-v1",
                    "version": 1,
                    "status": "accepted",
                },
            ],
        },
    }))

    packet = build_resume_packet(state_dir, task.id, dispatch_id="disp-1")

    assert packet["accepted_artifact_refs"][0]["path"] == "docs/plans/task-plan.md"
    assert packet["artifact_hash_status"][0]["status"] == "ok"
    assert packet["artifact_recovery"]["contract_refs"]["plan_ref"] == (
        "docs/plans/task-plan.md"
    )
    assert packet["sufficiency_requirements"]["required_fields"]


def test_workpad_retry_stall_goal_trace_and_skill_projections(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    task = _add_task(
        state_dir,
        required_events=["gate.passed"],
        required_commands=["pytest tests/test_queue.py"],
    )
    task.skills_required = ["python-dev"]
    task.active_dispatch_id = "disp-live"
    TaskStore(state_dir / "kanban.json").update(
        task.id,
        skills_required=task.skills_required,
        active_dispatch_id="disp-live",
    )
    FeatureStore(state_dir / "feature_list.json").add(Feature(
        id="FEAT-1",
        title="Queue recovery",
        description="Ship queue recovery\n验证: pytest tests/test_queue.py\n约束: no redis",
    ))
    writer = _writer(state_dir)
    writer.append(ZfEvent(
        type="task.retry_scheduled",
        actor="zf-cli",
        task_id=task.id,
        payload={
            "attempt": 3,
            "worker": "dev-1",
            "dispatch_id": "disp-live",
            "retry_token": "tok-live",
            "generation": "gen-1",
        },
    ))
    writer.append(ZfEvent(
        type="worker.context.warning",
        actor="dev-1",
        task_id=task.id,
        payload={"context_usage_ratio": 0.91},
    ))
    writer.append(ZfEvent(
        type="completion_audit.routed",
        actor="zf-cli",
        task_id=task.id,
        payload={"route": "continuation"},
    ))

    workpad = project_workpad(state_dir, task.id).to_dict()
    retry = project_retry_metadata(state_dir, task.id).to_dict()
    stale = guard_retry_token(
        state_dir,
        task.id,
        retry_token="old",
        event_writer=writer,
    )
    stall = project_stall_status(state_dir, task.id, actor="dev-1").to_dict()
    goal = map_goal_to_work_units(state_dir, "FEAT-1")
    trace = decision_trace_for_task(state_dir, task.id)
    skills = project_skill_set(state_dir, task.id)

    assert workpad["validation"]
    assert retry["attempt"] == 3
    assert retry["stale"] is False
    assert stale["ok"] is False
    assert stall["status"] == "context_warn"
    assert goal["goal"]["verification_surface"] == ["pytest tests/test_queue.py"]
    assert goal["work_units"][0]["id"] == "WU-TASK-1"
    assert trace["decisions"]
    assert "python-dev" in skills["skills"]
    assert any(
        event.type == "task.retry.stale_ignored"
        for event in EventLog(state_dir / "events.jsonl").read_all()
    )


def test_stall_projection_marks_old_heartbeat_as_stalled(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    _add_task(state_dir)
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
    _writer(state_dir).append(ZfEvent(
        type="worker.heartbeat",
        ts=old_ts,
        actor="dev-1",
        task_id="TASK-1",
    ))

    status = project_stall_status(
        state_dir,
        "TASK-1",
        actor="dev-1",
        heartbeat_threshold_sec=30,
    ).to_dict()

    assert status["status"] == "stalled"
    assert status["reasons"]


def test_integration_item_marks_duplicate_changed_files(tmp_path: Path) -> None:
    state_dir = _state(tmp_path)
    _add_task(
        state_dir,
        task_id="TASK-1",
        affected_files=["src/shared.py"],
        required_commands=["pytest tests/test_a.py"],
    )
    _add_task(
        state_dir,
        task_id="TASK-2",
        affected_files=["src/shared.py"],
        required_commands=["pytest tests/test_b.py"],
    )

    item = build_integration_item(state_dir, "FEAT-1").to_dict()

    assert item["conflict_risk"]["level"] == "high"
    assert "src/shared.py" in item["changed_files"]
    assert len(item["work_units"]) == 2


def test_workflow_harness_profile_loads_and_rejects_invalid(tmp_path: Path) -> None:
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        """
project:
  name: demo
workflow:
  harness_profile: strict
  completion_audit:
    enabled: true
  resume_packet:
    enabled: true
  strict_triggers:
    rework_attempts_gte: 2
roles: []
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    assert cfg.workflow.harness_profile == "strict"
    assert cfg.workflow.completion_audit.enabled is True
    assert cfg.workflow.resume_packet.enabled is True
    assert cfg.workflow.strict_triggers.rework_attempts_gte == 2

    config_path.write_text(
        "project:\n  name: demo\nworkflow:\n  harness_profile: heavy\n",
        encoding="utf-8",
    )
    try:
        load_config(config_path)
    except ConfigError as exc:
        assert "workflow.harness_profile" in str(exc)
    else:
        raise AssertionError("invalid harness_profile should fail")


def test_complexity_escalates_effective_profile_and_work_unit_reason(
    tmp_path: Path,
) -> None:
    state_dir = _state(tmp_path)
    task = _add_task(
        state_dir,
        affected_files=["src/zf/runtime/orchestrator.py"],
    )
    task.contract.complexity = "complex"
    TaskStore(state_dir / "kanban.json").update(task.id, contract=task.contract)
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(
        """
project:
  name: demo
workflow:
  harness_profile: baseline
  strict_triggers:
    rework_attempts_gte: 2
    file_globs:
    - src/zf/runtime/**
roles: []
""",
        encoding="utf-8",
    )
    cfg = load_config(config_path)

    work_unit = project_why_not_done(
        state_dir,
        task.id,
        config=cfg,
    ).to_dict()["work_unit"]

    assert task_complexity(task) == "complex"
    assert effective_profile_for_task(task, config=cfg) == "strict"
    assert work_unit["complexity"] == "complex"
    assert "complexity=complex" in work_unit["effective_profile_reason"]


def test_spec_ingest_records_complexity_override(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    state_dir = _state(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        "project:\n  name: demo\n  state_dir: .zf\nroles: []\n",
        encoding="utf-8",
    )
    spec = tmp_path / "spec.md"
    spec.write_text(
        """---
spec: demo
tasks:
  - id: TASK-CX
    title: Complex task
    scope:
      - src/zf/runtime/orchestrator.py
    verification: pytest tests/test_long_horizon_runtime.py
    complexity: complex
---

# demo
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    rc = main(["spec", "ingest", str(spec)])

    assert rc == 0
    _ = capsys.readouterr()
    task = TaskStore(state_dir / "kanban.json").get("TASK-CX")
    assert task is not None
    assert task.contract.complexity == "complex"
    event_types = [event.type for event in EventLog(state_dir / "events.jsonl").read_all()]
    assert "task.complexity.overridden" in event_types


def test_backlog_why_not_done_cli_json(tmp_path: Path, monkeypatch, capsys) -> None:
    state_dir = _state(tmp_path)
    _add_task(state_dir, required_events=["gate.passed"])
    (tmp_path / "zf.yaml").write_text(
        "project:\n  name: demo\n  state_dir: .zf\nroles: []\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    rc = main(["backlog", "why-not-done", "TASK-1", "--json"])

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["task_id"] == "TASK-1"
    assert data["recommended_action"]["kind"] == "continuation"
