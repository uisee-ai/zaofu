from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    QualityGateConfig,
    RoleConfig,
    RuntimeAutoresearchResidentConfig,
    RuntimeConfig,
    RuntimeRunManagerConfig,
    RuntimeRunManagerReflectConfig,
    RuntimeRunManagerSourceRepairConfig,
    SessionConfig,
    WorkflowAdmissionReplanConfig,
    WorkflowConfig,
    WorkflowDagConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_types import OrchestratorDecision
from zf.runtime.run_manager import (
    HUMAN_ESCALATION_SENT,
    RUN_MANAGER_ACTION_APPLIED,
    RUN_MANAGER_ACTION_BLOCKED,
    RUN_MANAGER_ACTION_FAILED,
    RUN_MANAGER_ACTION_VERIFY_FAILED,
    RUN_MANAGER_ACTION_VERIFY_PASSED,
    RUN_MANAGER_AUTORESEARCH_CONSUMED,
    RUN_MANAGER_AUTORESEARCH_REQUESTED,
    RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED,
    RUN_MANAGER_HUMAN_DECISION_APPLIED,
    RUN_MANAGER_HUMAN_DECISION_REJECTED,
    RUN_MANAGER_REFLECT_COMPLETED,
    RUN_MANAGER_REFLECT_REQUESTED,
    RUN_MANAGER_REPAIR_ACCEPTED,
    RUN_MANAGER_REPAIR_MERGE_MERGED,
    RUN_MANAGER_REPAIR_MERGE_QUEUED,
    RUN_MANAGER_TRANSITION,
    build_run_goal_projection,
    build_run_manager_monitor_projection,
    build_run_manager_projection,
    build_run_monitor_projection,
    build_repair_merge_queue,
    decide_action_policy,
    run_manager_tick,
    write_run_manager_projections,
    _post_verify_action,
)
from zf.autoresearch.loop_types import ReflectionResult
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_resume import build_workflow_resume_projection
from zf.runtime.workflow_anchor import mark_workflow_fanout_anchor


def _state(tmp_path: Path) -> tuple[Path, EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log)


class _RespawnRecordingTransport:
    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        return None

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _write_supervisor_attention(state_dir: Path) -> None:
    supervisor_dir = state_dir / "projections" / "supervisor"
    supervisor_dir.mkdir(parents=True)
    (supervisor_dir / "snapshot.json").write_text(
        json.dumps({
            "schema_version": "supervisor.snapshot.v1",
            "attention_items": [{
                "attention_id": "attn-stall-1",
                "status": "open",
                "fingerprint": "runtime:dispatch.silent_stall:T1",
                "severity": "high",
                "title": "Dispatch silent stall",
                "summary": "task.assigned had no matching terminal event",
                "task_id": "T1",
                "fanout_id": "fanout-impl-1",
                "stage_id": "cj-min-slice-implementation",
                "lane": "dev-lane-1",
                "source_event_ids": ["evt-stall-1"],
                "source_ref": "events.jsonl#evt-stall-1",
                "suggested_route": "autoresearch_trigger",
                "suggested_action": {"kind": "diagnose_worker_noop"},
            }],
        }) + "\n",
        encoding="utf-8",
    )


def test_run_manager_projection_includes_budget_diagnostics(tmp_path: Path) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        type="cost.budget.exceeded",
        id="evt-budget-1",
        actor="zf-cli",
        payload={
            "scope": "global",
            "budget_usd": 60.0,
            "current_usd": 96.0379,
        },
    ))
    log.append(ZfEvent(
        type="cost.budget.exceeded",
        id="evt-budget-2",
        actor="zf-cli",
        payload={
            "scope": "global",
            "budget_usd": 60.0,
            "current_usd": 96.0379,
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=ZfConfig(global_budget_usd=60.0),
    )

    diagnostics = projection["budget_diagnostics"]
    assert diagnostics["status"] == "exceeded"
    assert diagnostics["summary"]["open"] == 1
    assert diagnostics["summary"]["owner_visible_default"] is False
    item = diagnostics["items"][0]
    assert item["event_count"] == 2
    assert item["first_event_id"] == "evt-budget-1"
    assert item["latest_event_id"] == "evt-budget-2"
    assert item["notification_policy"] == "owner_on_human_required"


def _write_failure_closeout_manifest(project_root: Path) -> Path:
    backlogs = project_root / "artifacts" / "failure-closeout" / "backlogs"
    backlogs.mkdir(parents=True, exist_ok=True)
    draft = backlogs / "2026-07-03-0000-fail-run-manager.md"
    draft.write_text(
        "# Failure Candidate fail-run-manager\n\n"
        "> 状态: proposed\n\n"
        "## 背景\n\n"
        "run.manager.action.failed: test fixture\n",
        encoding="utf-8",
    )
    manifest = project_root / "artifacts" / "failure-closeout" / "failure-closeout-manifest.json"
    manifest.write_text(
        json.dumps({
            "schema_version": "failure-closeout.v1",
            "status": "ready",
            "manifest_ref": str(manifest),
            "items": [{
                "failure_id": "fail-run-manager",
                "outputs": {"backlog": str(draft)},
            }],
        }) + "\n",
        encoding="utf-8",
    )
    return manifest


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="run-manager-test"),
        session=SessionConfig(tmux_session="run-manager-test"),
        roles=[
            RoleConfig(name="dev-lane-0", backend="mock"),
            RoleConfig(name="review-lane-0", backend="mock"),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(enabled=True, graph_review_test_judge_reconcile=True),
        ),
    )


def _lane_resume_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="run-manager-resume-test"),
        session=SessionConfig(tmux_session="run-manager-resume-test"),
        roles=[
            RoleConfig(
                name="dev-lane-0",
                backend="mock",
                publishes=["dev.build.done", "dev.failed"],
            ),
            RoleConfig(
                name="review-lane-0",
                backend="mock",
                triggers=["static_gate.passed"],
                publishes=["review.approved", "review.rejected"],
            ),
            RoleConfig(
                name="verify-lane-0",
                backend="mock",
                triggers=["review.approved"],
                publishes=["verify.passed", "verify.failed"],
            ),
        ],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(
                enabled=True,
                graph_review_test_judge_reconcile=True,
            ),
            rework_routing={
                "review.rejected": "dev-lane-0",
                "verify.failed": "dev-lane-0",
            },
        ),
        quality_gates={
            "static": QualityGateConfig(enabled=True, required_checks=["true"]),
        },
    )


def _reflect_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="run-manager-reflect-test"),
        session=SessionConfig(tmux_session="run-manager-reflect-test"),
        roles=[RoleConfig(name="dev-lane-0", backend="mock")],
        runtime=RuntimeConfig(
            run_manager=RuntimeRunManagerConfig(
                reflect=RuntimeRunManagerReflectConfig(
                    enabled=True,
                    backend="codex",
                    timeout_seconds=45,
                ),
            ),
        ),
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(enabled=True, graph_review_test_judge_reconcile=True),
        ),
    )


def _source_repair_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="run-manager-source-repair-test"),
        session=SessionConfig(tmux_session="run-manager-source-repair-test"),
        roles=[RoleConfig(name="dev-lane-0", backend="mock")],
        runtime=RuntimeConfig(
            run_manager=RuntimeRunManagerConfig(
                backend="claude-code",
                source_repair=RuntimeRunManagerSourceRepairConfig(
                    enabled=True,
                    backend="claude-code",
                ),
            ),
        ),
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(enabled=True, graph_review_test_judge_reconcile=True),
        ),
    )


def _admission_replan_config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="run-manager-admission-replan-test"),
        session=SessionConfig(tmux_session="run-manager-admission-replan-test"),
        roles=[RoleConfig(name="dev-lane-0", backend="mock")],
        workflow=WorkflowConfig(
            dag=WorkflowDagConfig(enabled=True, graph_review_test_judge_reconcile=True),
            admission_replan=WorkflowAdmissionReplanConfig(
                enabled=True,
                resynth_trigger="zaofu.refactor.review.ready",
            ),
        ),
    )


def _append_repair_failed_children_fixture(log: EventLog) -> None:
    task_map = ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-RM",
            "feature_id": "CJMIN-RM",
            "trace_id": "trace-rm",
            "task_map_ref": ".zf/artifacts/CJMIN-RM/task_map.json",
            "source_index_ref": ".zf/artifacts/CJMIN-RM/source_index.json",
            "source_commit": "base123",
            "candidate_base_commit": "base123",
            "target_ref": "dev",
        },
        correlation_id="trace-rm",
    )
    aggregate = ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-rm",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "failure_event": "integration.failed",
            "pdd_id": "CJMIN-RM",
            "feature_id": "CJMIN-RM",
            "candidate_ref": "cand/CJMIN-RM",
            "candidate_base_commit": "base123",
            "candidate_head_commit": "head456",
            "completed_task_ids": ["CJMIN-GATEWAY-001"],
            "failed_children": ["dev-lane-0-CJMIN-ASSEMBLY-001"],
        },
        correlation_id="trace-rm",
    )
    failed = ZfEvent(
        type="integration.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-rm",
            "pdd_id": "CJMIN-RM",
            "reason": "assembly failed",
        },
        causation_id=aggregate.id,
        correlation_id="trace-rm",
    )
    log.append(task_map)
    log.append(aggregate)
    log.append(failed)


def test_batch_resume_ignores_repair_superseded_by_later_success(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    task_map = ZfEvent(
        id="evt-task-map",
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "PDD-RUN",
            "feature_id": "feature-run",
            "trace_id": "trace-run",
            "task_map_ref": "artifacts/workflow/run/task-map.json",
            "source_index_ref": "artifacts/workflow/run/source-index.json",
            "candidate_ref": "candidate/PDD-RUN",
        },
        correlation_id="trace-run",
    )
    log.append(task_map)
    aggregate = ZfEvent(
        id="evt-aggregate",
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-run",
            "stage_id": "issue-lanes-impl",
            "status": "failed",
            "failure_event": "integration.failed",
            "pdd_id": "PDD-RUN",
            "feature_id": "feature-run",
            "candidate_ref": "candidate/PDD-RUN",
            "completed_task_ids": [],
            "failed_children": ["fix-lane-0-TASK-1"],
        },
        causation_id=task_map.id,
        correlation_id="trace-run",
    )
    log.append(aggregate)
    failed = ZfEvent(
        id="evt-integration-failed",
        type="integration.failed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-run",
            "stage_id": "issue-lanes-impl",
            "pdd_id": "PDD-RUN",
            "feature_id": "feature-run",
            "candidate_ref": "candidate/PDD-RUN",
            "failed_children": ["fix-lane-0-TASK-1"],
        },
        causation_id=aggregate.id,
        correlation_id="trace-run",
    )
    log.append(failed)
    done = ZfEvent(
        id="evt-dev-done",
        type="dev.build.done",
        actor="fix-lane-0",
        task_id="TASK-1",
        payload={
            "fanout_id": "fanout-impl-run",
            "child_id": "fix-lane-0-TASK-1",
            "source_commit": "abc123",
        },
        correlation_id="trace-run",
    )
    log.append(done)
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "trigger_event_id": done.id,
            "task_ref": "task/TASK-1",
        },
        correlation_id="trace-run",
    ))
    log.append(ZfEvent(
        type="fanout.child.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-impl-run",
            "child_id": "fix-lane-0-TASK-1",
            "result_event_id": done.id,
        },
        causation_id=done.id,
        correlation_id="trace-run",
    ))
    log.append(ZfEvent(
        type="candidate.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "PDD-RUN",
            "feature_id": "feature-run",
            "trace_id": "trace-run",
            "fanout_id": "fanout-impl-run",
            "candidate_ref": "candidate/PDD-RUN",
            "completed_task_ids": ["TASK-1"],
            "failed_children": [],
        },
        causation_id=failed.id,
        correlation_id="trace-run",
    ))

    projection = build_workflow_resume_projection(
        state_dir,
        _lane_resume_config(),
    )

    assert projection["batch_checkpoints"] == []


def _append_trigger_rework_fixture(log: EventLog) -> None:
    log.append(ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-rework-rm",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "failure_event": "integration.failed",
            "pdd_id": "CJMIN-RM",
            "feature_id": "CJMIN-RM",
            "task_map_ref": ".zf/artifacts/CJMIN-RM/task_map.json",
            "source_commit": "base123",
            "candidate_base_commit": "base123",
        },
        correlation_id="trace-rm",
    ))


def _append_candidate_rework_fixture(log: EventLog) -> None:
    log.append(ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-RM",
            "feature_id": "CJMIN-RM",
            "trace_id": "trace-rm",
            "target_ref": "cand/CJMIN-RM",
            "task_map_ref": ".zf/artifacts/CJMIN-RM/task_map.json",
            "source_index_ref": ".zf/artifacts/CJMIN-RM/source_index.json",
            "source_commit": "base123",
            "candidate_base_commit": "base123",
        },
        correlation_id="trace-rm",
    ))
    log.append(ZfEvent(
        id="verify-rm-1",
        type="verify.failed",
        actor="zf-cli",
        payload={
            "pdd_id": "CJMIN-RM",
            "trace_id": "trace-rm",
            "target_ref": "cand/CJMIN-RM",
            "reason": "contract mismatch",
            "findings": [{
                "task_id": "CJMIN-WEB-001",
                "category": "parity_gap",
                "message": "web dashboard is still a demo bridge",
                "verification_command": "npm run test -- web",
                "gap_task": {
                    "task_id": "CJMIN-WEB-GAP-001",
                    "affinity_tag": "web-tui",
                    "owner_role": "dev",
                    "allowed_paths": ["web/**", "packages/web-adapter/**"],
                    "acceptance_criteria": ["web dashboard reaches Cangjie runtime"],
                    "verification": ["npm run test -- web"],
                    "source_refs": ["hermes-agent/web"],
                },
            }],
        },
        correlation_id="trace-rm",
    ))


def _append_unknown_batch_fixture(log: EventLog) -> None:
    log.append(ZfEvent(
        type="fanout.aggregate.completed",
        actor="zf-cli",
        payload={
            "fanout_id": "fanout-unknown-rm",
            "stage_id": "cj-min-slice-implementation",
            "status": "failed",
            "failure_event": "integration.failed",
            "pdd_id": "CJMIN-RM",
            "feature_id": "CJMIN-RM",
            "reason": "external gate failed without deterministic resume",
        },
        correlation_id="trace-rm",
    ))


def test_workflow_batch_resume_controlled_action_applies_and_emits_web_completion(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    _append_repair_failed_children_fixture(log)
    checkpoint = build_workflow_resume_projection(
        state_dir,
        _config(),
    )["batch_checkpoints"][0]
    requested = writer.emit(
        "web.action.requested",
        actor="web",
        payload={"action": "workflow-batch-resume"},
    )
    service = ControlledActionService(
        state_dir,
        writer,
        config=_config(),
        actor="web",
        surface="web",
    )

    result = service.execute(
        action="workflow-batch-resume",
        requested_action="workflow-batch-resume",
        requested=requested,
        payload={
            "checkpoint_id": checkpoint["checkpoint_id"],
            "safe_resume_action": checkpoint["safe_resume_action"],
        },
    )

    events = log.read_all()
    assert result["status"] == "applied"
    assert any(event.type == "workflow.resume.applied" for event in events)
    assert any(event.type == "web.action.completed" for event in events)


def test_run_manager_executes_safe_workflow_resume_and_post_verifies(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    _append_repair_failed_children_fixture(log)
    initial = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_lane_resume_config(),
    )
    pending = initial["pending_actions"][0]
    assert [item["action"] for item in initial["pending_actions"]] == [
        "workflow-batch-resume",
    ]
    assert pending["failure_class"] == "deterministic_resume"
    assert pending["owner_route"] == "controlled_action"
    assert pending["action_policy"] == "auto_decide"
    assert pending["intervention_class"] == "auto_recover"
    assert pending["problem_envelope"]["problem_class"] == "workflow_progress"
    assert pending["problem_envelope"]["failure_class"] == "deterministic_resume"
    assert initial["policy"]["by_problem_class"] == {"workflow_progress": 1}
    assert "task_map.ready" in pending["verify_condition"]
    assert initial["status_explain"]["intervention_class"] == "auto_recover"
    assert initial["status_explain"]["next_auto_action"] == "repair_failed_children"

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.actions_applied == 1
    assert any(event.type == RUN_MANAGER_ACTION_APPLIED for event in events)
    assert any(event.type == RUN_MANAGER_ACTION_VERIFY_PASSED for event in events)
    assert any(
        event.type == RUN_MANAGER_TRANSITION
        and event.payload.get("transition") == "apply_batch_resume"
        for event in events
    )
    assert any(
        event.type == "task_map.ready"
        and event.payload.get("source") == "workflow_resume_batch"
        for event in events
    )
    projection = json.loads(
        (state_dir / "projections" / "run_manager.json").read_text(encoding="utf-8")
    )
    explain = json.loads(
        (state_dir / "projections" / "run_status_explain.json").read_text(encoding="utf-8")
    )
    assert projection["summary"]["goal_status"] in {"unknown", "active"}
    assert projection["monitor"]["state"] in {"healthy_waiting", "repair_in_flight"}
    assert explain["schema_version"] == "run-status-explain.v1"
    assert explain["source_refs"]["events"] == "events.jsonl"


def test_run_manager_noop_tick_keeps_summary_out_of_transition_event(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    completed = next(
        event for event in events
        if event.type == "run.manager.tick.completed"
    )
    assert result.actions_applied == 0
    assert result.actions_blocked == 0
    assert result.actions_failed == 0
    assert not any(event.type == RUN_MANAGER_TRANSITION for event in events)
    assert completed.payload["transition"] == "continue_waiting"
    assert completed.payload["transition_event_id"] == ""
    assert completed.payload["transition_event_written"] is False

    projection = json.loads(
        (state_dir / "projections" / "run_manager.json").read_text(encoding="utf-8")
    )
    summary = projection["last_tick_summary"]
    assert summary["completed_event_id"] == completed.id
    assert summary["transition"] == "continue_waiting"
    assert summary["transition_event_id"] == ""
    assert summary["transition_event_written"] is False


def test_run_status_explain_flags_ready_action_after_noop_tick(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    _append_repair_failed_children_fixture(log)
    log.append(ZfEvent(
        type="run.manager.tick.completed",
        id="evt-noop-tick",
        actor="run-manager",
        payload={
            "actions_applied": 0,
            "actions_blocked": 0,
            "actions_failed": 0,
            "transition": "continue_waiting",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_lane_resume_config(),
    )
    explain = projection["status_explain"]

    assert explain["pending_execution"]["status"] == "ready_but_last_tick_no_action"
    assert explain["pending_execution"]["last_tick_event_id"] == "evt-noop-tick"
    assert explain["pending_actions"][0]["readiness"] == "ready_to_execute"
    assert explain["pending_actions"][0]["skip_reason"] == "last_tick_no_action"


def test_run_manager_executes_task_level_workflow_resume_and_post_verifies(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="CJMIN-GATEWAY-001",
        title="gateway",
        status="in_progress",
        assigned_to="dev-lane-0",
    ))
    dev_done = ZfEvent(
        id="dev-done-rm-1",
        type="dev.build.done",
        actor="dev-lane-0",
        task_id="CJMIN-GATEWAY-001",
        payload={"dispatch_id": "disp-dev"},
    )
    gate = ZfEvent(
        id="gate-rm-1",
        type="static_gate.passed",
        actor="zf-cli",
        task_id="CJMIN-GATEWAY-001",
        payload={"trigger_event_id": dev_done.id},
    )
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="CJMIN-GATEWAY-001",
        payload={"assignee": "dev-lane-0", "dispatch_id": "disp-dev"},
    ))
    log.append(dev_done)
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="CJMIN-GATEWAY-001",
        payload={"trigger_event_id": dev_done.id},
    ))
    log.append(gate)

    initial = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_lane_resume_config(),
    )
    pending = initial["pending_actions"][0]
    assert pending["action"] == "workflow-task-resume"
    assert pending["safe_resume_action"] == "needs_stage_dispatch"
    assert pending["task_id"] == "CJMIN-GATEWAY-001"
    assert pending["policy_decision"]["decision"] == "auto_decide"
    assert "task.dispatched" in pending["expected_downstream_events"]

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_lane_resume_config(),
        event_log=log,
        action_filter={"workflow-task-resume"},
        spawn_repairs=False,
    )

    events = log.read_all()
    task = store.get("CJMIN-GATEWAY-001")
    assert result.actions_applied == 1
    assert task is not None
    assert task.status == "review"
    assert task.assigned_to == "review-lane-0"
    assert any(
        event.type == "task.dispatched"
        and event.payload.get("source") == "workflow_resume"
        and event.payload.get("trigger_event_id") == gate.id
        for event in events
    )
    assert any(event.type == RUN_MANAGER_ACTION_VERIFY_PASSED for event in events)


def test_run_manager_executes_task_level_rework_resume_once(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="CJMIN-GATEWAY-001",
        title="gateway",
        status="review",
        assigned_to="review-lane-0",
    ))
    rejected = ZfEvent(
        id="review-rejected-rm-1",
        type="review.rejected",
        actor="review-lane-0",
        task_id="CJMIN-GATEWAY-001",
        payload={"reason": "missing regression evidence"},
    )
    log.append(rejected)

    initial = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_lane_resume_config(),
    )
    pending = initial["pending_actions"][0]
    assert pending["action"] == "workflow-task-resume"
    assert pending["safe_resume_action"] == "needs_rework_dispatch"
    assert pending["policy_decision"]["decision"] == "auto_decide"

    first = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_lane_resume_config(),
        event_log=log,
        action_filter={"workflow-task-resume"},
        spawn_repairs=False,
    )
    second = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_lane_resume_config(),
        event_log=log,
        action_filter={"workflow-task-resume"},
        spawn_repairs=False,
    )

    events = log.read_all()
    task = store.get("CJMIN-GATEWAY-001")
    rework = [
        event for event in events
        if event.type == "task.rework.requested"
        and event.payload.get("source") == "workflow_resume"
    ]
    assert first.actions_applied == 1
    assert second.actions_applied == 0
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "dev-lane-0"
    assert len(rework) == 1
    assert rework[0].payload["trigger_event_id"] == rejected.id
    assert any(event.type == RUN_MANAGER_ACTION_VERIFY_PASSED for event in events)


def test_run_manager_executes_reader_stage_replan_resume(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(mark_workflow_fanout_anchor(
        Task(id="PRD-WFINT-1", title="PRD flow", status="in_progress"),
        request_id="wfint-1",
        pattern_id="prd-scan",
    ))
    origin = ZfEvent(type="prd.scan.completed", actor="zf-cli",
                     payload={"target_ref": "docs/prd/TODO.md"})
    failure = ZfEvent(type="prd.plan.failed", actor="zf-cli",
                      payload={"reason": "bad task_map"})
    log.append(origin)
    log.append(failure)
    cfg = ZfConfig(
        project=ProjectConfig(name="run-manager-stage-replan"),
        session=SessionConfig(tmux_session="run-manager-stage-replan"),
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="prd-plan",
                trigger="prd.scan.completed",
                topology="fanout_reader",
                aggregate=FanoutAggregateConfig(
                    success_event="task_map.ready",
                    failure_event="prd.plan.failed",
                ),
            ),
        ]),
    )

    initial = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=cfg,
    )
    pending = initial["pending_actions"][0]
    assert pending["action"] == "workflow-task-resume"
    assert pending["safe_resume_action"] == "needs_stage_replan"
    assert pending["owner_route"] == "controlled_action"
    assert pending["policy_decision"]["decision"] == "auto_decide"
    checkpoint_id = pending["checkpoint_id"]
    log.append(ZfEvent(
        type=RUN_MANAGER_AUTORESEARCH_REQUESTED,
        actor="run-manager",
        payload={
            "checkpoint_id": checkpoint_id,
            "safe_resume_action": "needs_stage_replan",
        },
    ))
    rerouted = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=cfg,
    )
    pending = [
        item for item in rerouted["pending_actions"]
        if item.get("checkpoint_id") == checkpoint_id
    ][0]
    assert pending["owner_route"] == "controlled_action"

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=cfg,
        event_log=log,
        action_filter={"workflow-task-resume"},
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.actions_applied == 1
    assert any(
        event.type == "prd.scan.completed" and event.causation_id == failure.id
        for event in events
    )
    assert any(
        event.type == "workflow.resume.applied"
        and event.payload.get("mode") == "stage_replan_trigger"
        for event in events
    )
    assert any(event.type == RUN_MANAGER_ACTION_VERIFY_PASSED for event in events)


def test_run_manager_fails_post_verify_when_gate_resume_checkpoint_remains(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="CJMIN-GATEWAY-001",
        title="gateway",
        status="in_progress",
        assigned_to="dev-lane-0",
    ))
    dev_done = ZfEvent(
        id="dev-done-rm-gate",
        type="dev.build.done",
        actor="dev-lane-0",
        task_id="CJMIN-GATEWAY-001",
        payload={"dispatch_id": "disp-dev"},
    )
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="CJMIN-GATEWAY-001",
        payload={"assignee": "dev-lane-0", "dispatch_id": "disp-dev"},
    ))
    log.append(dev_done)

    initial = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_lane_resume_config(),
    )
    pending = initial["pending_actions"][0]
    assert pending["action"] == "workflow-task-resume"
    assert pending["safe_resume_action"] == "needs_gate_dispatch"

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_lane_resume_config(),
        event_log=log,
        action_filter={"workflow-task-resume"},
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.actions_applied == 1
    assert any(event.type == "stage.transition.stalled" for event in events)
    verify_failed = [
        event for event in events
        if event.type == RUN_MANAGER_ACTION_VERIFY_FAILED
    ]
    assert verify_failed
    # ZF-E2E-PRDCTL-P1-4: the honest failure reason is now the unroutable
    # gate itself, and the filing guard stops re-filing the dead checkpoint
    # (pending drops to 0 instead of looping verify.failed forever).
    assert "gate unroutable" in verify_failed[-1].payload["reason"]
    projection = build_workflow_resume_projection(
        state_dir,
        _lane_resume_config(),
        events=events,
    )
    assert projection["summary"]["pending"] == 0


def test_run_manager_post_verify_reads_checkpoint_matched_downstream_events(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        id="resume-applied",
        type="workflow.resume.applied",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "idempotency_key": "wfres-1",
            "safe_resume_action": "needs_rework_dispatch",
        },
    ))

    _post_verify_action(
        writer,
        {
            "action": "workflow-task-resume",
            "checkpoint_id": "wfres-1",
            "safe_resume_action": "needs_rework_dispatch",
            "expected_downstream_events": ["workflow.resume.applied"],
        },
        {"ok": True, "status": "applied", "event_id": "control-result-only"},
        causation_id="action-outcome",
    )

    verify = [
        event for event in log.read_all()
        if event.type in {
            RUN_MANAGER_ACTION_VERIFY_PASSED,
            RUN_MANAGER_ACTION_VERIFY_FAILED,
        }
    ][-1]
    assert verify.type == RUN_MANAGER_ACTION_VERIFY_PASSED
    assert verify.payload["observed_event_types"] == ["workflow.resume.applied"]


def test_run_manager_post_verify_does_not_fail_observed_resume_for_stale_checkpoint(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        id="resume-checkpoint",
        type="workflow.resume.checkpoint",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "idempotency_key": "wfres-stale",
            "safe_resume_action": "needs_gate_dispatch",
        },
    ))
    log.append(ZfEvent(
        id="resume-applied",
        type="workflow.resume.applied",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "idempotency_key": "wfres-stale",
            "safe_resume_action": "needs_gate_dispatch",
        },
    ))

    _post_verify_action(
        writer,
        {
            "action": "workflow-task-resume",
            "checkpoint_id": "wfres-stale",
            "safe_resume_action": "needs_gate_dispatch",
            "expected_downstream_events": ["workflow.resume.applied"],
        },
        {"ok": True, "status": "applied", "event_id": "control-result-only"},
        causation_id="action-outcome",
        state_dir=state_dir,
        config=_config(),
    )

    verify = [
        event for event in log.read_all()
        if event.type in {
            RUN_MANAGER_ACTION_VERIFY_PASSED,
            RUN_MANAGER_ACTION_VERIFY_FAILED,
        }
    ][-1]
    assert verify.type == RUN_MANAGER_ACTION_VERIFY_PASSED


def test_run_manager_executes_candidate_rework_controlled_action(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    _append_candidate_rework_fixture(log)
    task_map_path = state_dir / "artifacts" / "CJMIN-RM" / "task_map.json"
    task_map_path.parent.mkdir(parents=True)
    task_map_path.write_text(
        json.dumps({
            "schema_version": "task-map.v1",
            "feature_id": "CJMIN-RM",
            "source_refs": {"source_index_ref": ".zf/artifacts/CJMIN-RM/source_index.json"},
            "tasks": [{
                "task_id": "CJMIN-WEB-001",
                "title": "Web baseline",
                "owner_role": "dev",
                "wave": 0,
                "allowed_paths": ["web/**"],
                "allowed_paths_reason": "original web slice",
                "acceptance": ["baseline web slice exists"],
            }],
        }),
        encoding="utf-8",
    )
    initial = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )
    pending = initial["pending_actions"][0]
    assert [item["action"] for item in initial["pending_actions"]] == [
        "candidate-rework-apply",
    ]
    assert pending["candidate_rework_action"] == "retrigger"
    assert pending["failure_class"] == "candidate_rework_retrigger"
    assert pending["owner_route"] == "controlled_action"
    assert pending["policy_decision"]["decision"] == "auto_decide"
    assert pending["failed_task_ids"] == ["CJMIN-WEB-001"]
    assert pending["rework_summary"]["gap_tasks"][0]["task_id"] == "CJMIN-WEB-GAP-001"
    assert "task_map.ready" in pending["expected_downstream_events"]
    assert "task_map.amended" in pending["expected_downstream_events"]

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        action_filter={"candidate-rework-apply"},
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.actions_applied == 1
    assert any(
        event.type == RUN_MANAGER_ACTION_APPLIED
        and event.payload.get("candidate_rework_action") == "retrigger"
        for event in events
    )
    assert any(event.type == RUN_MANAGER_ACTION_VERIFY_PASSED for event in events)
    amended = [
        event for event in events
        if event.type == "task_map.amended"
        and event.payload.get("source") == "run_manager_gap_task_map_amend"
    ]
    assert amended
    assert amended[-1].payload["gap_task_ids"] == ["CJMIN-WEB-GAP-001"]
    rework_ready = [
        event for event in events
        if event.type == "task_map.ready"
        and event.payload.get("source") == "run_manager_gap_task_map_amend"
        and event.payload.get("rework_of") == "verify-rm-1"
    ]
    assert rework_ready
    assert (
        rework_ready[-1].payload["rework_summary"]["gap_tasks"][0]["task_id"]
        == "CJMIN-WEB-GAP-001"
    )
    assert any(
        event.type == "task_map.ready"
        and event.payload.get("source") == "run_manager_gap_task_map_amend"
        and event.payload.get("rework_of") == "verify-rm-1"
        for event in events
    )
    amended_ref = rework_ready[-1].payload["task_map_ref"]
    amended_path = state_dir.joinpath(*Path(amended_ref).parts[1:])
    amended_task_map = json.loads(amended_path.read_text(encoding="utf-8"))
    assert [task["task_id"] for task in amended_task_map["tasks"]] == [
        "CJMIN-WEB-001",
        "CJMIN-WEB-GAP-001",
    ]

    second = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        action_filter={"candidate-rework-apply"},
        spawn_repairs=False,
    )
    assert second.actions_applied == 0


def test_run_manager_recovers_candidate_rework_anchor_from_artifact_and_git_head(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    task_map_ref = state_dir / "artifacts" / "CJMIN-RM" / "task_map.json"
    task_map_ref.parent.mkdir(parents=True)
    task_map_ref.write_text('{"tasks": []}\n', encoding="utf-8")
    log.append(ZfEvent(
        id="integration-rm-1",
        type="integration.failed",
        actor="zf-cli",
        payload={"pdd_id": "CJMIN-RM", "reason": "timeout"},
        correlation_id="trace-rm",
    ))

    git_result = MagicMock(returncode=0, stdout="head123\n")
    with patch("zf.runtime.run_manager.subprocess.run", return_value=git_result):
        projection = build_run_manager_projection(
            state_dir,
            events=log.read_all(),
            config=_config(),
        )
        pending = next(
            item for item in projection["pending_actions"]
            if item["action"] == "candidate-rework-apply"
        )
        assert pending["action"] == "candidate-rework-apply"
        assert pending["candidate_rework_action"] == "retrigger"
        assert pending["preflight"]["status"] == "passed"
        assert pending["task_map_ref"] == str(task_map_ref)
        assert pending["source_commit"] == "head123"
        assert pending["candidate_base_commit"] == "head123"

        result = run_manager_tick(
            state_dir=state_dir,
            writer=writer,
            config=_config(),
            event_log=log,
            action_filter={"candidate-rework-apply"},
            spawn_repairs=False,
        )

    events = log.read_all()
    assert result.actions_applied == 1
    rework = [
        event for event in events
        if event.type == "task_map.ready"
        and event.payload.get("source") == "run_manager_candidate_rework"
    ]
    assert rework
    assert rework[-1].payload.get("rework_of") == "integration-rm-1"
    assert rework[-1].payload.get("task_map_ref") == str(task_map_ref)
    assert rework[-1].payload.get("source_commit") == "head123"
    assert rework[-1].payload.get("candidate_base_commit") == "head123"


def test_run_manager_suppresses_candidate_rework_after_clean_parity_closure(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        id="rq1",
        type="orchestrator.replan_requested",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R3",
            "rework_of": "cx1",
            "rework_source": "fanout.cancelled",
            "rework_attempt": 1,
        },
        correlation_id="trace-r3",
    ))
    log.append(ZfEvent(
        id="rq2",
        type="orchestrator.replan_requested",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R3",
            "rework_of": "cx2",
            "rework_source": "fanout.cancelled",
            "rework_attempt": 2,
        },
        correlation_id="trace-r3",
    ))
    log.append(ZfEvent(
        id="cx3",
        type="fanout.cancelled",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R3",
            "trace_id": "trace-r3",
            "candidate_ref": "cand/CANGJIE-R3",
            "reason": (
                "writer fanout task_map has overlapping allowed paths "
                "'packages/core/**' and 'packages/core/src/agent-loop.ts'"
            ),
        },
        correlation_id="trace-r3",
    ))
    log.append(ZfEvent(
        id="parity-clean",
        type="module.parity.scan.completed",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R3",
            "trace_id": "trace-r3",
            "candidate_ref": "cand/CANGJIE-R3",
            "open_p0_p1_gap_count": 0,
        },
        correlation_id="trace-r3",
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_admission_replan_config(),
    )

    assert not any(
        action.get("action") == "candidate-rework-apply"
        for action in projection["pending_actions"]
    )
    assert not any(
        action.get("source_event_id") == "cx3"
        for action in projection["pending_actions"]
    )


def test_run_manager_transition_event_is_known_type() -> None:
    assert RUN_MANAGER_TRANSITION in KNOWN_EVENT_TYPES


def test_run_manager_projection_writes_pane_snapshot_and_monitor(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
        project_root=tmp_path,
    )

    write_run_manager_projections(state_dir, projection)

    assert projection["runtime_pane_snapshot"]["is_derived_projection"] is True
    assert projection["run_manager_monitor"]["schema_version"] == "run-manager.monitor.v1"
    pane_path = state_dir / "projections" / "runtime_pane_snapshot.json"
    monitor_path = state_dir / "projections" / "run_manager_monitor.json"
    assert pane_path.exists()
    assert monitor_path.exists()
    monitor = json.loads(monitor_path.read_text(encoding="utf-8"))
    assert monitor["refs"]["runtime_pane_snapshot"] == "projections/runtime_pane_snapshot.json"
    assert "pending_actions" in monitor["summary"]


def test_run_manager_projection_attaches_spine_summary(tmp_path: Path) -> None:
    state_dir, log, _writer = _state(tmp_path)
    _append_repair_failed_children_fixture(log)
    projections_dir = state_dir / "projections"
    projections_dir.mkdir(parents=True, exist_ok=True)
    (projections_dir / "workflow_health.json").write_text(
        json.dumps({"counters": {"human.escalate": 2}, "last_event_ts": "t1"}),
        encoding="utf-8",
    )
    (projections_dir / "workflow_spine.json").write_text(
        json.dumps({"runs": {"PDD-1": {"milestones": 3, "attention": True}}}),
        encoding="utf-8",
    )
    (projections_dir / "task_attempts.json").write_text(
        json.dumps({
            "schema_version": "shadow-spine.v1",
            "tasks": {
                "CJMIN-ASSEMBLY-001": {
                    "attempt_count": 2,
                    "current_owner": "dev-lane-1",
                    "latest_attempt_key": "attempt-2",
                    "latest_state": "failed",
                    "lease_state": "released",
                    "last_terminal": "dev.failed",
                    "open_attempts": 0,
                    "counted_failures": 1,
                    "attempts": [{
                        "attempt_key": "attempt-2",
                        "state": "failed",
                        "lease_state": "released",
                        "role": "dev-lane-1",
                    }],
                }
            },
        }),
        encoding="utf-8",
    )
    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
        project_root=tmp_path,
    )
    write_run_manager_projections(state_dir, projection)

    written = json.loads(
        (projections_dir / "run_manager.json").read_text(encoding="utf-8"),
    )
    assert written["spine_summary"]["counters"]["human.escalate"] == 2
    assert written["spine_summary"]["runs"]["PDD-1"]["attention"] is True
    attempt_context = written["spine_summary"]["task_attempts"]["tasks"]["CJMIN-ASSEMBLY-001"]
    assert attempt_context["latest_state"] == "failed"
    pending = written["status_explain"]["pending_actions"][0]
    assert pending["attempt_contexts"][0]["latest_attempt_key"] == "attempt-2"
    assert (
        pending["attempt_contexts"][0]["source_ref"]
        == "projections/task_attempts.json#tasks.CJMIN-ASSEMBLY-001"
    )


def test_run_manager_projection_adds_expired_attempt_recovery_action(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    projections_dir = state_dir / "projections"
    projections_dir.mkdir(parents=True, exist_ok=True)
    (projections_dir / "task_attempts.json").write_text(
        json.dumps({
            "schema_version": "shadow-spine.v1",
            "tasks": {
                "TASK-LEASE": {
                    "latest_state": "running",
                    "current_owner": "dev-lane-1",
                    "open_attempts": 1,
                    "counted_failures": 0,
                    "attempts": [{
                        "attempt_key": "attempt-lease",
                        "state": "running",
                        "role": "dev-lane-1",
                        "started_ts": "2000-01-01T00:00:00+00:00",
                        "source_event_id": "evt-attempt-start",
                        "lease_token": "lease-1",
                        "lease_state": "held",
                        "terminal": None,
                    }],
                },
            },
        }),
        encoding="utf-8",
    )

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
        project_root=tmp_path,
    )

    pending = projection["pending_actions"][0]
    assert pending["action"] == "worker-lifecycle-recover"
    assert pending["safe_resume_action"] == "worker_lifecycle_recover"
    assert pending["task_id"] == "TASK-LEASE"
    assert pending["policy_decision"]["decision"] == "auto_decide"
    assert pending["attempt_context"]["latest_attempt_key"] == "attempt-lease"
    assert pending["source_refs"] == [
        "projections/task_attempts.json#tasks.TASK-LEASE",
    ]


def test_run_manager_projection_adds_failed_attempt_diagnosis_action(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    projections_dir = state_dir / "projections"
    projections_dir.mkdir(parents=True, exist_ok=True)
    (projections_dir / "task_attempts.json").write_text(
        json.dumps({
            "schema_version": "shadow-spine.v1",
            "tasks": {
                "TASK-FAILED": {
                    "latest_state": "failed",
                    "current_owner": "dev-lane-1",
                    "open_attempts": 0,
                    "counted_failures": 1,
                    "attempts": [{
                        "attempt_key": "attempt-failed",
                        "state": "failed",
                        "role": "dev-lane-1",
                        "source_event_id": "evt-attempt-start",
                        "failure_signature": "task_attempt_failed",
                        "retryable": True,
                        "terminal": {
                            "type": "task.attempt.failed",
                            "event_id": "evt-attempt-failed",
                        },
                    }],
                },
            },
        }),
        encoding="utf-8",
    )

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
        project_root=tmp_path,
    )

    pending = projection["pending_actions"][0]
    assert pending["action"] == "diagnose-attention"
    assert pending["safe_resume_action"] == "diagnose_attention"
    assert pending["task_id"] == "TASK-FAILED"
    assert pending["policy_decision"]["decision"] == "needs_diagnosis"
    assert pending["attempt_context"]["latest_attempt_key"] == "attempt-failed"


def test_run_manager_unknown_gap_fallback_for_all_missing_panes(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    (state_dir / "session.yaml").write_text("runtime_state: active\n", encoding="utf-8")
    missing_probe = {
        "schema_version": "runtime.pane_probe.v0",
        "is_derived_projection": True,
        "enabled": True,
        "summary": {
            "expected": 2,
            "observed": 0,
            "missing": 2,
            "mismatch": 0,
            "by_status": {"pane_missing": 2},
        },
        "panes": [],
    }

    with patch("zf.runtime.run_manager.build_runtime_pane_probe", return_value=missing_probe):
        projection = build_run_manager_projection(
            state_dir,
            events=log.read_all(),
            config=_config(),
            project_root=tmp_path,
        )

    pending = projection["pending_actions"][0]
    assert pending["action"] == "diagnose-attention"
    assert pending["failure_class"] == "unknown_runtime_gap"
    assert pending["kind"] == "all_panes_missing"
    assert pending["policy_decision"]["decision"] == "needs_diagnosis"
    assert pending["source_ref"] == "projections/runtime_pane_snapshot.json"
    assert "restart_missing_tmux_workers_if_safe" in pending["recommended_actions"]

    with patch("zf.runtime.run_manager.build_runtime_pane_probe", return_value=missing_probe):
        result = run_manager_tick(
            state_dir=state_dir,
            writer=writer,
            config=_config(),
            project_root=tmp_path,
            event_log=log,
            spawn_repairs=False,
        )

    events = log.read_all()
    assert result.autoresearch_requested == 1
    requested = [
        event for event in events
        if event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
    ]
    assert len(requested) == 1
    assert requested[0].payload["failure_class"] == "unknown_runtime_gap"
    assert requested[0].payload["source_ref"] == "projections/runtime_pane_snapshot.json"


def test_run_manager_unknown_gap_fallback_for_active_run_without_events(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    (state_dir / "session.yaml").write_text("runtime_state: active\n", encoding="utf-8")
    observed_probe = {
        "schema_version": "runtime.pane_probe.v0",
        "is_derived_projection": True,
        "enabled": True,
        "summary": {
            "expected": 2,
            "observed": 2,
            "missing": 0,
            "mismatch": 0,
            "by_status": {"observed": 2},
        },
        "panes": [],
    }

    with patch("zf.runtime.run_manager.build_runtime_pane_probe", return_value=observed_probe):
        projection = build_run_manager_projection(
            state_dir,
            events=log.read_all(),
            config=_config(),
            project_root=tmp_path,
        )

    pending = projection["pending_actions"][0]
    assert pending["failure_class"] == "unknown_runtime_gap"
    assert pending["kind"] == "active_run_no_events"
    assert pending["source_ref"] == "events.jsonl"
    assert "verify_entry_trigger_was_emitted" in pending["recommended_actions"]


def test_run_manager_unknown_gap_diagnoses_tripped_no_progress(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    for index in range(3):
        log.append(ZfEvent(
            id=f"rm-action-failed-{index}",
            type=RUN_MANAGER_ACTION_FAILED,
            actor="run-manager",
            payload={
                "checkpoint_id": "missing-terminal-event",
                "reason": "expected downstream event was not observed",
            },
        ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
        project_root=tmp_path,
    )
    pending = projection["pending_actions"][0]
    assert pending["failure_class"] == "no_progress_breaker"
    assert pending["kind"] == "no_progress_breaker"
    assert pending["source_event_ids"][-1] == "rm-action-failed-2"

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        project_root=tmp_path,
        event_log=log,
        spawn_repairs=False,
    )
    second = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        project_root=tmp_path,
        event_log=log,
        spawn_repairs=False,
    )

    requests = [
        event for event in log.read_all()
        if event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
    ]
    assert result.autoresearch_requested == 1
    assert second.autoresearch_requested == 0
    assert len(requests) == 1
    assert requests[0].payload["failure_class"] == "no_progress_breaker"


def test_run_manager_unknown_gap_does_not_fire_for_cold_idle_project(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    missing_probe = {
        "schema_version": "runtime.pane_probe.v0",
        "is_derived_projection": True,
        "enabled": True,
        "summary": {
            "expected": 2,
            "observed": 0,
            "missing": 2,
            "mismatch": 0,
            "by_status": {"pane_missing": 2},
        },
        "panes": [],
    }

    with patch("zf.runtime.run_manager.build_runtime_pane_probe", return_value=missing_probe):
        projection = build_run_manager_projection(
            state_dir,
            events=log.read_all(),
            config=_config(),
            project_root=tmp_path,
        )

    assert projection["pending_actions"] == []


def test_run_manager_invokes_autoresearch_for_unknown_batch_recovery(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    _append_unknown_batch_fixture(log)
    initial = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_lane_resume_config(),
    )
    pending = initial["pending_actions"][0]
    assert pending["failure_class"] == "unknown_complex"
    assert pending["owner_route"] == "run_manager"
    assert pending["action_policy"] == "needs_diagnosis"
    assert pending["policy_decision"]["decision"] == "needs_diagnosis"

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_lane_resume_config(),
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.autoresearch_requested == 1
    requested = [event for event in events if event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED]
    assert len(requested) == 1
    assert requested[0].actor == "run-manager"
    assert requested[0].payload["owner_route"] == "run_manager"
    assert requested[0].payload["apply_policy"] == "proposal_only"
    context_ref = requested[0].payload["context_ref"]
    assert context_ref["ref_schema_version"] == "sidecar-ref.v1"
    assert context_ref["required"] is True
    assert requested[0].payload["legacy_context_ref"] == "projections/run_manager.json#run_context_bundle"
    hydrated = hydrate_sidecar_ref(state_dir, context_ref)
    assert hydrated.payload["legacy_context_ref"] == "projections/run_manager.json#run_context_bundle"
    assert requested[0].payload["read_set_ref"]["ref_schema_version"] == "sidecar-ref.v1"
    assert any(
        event.type == RUN_MANAGER_TRANSITION
        and event.payload.get("transition") == "invoke_autoresearch"
        for event in events
    )

    second = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )
    assert second.autoresearch_requested == 0
    assert [
        event.type for event in log.read_all()
        if event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
    ].count(RUN_MANAGER_AUTORESEARCH_REQUESTED) == 1


def test_open_supervisor_attention_becomes_diagnosis_pending_action(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    _write_supervisor_attention(state_dir)

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    pending = projection["pending_actions"][0]
    assert pending["action"] == "diagnose-attention"
    assert pending["safe_resume_action"] == "diagnose_attention"
    assert pending["failure_class"] == "worker_noop_or_terminal_missing"
    assert pending["owner_route"] == "run_manager"
    assert pending["policy_decision"]["decision"] == "needs_diagnosis"
    assert pending["policy_decision"]["executable"] is True
    assert pending["source_event_ids"] == ["evt-stall-1"]
    assert pending["source_ref"] == "events.jsonl#evt-stall-1"
    assert "replay_worker_briefing" in pending["recommended_actions"]
    assert "diagnosis_report" in pending["expected_output"]
    assert projection["status_explain"]["next_auto_action"] == "run_manager_diagnosis"


def test_flow_goal_blocked_becomes_semantic_pending_action(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        id="evt-flow-blocked",
        type="flow.goal.blocked",
        actor="verify",
        payload={"pdd_id": "PDD-1", "reason": "dashboard gap remains"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    pending = next(
        action for action in projection["pending_actions"]
        if action.get("failure_class") == "flow_goal_blocked"
    )
    assert pending["action"] == "diagnose-attention"
    assert pending["suggested_action"]["kind"] == "request_goal_gap_plan"
    assert pending["owner_route"] == "run_manager"
    assert pending["problem_envelope"]["problem_class"] == "product_gap"
    assert pending["source_event_ids"] == ["evt-flow-blocked"]
    assert "flow.gap_plan.ready" in pending["expected_downstream_events"]
    assert "goal.gap_plan.ready" in pending["expected_downstream_events"]
    assert pending["verify_condition"].startswith("expected_downstream_event:")


def test_flow_goal_blocked_superseded_by_gap_plan_ready(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        id="evt-flow-blocked",
        type="flow.goal.blocked",
        actor="verify",
        payload={"pdd_id": "PDD-1", "reason": "dashboard gap remains"},
    ))
    log.append(ZfEvent(
        id="evt-gap-plan-ready",
        type="flow.gap_plan.ready",
        actor="verify",
        payload={"pdd_id": "PDD-1", "gap_plan_ref": "reports/PDD-1/gap-plan.json"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    assert not any(
        action.get("failure_class") == "flow_goal_blocked"
        for action in projection["pending_actions"]
    )


def test_unknown_actionable_event_becomes_pending_diagnostic(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        id="evt-custom-failed",
        type="custom.discovery.failed",
        actor="worker",
        task_id="TASK-CUSTOM",
        payload={"reason": "custom profile failure"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    pending = next(
        action for action in projection["pending_actions"]
        if action.get("failure_class") == "unknown_actionable_event"
    )
    assert pending["action"] == "diagnose-attention"
    assert pending["suggested_action"]["kind"] == "diagnose_unknown_actionable_event"
    assert pending["suggested_action"]["event_type"] == "custom.discovery.failed"
    assert pending["source_event_ids"] == ["evt-custom-failed"]


def test_run_manager_tick_requests_autoresearch_once_for_attention_diagnosis(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    _write_supervisor_attention(state_dir)

    first = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )
    second = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    requests = [
        event for event in log.read_all()
        if event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
    ]
    assert first.autoresearch_requested == 1
    assert second.autoresearch_requested == 0
    assert len(requests) == 1
    assert requests[0].payload["fingerprint"] == "runtime:dispatch.silent_stall:T1"
    assert requests[0].payload["owner_route"] == "run_manager"
    assert requests[0].payload["checkpoint_id"].startswith("attention-diagnosis-")
    assert requests[0].payload["source_event_ids"] == ["evt-stall-1"]
    assert "replay_worker_briefing" in requests[0].payload["recommended_actions"]


def test_run_manager_marks_stale_autoresearch_diagnosis_as_failed(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    _write_supervisor_attention(state_dir)
    initial = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )
    pending = initial["pending_actions"][0]
    log.append(ZfEvent(
        id="evt-rmar-stale",
        type=RUN_MANAGER_AUTORESEARCH_REQUESTED,
        ts="2026-06-01T00:00:00+00:00",
        actor="run-manager",
        correlation_id="rmar-stale-1",
        payload={
            "request_id": "rmar-stale-1",
            "checkpoint_id": pending["checkpoint_id"],
            "fingerprint": pending["fingerprint"],
            "safe_resume_action": "diagnose_attention",
        },
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    failed = [
        event for event in log.read_all()
        if event.type == RUN_MANAGER_ACTION_FAILED
        and event.payload.get("checkpoint_id") == pending["checkpoint_id"]
    ]
    assert result.actions_failed == 1
    assert failed
    assert "autoresearch request became stale" in failed[-1].payload["reason"]


def test_run_manager_does_not_fail_stale_diagnosis_after_run_completed(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    _write_supervisor_attention(state_dir)
    initial = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )
    pending = initial["pending_actions"][0]
    log.append(ZfEvent(
        id="evt-rmar-stale",
        type=RUN_MANAGER_AUTORESEARCH_REQUESTED,
        ts="2026-06-01T00:00:00+00:00",
        actor="run-manager",
        correlation_id="rmar-stale-1",
        payload={
            "request_id": "rmar-stale-1",
            "checkpoint_id": pending["checkpoint_id"],
            "fingerprint": pending["fingerprint"],
            "safe_resume_action": "diagnose_attention",
        },
    ))
    log.append(ZfEvent(
        id="evt-run-completed",
        type="run.completed",
        actor="run-manager",
        payload={"status": "passed", "run_id": "R-ISSUE"},
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    failed = [
        event for event in log.read_all()
        if event.type == RUN_MANAGER_ACTION_FAILED
        and event.payload.get("checkpoint_id") == pending["checkpoint_id"]
    ]
    assert result.actions_failed == 0
    assert failed == []


def _stale_diagnosis_request(log, pending, request_id: str) -> None:
    log.append(ZfEvent(
        id=f"evt-{request_id}",
        type=RUN_MANAGER_AUTORESEARCH_REQUESTED,
        ts="2026-06-01T00:00:00+00:00",
        actor="run-manager",
        payload={
            "request_id": request_id,
            "checkpoint_id": pending["checkpoint_id"],
            "fingerprint": pending["fingerprint"],
            "safe_resume_action": "diagnose_attention",
        },
    ))


def test_stale_diagnosis_resolved_by_loop_skipped(tmp_path: Path) -> None:
    """2026-07-10 E2E: a resident dedupe-skip (autoresearch.loop.skipped) is a
    terminal answer — the request must not burn the stale window and fail
    (PRD: 8 stale action.failed with 1 real loop run)."""
    state_dir, log, writer = _state(tmp_path)
    _write_supervisor_attention(state_dir)
    initial = build_run_manager_projection(
        state_dir, events=log.read_all(), config=_config(),
    )
    pending = initial["pending_actions"][0]
    _stale_diagnosis_request(log, pending, "rmar-skip-1")
    log.append(ZfEvent(
        id="evt-loop-skip",
        type="autoresearch.loop.skipped",
        ts="2026-06-01T00:00:05+00:00",
        actor="zf-autoresearch-resident",
        payload={"loop_request_id": "rmar-skip-1"},
    ))

    result = run_manager_tick(
        state_dir=state_dir, writer=writer, config=_config(),
        event_log=log, spawn_repairs=False,
    )

    assert result.actions_failed == 0


def test_stale_diagnosis_age_anchored_to_running_loop(tmp_path: Path) -> None:
    """2026-07-10 E2E: a matching loop.started re-anchors the stale clock —
    a loop legitimately running past the window is not stale mid-flight."""
    state_dir, log, writer = _state(tmp_path)
    _write_supervisor_attention(state_dir)
    initial = build_run_manager_projection(
        state_dir, events=log.read_all(), config=_config(),
    )
    pending = initial["pending_actions"][0]
    _stale_diagnosis_request(log, pending, "rmar-run-1")
    # emitted via writer → current timestamp → recent liveness
    writer.emit(
        "autoresearch.loop.started",
        actor="zf-autoresearch-resident",
        payload={"loop_request_id": "rmar-run-1"},
    )

    result = run_manager_tick(
        state_dir=state_dir, writer=writer, config=_config(),
        event_log=log, spawn_repairs=False,
    )

    assert result.actions_failed == 0


def test_stale_diagnosis_still_fails_when_started_loop_hangs(tmp_path: Path) -> None:
    """Counterpart: a loop that STARTED long ago and never reached a terminal
    event still goes stale — hang detection survives the liveness anchor."""
    state_dir, log, writer = _state(tmp_path)
    _write_supervisor_attention(state_dir)
    initial = build_run_manager_projection(
        state_dir, events=log.read_all(), config=_config(),
    )
    pending = initial["pending_actions"][0]
    _stale_diagnosis_request(log, pending, "rmar-hang-1")
    log.append(ZfEvent(
        id="evt-loop-hang",
        type="autoresearch.loop.started",
        ts="2026-06-01T00:01:00+00:00",
        actor="zf-autoresearch-resident",
        payload={"loop_request_id": "rmar-hang-1"},
    ))

    result = run_manager_tick(
        state_dir=state_dir, writer=writer, config=_config(),
        event_log=log, spawn_repairs=False,
    )

    assert result.actions_failed == 1


def test_stale_diagnosis_anchored_window_tolerates_queue_wait(
    tmp_path: Path,
) -> None:
    """2026-07-10 R5: a request the resident has ACKed (queued) is not dead,
    just waiting behind a bounded loop — past the tight 300s window it must
    NOT stale; the wide anchored window (resident-died backstop) governs."""
    from datetime import datetime, timedelta, timezone

    state_dir, log, writer = _state(tmp_path)
    _write_supervisor_attention(state_dir)
    initial = build_run_manager_projection(
        state_dir, events=log.read_all(), config=_config(),
    )
    pending = initial["pending_actions"][0]
    _stale_diagnosis_request(log, pending, "rmar-queued-1")
    ten_minutes_ago = (
        datetime.now(timezone.utc) - timedelta(seconds=600)
    ).isoformat()
    log.append(ZfEvent(
        id="evt-loop-queued",
        type="autoresearch.loop.accepted",
        ts=ten_minutes_ago,
        actor="zf-autoresearch-resident",
        payload={"loop_request_id": "rmar-queued-1", "queued": True},
    ))

    result = run_manager_tick(
        state_dir=state_dir, writer=writer, config=_config(),
        event_log=log, spawn_repairs=False,
    )

    # 600s > 300s tight window, but well inside the 3600s anchored window
    assert result.actions_failed == 0


def test_failure_closeout_activation_skipped_after_ship_completed(
    tmp_path: Path,
) -> None:
    """2026-07-10 E2E: a ship.completed later than the materialized manifest
    means the run has delivered — the stale failure closeout must not escalate
    to the owner (PRD shipped yet human.escalate on closeout approval)."""
    state_dir, log, writer = _state(tmp_path)
    manifest = _write_failure_closeout_manifest(tmp_path)
    writer.emit(
        "failure.closeout.materialized",
        actor="run-manager",
        payload={
            "schema_version": "failure-closeout.event.v1",
            "manifest_ref": str(manifest),
            "materialized_count": 1,
        },
    )
    writer.emit(
        "ship.completed",
        actor="zf-cli",
        payload={"pdd_id": "F-1", "target_branch": "main"},
    )

    projection = build_run_manager_projection(
        state_dir, events=log.read_all(), config=_config(), project_root=tmp_path,
    )
    assert not any(
        action.get("action") == "failure-closeout-activate"
        for action in projection["pending_actions"]
    )

    # a manifest materialized AFTER the ship still escalates
    post_ship = manifest.parent / "2026-07-10-0001-post-ship.md"
    post_ship.write_text(manifest.read_text(encoding="utf-8"), encoding="utf-8")
    writer.emit(
        "failure.closeout.materialized",
        actor="run-manager",
        payload={
            "schema_version": "failure-closeout.event.v1",
            "manifest_ref": str(post_ship),
            "materialized_count": 1,
        },
    )
    projection = build_run_manager_projection(
        state_dir, events=log.read_all(), config=_config(), project_root=tmp_path,
    )
    assert any(
        action.get("action") == "failure-closeout-activate"
        for action in projection["pending_actions"]
    )


def test_run_manager_monitor_does_not_report_healthy_when_diagnosis_pending() -> None:
    projection = build_run_manager_monitor_projection(
        completion_profile={"status": "running"},
        monitor={"state": "healthy_waiting"},
        status_explain={
            "blocking": False,
            "wait_reason": "diagnosis_required",
            "next_auto_action": "run_manager_diagnosis",
        },
        pending_actions=[{
            "action": "diagnose-attention",
            "safe_resume_action": "diagnose_attention",
            "policy_decision": {"decision": "needs_diagnosis"},
            "owner_route": "run_manager",
        }],
        no_progress={"status": "tripped"},
        advisor={"summary": {}},
        wait_hints={"summary": {}},
        resident_agent={"status": "observing"},
        runtime_pane_snapshot={"summary": {}},
    )

    assert projection["monitor_state"] == "diagnosis_pending"
    assert projection["summary"]["no_progress_status"] == "tripped"


def test_attention_diagnosis_with_resident_does_not_fake_recovery(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    briefing = state_dir / "briefings" / "run-manager-resident.md"
    briefing.parent.mkdir(parents=True)
    briefing.write_text("observe this run", encoding="utf-8")
    _write_supervisor_attention(state_dir)
    log.append(ZfEvent(
        type="run.manager.resident.spawned",
        id="evt-resident-spawned",
        actor="zf-cli",
        payload={
            "ready": True,
            "session_mode": "dedicated",
            "tmux_session": "zf-rm",
        },
    ))
    log.append(ZfEvent(
        type="run.manager.resident.prompted",
        id="evt-resident-prompted",
        actor="zf-cli",
        payload={"prompted": True, "briefing_path": str(briefing)},
    ))
    log.append(ZfEvent(
        type="run.manager.agent.observation",
        id="evt-resident-observed",
        actor="run-manager",
        payload={"status": "watching"},
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        project_root=tmp_path,
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    requests = [
        event for event in events
        if event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
    ]
    reprompts = [
        event for event in events
        if event.type == "run.manager.resident.prompted"
        and event.payload.get("reprompt") is True
    ]
    applied = [
        event for event in events
        if event.type == RUN_MANAGER_ACTION_APPLIED
        and event.payload.get("safe_resume_action") == "resident_agent_reprompt"
    ]

    assert result.actions_applied == 0
    assert result.autoresearch_requested == 1
    assert len(requests) == 1
    assert requests[0].payload["failure_class"] == "worker_noop_or_terminal_missing"
    assert requests[0].payload["fingerprint"] == "runtime:dispatch.silent_stall:T1"
    assert not reprompts
    assert not applied


def test_run_manager_invokes_reflect_before_autoresearch_for_diagnosis(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    _write_supervisor_attention(state_dir)
    prompts: list[str] = []

    def fake_reflect(prompt: str, **_kwargs: object) -> ReflectionResult:
        prompts.append(prompt)
        return ReflectionResult(
            verdict="better_fix_exists",
            alternatives=["inspect terminal ledger before rework"],
            risk="medium",
            rec_for_next_iter="run autoresearch with terminal ledger evidence",
            raw_response='{"verdict":"better_fix_exists"}',
        )

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_reflect_config(),
        project_root=tmp_path,
        event_log=log,
        spawn_repairs=False,
        reflect_fn=fake_reflect,
    )

    events = log.read_all()
    requested = [event for event in events if event.type == RUN_MANAGER_REFLECT_REQUESTED]
    completed = [event for event in events if event.type == RUN_MANAGER_REFLECT_COMPLETED]
    assert result.reflect_requested == 1
    assert result.reflect_completed == 1
    assert result.autoresearch_requested == 1
    assert len(requested) == 1
    assert len(completed) == 1
    assert requested[0].payload["checkpoint_id"].startswith("attention-diagnosis-")
    assert requested[0].payload["context_ref"]["ref_schema_version"] == "sidecar-ref.v1"
    assert requested[0].payload["context_ref"]["required"] is True
    assert requested[0].payload["read_set_ref"]["ref_schema_version"] == "sidecar-ref.v1"
    assert completed[0].payload["context_ref"]["ref_schema_version"] == "sidecar-ref.v1"
    assert completed[0].payload["legacy_context_ref"] == "projections/run_manager.json#run_context_bundle"
    assert completed[0].payload["recommended_route"] == "autoresearch"
    assert completed[0].payload["alternatives"] == [
        "inspect terminal ledger before rework",
    ]
    assert "Current Focus" not in prompts[0]
    assert "Run Manager" in prompts[0]


def test_run_manager_consumes_agent_recommendation_to_autoresearch(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        type="run.manager.agent.recommendation",
        id="evt-rec-1",
        actor="run-manager",
        payload={
            "recommended_route": "autoresearch",
            "checkpoint_id": "ck-rec-1",
            "fingerprint": "resident:ck-rec-1",
            "summary": "worker terminal missing",
        },
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.agent_recommendations_consumed == 1
    assert result.autoresearch_requested == 1
    assert any(event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED for event in events)
    consumed = [
        event for event in events
        if event.type == RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED
    ]
    assert len(consumed) == 1
    assert consumed[0].payload["route"] == "autoresearch"
    assert consumed[0].payload["source_event_id"] == "evt-rec-1"


def test_run_manager_consumes_agent_controlled_action_to_resident_reprompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    briefing = state_dir / "briefings" / "run-manager.md"
    briefing.parent.mkdir(parents=True)
    briefing.write_text("observe the run", encoding="utf-8")
    log.append(ZfEvent(
        type="run.manager.agent.recommendation",
        id="evt-rec-controlled-reprompt",
        actor="run-manager",
        payload={
            "recommended_route": "controlled_action",
            "safe_resume_action": "resident_agent_reprompt",
            "checkpoint_id": "ck-reprompt-1",
            "fingerprint": "resident:ck-reprompt-1",
            "tmux_session": "zf-rm",
            "instance_id": "run-manager",
            "briefing_path": str(briefing),
            "summary": "resident agent should continue observing the run",
        },
    ))
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["tmux", "display-message", "-p"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=f"%9\tcodex\t{tmp_path}\t0\n",
                stderr="",
            )
        if args[:2] == ["tmux", "send-keys"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="unexpected")

    monkeypatch.setattr("zf.runtime.run_manager.subprocess.run", fake_run)
    monkeypatch.setattr("zf.runtime.run_manager.time.sleep", lambda _seconds: None)

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.agent_recommendations_consumed == 1
    assert result.actions_applied == 1
    assert any(call[:2] == ["tmux", "send-keys"] for call in calls)
    reprompts = [
        event for event in events
        if event.type == "run.manager.resident.prompted"
        and event.payload.get("reprompt") is True
    ]
    assert reprompts
    consumed = [
        event for event in events
        if event.type == RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED
    ][-1]
    assert consumed.payload["route"] == "controlled_action"
    assert consumed.payload["status"] == "consumed"
    assert consumed.payload["downstream_event_ids"]


def test_run_manager_controlled_action_diagnosis_requests_autoresearch(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        type="run.manager.agent.recommendation",
        id="evt-rec-controlled-diagnosis",
        actor="run-manager",
        payload={
            "recommended_route": "controlled_action",
            "safe_resume_action": "diagnose_attention",
            "checkpoint_id": "ck-diagnose-1",
            "fingerprint": "resident:ck-diagnose-1",
            "summary": "worker no-op needs diagnosis",
        },
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.agent_recommendations_consumed == 1
    assert result.autoresearch_requested == 1
    assert any(event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED for event in events)
    consumed = [
        event for event in events
        if event.type == RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED
    ][-1]
    assert consumed.payload["route"] == "controlled_action"
    assert consumed.payload["status"] == "consumed"
    assert consumed.payload["reason"] == "controlled_action_requires_diagnosis"


def test_run_manager_consumes_agent_recommendation_to_reflect(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        type="run.manager.agent.recommendation",
        id="evt-rec-reflect",
        actor="run-manager",
        payload={
            "recommended_route": "reflect",
            "checkpoint_id": "ck-reflect-1",
            "fingerprint": "resident:ck-reflect-1",
            "summary": "same checkpoint failed twice",
        },
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_reflect_config(),
        project_root=tmp_path,
        event_log=log,
        spawn_repairs=False,
        reflect_fn=lambda _prompt, **_kwargs: ReflectionResult(
            verdict="unknown",
            alternatives=[],
            risk="medium",
            rec_for_next_iter="inspect supervisor snapshot",
            raw_response='{"verdict":"unknown"}',
        ),
    )

    events = log.read_all()
    assert result.agent_recommendations_consumed == 1
    assert result.reflect_requested == 1
    assert result.reflect_completed == 1
    assert any(event.type == RUN_MANAGER_REFLECT_COMPLETED for event in events)
    consumed = [
        event for event in events
        if event.type == RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED
    ]
    assert consumed[0].payload["route"] == "reflect"


def test_run_manager_worker_lifecycle_recovery_requests_respawn(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    (state_dir / "role_sessions.yaml").write_text(
        "project_root: /tmp/project\n"
        "roles:\n"
        "  dev-lane-0: 11111111-1111-1111-1111-111111111111\n"
        "instance_meta:\n"
        "  dev-lane-0:\n"
        "    pid: not-a-pid\n"
        "    last_heartbeat_payload:\n"
        "      current_task_id: CJMIN-GATEWAY-001\n"
        "      briefing_ref: .zf/briefings/dev-lane-0.md\n",
        encoding="utf-8",
    )

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )
    pending = projection["pending_actions"][0]
    assert pending["action"] == "worker-lifecycle-recover"
    assert pending["policy_decision"]["decision"] == "auto_decide"
    assert pending["task_id"] == "CJMIN-GATEWAY-001"

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_lane_resume_config(),
        event_log=log,
        action_filter={"worker-lifecycle-recover"},
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.actions_applied == 1
    requested = [
        event for event in events
        if event.type == "worker.respawn.requested"
    ]
    assert requested
    assert requested[-1].payload["instance_id"] == "dev-lane-0"
    assert requested[-1].task_id == "CJMIN-GATEWAY-001"
    assert any(event.type == RUN_MANAGER_ACTION_VERIFY_PASSED for event in events)


def test_run_manager_worker_lifecycle_respawn_request_reaches_orchestrator(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    (state_dir / "role_sessions.yaml").write_text(
        "project_root: /tmp/project\n"
        "roles:\n"
        "  dev-lane-0: 11111111-1111-1111-1111-111111111111\n"
        "instance_meta:\n"
        "  dev-lane-0:\n"
        "    pid: not-a-pid\n"
        "    last_heartbeat_payload:\n"
        "      current_task_id: CJMIN-GATEWAY-001\n"
        "      briefing_ref: .zf/briefings/dev-lane-0.md\n",
        encoding="utf-8",
    )

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_lane_resume_config(),
        event_log=log,
        action_filter={"worker-lifecycle-recover"},
        spawn_repairs=False,
    )
    assert result.actions_applied == 1

    cfg = ZfConfig(
        project=ProjectConfig(name="worker-lifecycle-test"),
        roles=[
            RoleConfig(
                name="dev-lane-0",
                instance_id="dev-lane-0",
                backend="mock",
            ),
        ],
    )
    orch = Orchestrator(state_dir, cfg, _RespawnRecordingTransport())  # type: ignore[arg-type]
    calls: list[str] = []

    def _respawn(role):  # noqa: ANN001
        calls.append(role.instance_id)
        return OrchestratorDecision(
            action="respawn",
            role=role.instance_id,
            reason="respawned",
        )

    orch._respawn_instance = _respawn  # type: ignore[method-assign]
    orch.run_once(events=[])

    events = log.read_all()
    assert calls == ["dev-lane-0"]
    assert any(
        event.type == "worker.respawn.completed"
        and event.actor == "dev-lane-0"
        for event in events
    )


def test_run_manager_worker_lifecycle_missing_ownership_routes_to_diagnosis(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    (state_dir / "role_sessions.yaml").write_text(
        "project_root: /tmp/project\n"
        "roles:\n"
        "  dev-lane-0: 11111111-1111-1111-1111-111111111111\n"
        "instance_meta:\n"
        "  dev-lane-0:\n"
        "    pid: not-a-pid\n",
        encoding="utf-8",
    )

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )
    pending = projection["pending_actions"][0]
    assert pending["action"] == "worker-lifecycle-recover"
    assert pending["policy_decision"]["decision"] == "needs_diagnosis"
    assert pending["preflight"]["failures"] == ["missing_worker_ownership_evidence"]

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        action_filter={"worker-lifecycle-recover"},
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.actions_applied == 0
    assert result.autoresearch_requested == 1
    assert not [event for event in events if event.type == "worker.respawn.requested"]


def test_run_manager_autonomous_recovery_drill_covers_task_resume_and_worker_recover(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="CJMIN-PROVIDER-001",
        title="provider",
        status="in_progress",
        assigned_to="dev-lane-0",
    ))
    (state_dir / "role_sessions.yaml").write_text(
        "project_root: /tmp/project\n"
        "roles:\n"
        "  dev-lane-0: 11111111-1111-1111-1111-111111111111\n"
        "instance_meta:\n"
        "  dev-lane-0:\n"
        "    pid: not-a-pid\n"
        "    last_heartbeat_payload:\n"
        "      current_task_id: CJMIN-PROVIDER-001\n"
        "      briefing_ref: .zf/briefings/dev-lane-0.md\n",
        encoding="utf-8",
    )
    dev_done = ZfEvent(
        id="dev-done-drill-1",
        type="dev.build.done",
        actor="dev-lane-0",
        task_id="CJMIN-PROVIDER-001",
        payload={"dispatch_id": "disp-dev"},
    )
    gate = ZfEvent(
        id="gate-drill-1",
        type="static_gate.passed",
        actor="zf-cli",
        task_id="CJMIN-PROVIDER-001",
        payload={"trigger_event_id": dev_done.id},
    )
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="CJMIN-PROVIDER-001",
        payload={"assignee": "dev-lane-0", "dispatch_id": "disp-dev"},
    ))
    log.append(dev_done)
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="CJMIN-PROVIDER-001",
        payload={"trigger_event_id": dev_done.id},
    ))
    log.append(gate)

    initial = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_lane_resume_config(),
    )
    assert {item["action"] for item in initial["pending_actions"]} == {
        "workflow-task-resume",
        "worker-lifecycle-recover",
    }

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_lane_resume_config(),
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.actions_applied == 2
    assert any(
        event.type == "task.dispatched"
        and event.payload.get("source") == "workflow_resume"
        for event in events
    )
    assert any(event.type == "worker.respawn.requested" for event in events)
    assert [
        event.type for event in events
        if event.type == RUN_MANAGER_ACTION_VERIFY_PASSED
    ].count(RUN_MANAGER_ACTION_VERIFY_PASSED) == 2
    assert not [event for event in events if event.type == "human.escalate"]


def test_run_manager_diagnoses_trigger_rework_without_mutation_support(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    _append_trigger_rework_fixture(log)
    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )
    pending = projection["pending_actions"][0]
    assert pending["failure_class"] == "task_map_drift"
    assert pending["owner_route"] == "run_manager"
    assert pending["action_policy"] == "needs_diagnosis"
    assert pending["intervention_class"] == "semantic_replan"
    assert pending["task_map_ref"] == ".zf/artifacts/CJMIN-RM/task_map.json"
    assert pending["source_commit"] == "base123"
    assert pending["candidate_base_commit"] == "base123"
    assert pending["source_event_id"]
    assert pending["source_event_type"] == "fanout.aggregate.completed"
    assert pending["preflight"]["mutating_resume_supported"] is False
    assert pending["policy_decision"]["intervention_class"] == "diagnose"
    assert projection["status_explain"]["intervention_class"] == "diagnose"
    assert projection["status_explain"]["wait_reason"] == "diagnosis_required"

    decision = decide_action_policy(
        action="workflow-batch-resume",
        payload=pending,
        mutating_resume_supported=False,
    )
    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert decision["decision"] == "needs_diagnosis"
    assert result.autoresearch_requested == 1
    assert not any(event.type == RUN_MANAGER_ACTION_BLOCKED for event in events)
    assert not any(event.type == HUMAN_ESCALATION_SENT for event in events)


def test_run_manager_diagnosis_does_not_notify_owner_before_exhaustion(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    _append_trigger_rework_fixture(log)

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.autoresearch_requested == 1
    assert any(
        event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
        and event.actor == "run-manager"
        for event in events
    )
    assert not any(event.type == "human.escalate" for event in events)
    assert not any(
        event.type == "owner.visible_message.requested" for event in events
    )


def test_run_manager_side_human_escalation_is_not_run_blocker(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        id="side-human",
        type="human.escalate",
        actor="run-manager",
        payload={
            "decision_token": "side-token",
            "reason": "failure closeout side gate",
            "blocking_scope": "side",
            "failure_class": "failure_closeout_activation",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    completion = projection["completion_profile"]
    assert completion["pending_human_decisions"]
    assert completion["blocking_human_decisions"] == []
    assert "pending_human_decision" not in completion["blockers"]
    assert projection["monitor"]["state"] == "healthy_waiting"
    assert projection["status_explain"]["blocking"] is False
    assert projection["status_explain"]["wait_reason"] == "side_decision_pending"


def test_run_completed_supersedes_side_human_escalation(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        id="side-human",
        type="human.escalate",
        actor="run-manager",
        payload={
            "decision_token": "side-token",
            "reason": "failure closeout side gate",
            "blocking_scope": "side",
            "failure_class": "failure_closeout_activation",
        },
    ))
    log.append(ZfEvent(
        id="run-completed",
        type="run.completed",
        actor="run-manager",
        payload={"status": "passed", "run_id": "R-ISSUE"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    assert projection["completion_profile"]["status"] == "complete"
    assert projection["completion_profile"]["pending_human_decisions"] == []
    assert projection["status_explain"]["wait_reason"] == "complete"


def test_run_manager_diagnosis_request_is_idempotent(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    _append_trigger_rework_fixture(log)
    first = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )
    assert first.autoresearch_requested == 1
    second = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    requests = [
        event for event in log.read_all()
        if event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
    ]
    assert second.autoresearch_requested == 0
    assert len(requests) == 1
    assert not any(event.type == "human.escalate" for event in log.read_all())


def test_run_has_recent_worker_activity_gates_on_idleness() -> None:
    """T1 (2026-07-08 E2E): only silence past the grace window counts as
    stalled — recent worker activity means the run is progressing and
    failure-closeout escalation must defer."""
    from zf.runtime.run_manager import _run_has_recent_worker_activity
    from zf.core.events.model import ZfEvent

    # worker active 30s before the latest event → progressing → defer
    progressing = [
        ZfEvent(type="failure.closeout.materialized", actor="run-manager", ts="2026-07-08T23:00:00Z"),
        ZfEvent(type="agent.usage", actor="dev-lane-0", ts="2026-07-08T23:04:30Z"),
        ZfEvent(type="run.manager.tick.completed", actor="run-manager", ts="2026-07-08T23:05:00Z"),
    ]
    assert _run_has_recent_worker_activity(progressing) is True

    # worker silent 10min while only infra churns → genuinely idle → escalate
    idle = [
        ZfEvent(type="agent.usage", actor="dev-lane-0", ts="2026-07-08T23:00:00Z"),
        ZfEvent(type="run.manager.tick.completed", actor="run-manager", ts="2026-07-08T23:10:00Z"),
    ]
    assert _run_has_recent_worker_activity(idle) is False

    # no worker events at all → do not block escalation
    infra_only = [
        ZfEvent(type="failure.closeout.materialized", actor="run-manager", ts="2026-07-08T23:00:00Z"),
    ]
    assert _run_has_recent_worker_activity(infra_only) is False


def test_failure_closeout_activation_deferred_while_worker_active(
    tmp_path: Path,
) -> None:
    """A worker emitting events (mid-turn) must defer the owner escalation —
    the run is alive, not stalled (E2E false-escalation regression)."""
    state_dir, log, writer = _state(tmp_path)
    manifest = _write_failure_closeout_manifest(tmp_path)
    writer.emit(
        "failure.closeout.materialized",
        actor="run-manager",
        payload={
            "schema_version": "failure-closeout.event.v1",
            "manifest_ref": str(manifest),
            "materialized_count": 1,
        },
    )
    # a lane worker is actively producing (same tick window) → not stalled
    writer.emit("agent.usage", actor="dev-lane-0", payload={"instance_id": "dev-lane-0"})

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
        project_root=tmp_path,
    )
    assert not any(
        action.get("action") == "failure-closeout-activate"
        for action in projection["pending_actions"]
    )


def test_run_manager_projects_failure_closeout_activation_for_owner_approval(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    manifest = _write_failure_closeout_manifest(tmp_path)
    materialized = writer.emit(
        "failure.closeout.materialized",
        actor="run-manager",
        payload={
            "schema_version": "failure-closeout.event.v1",
            "manifest_ref": str(manifest),
            "materialized_count": 1,
        },
    )

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
        project_root=tmp_path,
    )

    pending = projection["pending_actions"][0]
    assert pending["action"] == "failure-closeout-activate"
    assert pending["manifest_ref"] == str(manifest)
    assert pending["source_event_ids"] == [materialized.id]
    assert pending["policy_decision"]["decision"] == "needs_approval"
    assert pending["preflight"]["status"] == "passed"
    assert "failure.closeout.activated" in pending["verify_condition"]


def test_run_completed_suppresses_failure_closeout_activation_action(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    manifest = _write_failure_closeout_manifest(tmp_path)
    writer.emit(
        "failure.closeout.materialized",
        actor="run-manager",
        payload={
            "schema_version": "failure-closeout.event.v1",
            "manifest_ref": str(manifest),
            "materialized_count": 1,
        },
    )
    log.append(ZfEvent(
        type="run.completed",
        actor="run-manager",
        payload={"status": "passed", "run_id": "R-ISSUE"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
        project_root=tmp_path,
    )

    assert not [
        action for action in projection["pending_actions"]
        if action.get("action") == "failure-closeout-activate"
    ]
    assert projection["completion_profile"]["status"] == "complete"


def test_run_manager_approval_activates_failure_closeout_backlog(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    manifest = _write_failure_closeout_manifest(tmp_path)
    writer.emit(
        "failure.closeout.materialized",
        actor="run-manager",
        payload={
            "schema_version": "failure-closeout.event.v1",
            "manifest_ref": str(manifest),
            "materialized_count": 1,
        },
    )

    first = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        project_root=tmp_path,
        event_log=log,
        spawn_repairs=False,
    )
    assert first.actions_blocked == 1
    escalation = [
        event for event in log.read_all()
        if event.type == "human.escalate"
    ][-1]
    assert escalation.payload["action"] == "failure-closeout-activate"
    assert escalation.payload["manifest_ref"] == str(manifest)

    writer.emit(
        "human.escalation.acknowledged",
        actor="operator",
        payload={
            "decision_token": escalation.payload["decision_token"],
            "decision": "approve_controlled_action",
        },
    )
    second = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        project_root=tmp_path,
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert second.human_decisions_applied == 1
    assert any(event.type == "failure.closeout.activated" for event in events)
    assert any(
        event.type == RUN_MANAGER_ACTION_VERIFY_PASSED
        and event.payload.get("safe_resume_action") == "failure_closeout_activate"
        for event in events
    )
    active_tasks = list((tmp_path / "tasks" / "active").glob("*.md"))
    assert len(active_tasks) == 1
    assert "> 状态: active" in active_tasks[0].read_text(encoding="utf-8")


def test_run_manager_routes_rework_exhausted_human_gate_to_repair_resume(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(Task(
        id="CANGJIE-PARITY-GAP-001",
        title="Fix parity gap",
        status="in_progress",
        assigned_to="orchestrator",
    ))
    log.append(ZfEvent(
        type="verify.failed",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R3",
            "target_ref": "cand/CANGJIE-R3",
            "findings": [{
                "category": "stage_success_criteria",
                "message": (
                    "artifact matrix gate failed: required artifact "
                    "'pnpm-workspace.yaml' is missing"
                ),
            }],
        },
    ))
    log.append(ZfEvent(
        type="human.escalate",
        actor="zf-cli",
        payload={
            "pdd_id": "CANGJIE-R3",
            "reason": (
                "candidate rework exhausted after 2 attempts; "
                "reviewer findings unresolved"
            ),
            "rework_source": "verify.failed",
            "rework_feedback": [
                "stage_success_criteria: artifact matrix gate failed",
            ],
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    pending = projection["pending_actions"]
    assert len(pending) == 1
    assert pending[0]["action"] == "diagnose-attention"
    assert pending[0]["failure_class"] == "candidate_rework_exhausted_unresolved"
    assert pending[0]["kind"] == "human_gate_repair_resume"
    assert pending[0]["policy_decision"]["decision"] == "needs_diagnosis"
    assert projection["status_explain"]["wait_reason"] == "diagnosis_required"
    assert projection["status_explain"]["next_auto_action"] == "run_manager_diagnosis"
    assert projection["status_explain"]["blocking"] is False


def test_completion_profile_blocks_terminal_success_on_pending_human_decision(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    writer.emit(
        "human.escalate",
        actor="run-manager",
        payload={
            "decision_token": "blocking-decision",
            "reason": "owner approval required for destructive action",
            "blocking_scope": "run",
            "action": "destructive-controlled-action",
        },
    )
    log.append(ZfEvent(type="run.goal.completed", payload={"run_id": "R-COMP"}))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    assert projection["completion_profile"]["status"] == "blocked"
    assert "pending_human_decision" in projection["completion_profile"]["blockers"]
    assert projection["monitor"]["state"] == "needs_human"


def test_run_monitor_reports_blocked_when_completion_has_nonhuman_blockers(
    tmp_path: Path,
) -> None:
    state_dir, _log, _writer = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-A",
        title="still active",
        status="in_progress",
        assigned_to="dev-1",
    ))

    monitor = build_run_monitor_projection(
        state_dir,
        events=[],
        pending_actions=[],
        completion_profile={
            "status": "active",
            "blockers": ["action_verify_failed"],
        },
    )

    assert monitor["state"] == "blocked"
    assert monitor["in_flight_tasks"][0]["task_id"] == "TASK-A"


def test_run_manager_monitor_keeps_blocking_when_monitor_blocked_with_auto_action(
    tmp_path: Path,
) -> None:
    projection = build_run_manager_monitor_projection(
        completion_profile={
            "status": "active",
            "blockers": ["action_verify_failed"],
        },
        monitor={
            "state": "blocked",
            "next_wait": "run_manager_action",
        },
        status_explain={
            "wait_reason": "run_manager_action_ready",
            "next_auto_action": "candidate_escalate",
            "blocking": False,
        },
        pending_actions=[{
            "safe_resume_action": "candidate_escalate",
            "owner_route": "candidate_rework",
            "policy_decision": {"decision": "auto_decide"},
        }],
        no_progress={},
        advisor={"summary": {}},
        wait_hints={"summary": {}},
        resident_agent={},
        runtime_pane_snapshot={"summary": {}},
    )

    assert projection["monitor_state"] == "blocked"
    assert projection["blocking"] is True
    assert projection["wait_reason"] == "run_manager_action_ready"
    assert projection["next_auto_action"] == "candidate_escalate"


def test_terminal_success_overrides_stale_silent_stall_attention(tmp_path: Path) -> None:
    state_dir, log, _writer = _state(tmp_path)
    supervisor_dir = state_dir / "projections" / "supervisor"
    supervisor_dir.mkdir(parents=True)
    (supervisor_dir / "snapshot.json").write_text(
        json.dumps({
            "attention_items": [{
                "status": "open",
                "fingerprint": "stale:dispatch.silent_stall:T1",
                "summary": "stale dispatch silent stall",
            }],
        }) + "\n",
        encoding="utf-8",
    )
    log.append(ZfEvent(
        type="runtime.attention.needed",
        payload={"fingerprint": "stale:dispatch.silent_stall:T1"},
    ))
    log.append(ZfEvent(
        type="judge.passed",
        payload={"run_id": "R39", "candidate_ref": "cand/R39"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    assert projection["completion_profile"]["status"] == "complete"
    assert projection["monitor"]["state"] == "complete"
    assert projection["monitor"]["current_phase"] == "done"
    assert projection["monitor"]["next_wait"] == "complete"
    assert projection["status_explain"]["monitor_state"] == "complete"
    assert projection["status_explain"]["wait_reason"] == "complete"


def test_run_manager_monitor_suppresses_stale_attention_with_newer_progress(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(Task(
        id="T1",
        title="Implement T1",
        status="in_progress",
        assigned_to="dev-lane-1",
    ))
    supervisor_dir = state_dir / "projections" / "supervisor"
    supervisor_dir.mkdir(parents=True)
    (supervisor_dir / "snapshot.json").write_text(
        json.dumps({
            "attention_items": [{
                "status": "open",
                "fingerprint": "runtime:dispatch.silent_stall:T1",
                "task_id": "T1",
                "lane": "dev-lane-1",
                "source_event_ids": ["evt-stall"],
            }],
        }) + "\n",
        encoding="utf-8",
    )
    log.append(ZfEvent(
        id="evt-stall",
        type="runtime.attention.needed",
        task_id="T1",
        payload={"fingerprint": "runtime:dispatch.silent_stall:T1"},
    ))
    log.append(ZfEvent(
        id="evt-progress",
        type="dev.build.done",
        actor="dev-lane-1",
        task_id="T1",
        payload={"task_id": "T1"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    assert projection["monitor"]["state"] == "healthy_waiting"
    assert projection["monitor"]["open_attention"] == 0
    suppressed = projection["monitor"]["suppressed_open_attention"]
    assert suppressed[0]["status"] == "false_positive"
    assert suppressed[0]["evidence_window"]["recovery_event_id"] == "evt-progress"


def test_run_manager_tick_emits_idempotent_run_completed_closeout(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-RESIDUAL",
        title="residual parity task",
        status="in_progress",
        assigned_to="verify-lane-0",
    ))
    log.append(ZfEvent(
        type="judge.passed",
        actor="judge",
        payload={
            "run_id": "R4",
            "pdd_id": "CANGJIE-R4",
            "candidate_ref": "cand/CANGJIE-R4",
            "candidate_head_commit": "abc1234",
        },
    ))

    first = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )
    second = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    closeouts = [event for event in events if event.type == "run.completed"]
    assert first.closeout_events == 1
    assert second.closeout_events == 0
    assert len(closeouts) == 1
    assert closeouts[0].payload["status"] == "passed"
    assert closeouts[0].payload["release_status"] == "not_shipped"
    assert closeouts[0].payload["candidate_head_commit"] == "abc1234"
    assert "candidate.promoted" not in {event.type for event in events}
    assert "ship.completed" not in {event.type for event in events}

    projection = build_run_manager_projection(
        state_dir,
        events=events,
        config=_config(),
    )
    assert projection["completion_profile"]["terminal_signal"]["event_type"] == "run.completed"
    assert projection["monitor"]["state"] == "complete"
    assert projection["monitor"]["in_flight_tasks"] == []
    assert projection["monitor"]["residual_in_flight_tasks"][0]["task_id"] == "TASK-RESIDUAL"
    assert projection["status_explain"]["blocking"] is False
    assert projection["status_explain"]["active_task_id"] == ""


def test_terminal_closeout_recommendation_does_not_open_human_gate(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-RESIDUAL",
        title="residual task already covered by candidate judge",
        status="in_progress",
        assigned_to="verify-lane-0",
    ))
    log.append(ZfEvent(
        type="judge.passed",
        actor="judge",
        payload={
            "run_id": "R4",
            "pdd_id": "CANGJIE-R4",
            "candidate_ref": "cand/CANGJIE-R4",
        },
    ))
    log.append(ZfEvent(
        type="run.manager.agent.recommendation",
        actor="run-manager",
        payload={
            "checkpoint_id": "resident-terminal-closeout",
            "safe_resume_action": "needs_terminal_closeout",
            "recommended_route": "human",
            "failure_class": "workflow_terminal_closeout_stalled",
            "reason": "request terminal closeout",
        },
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert result.agent_recommendations_consumed == 1
    assert result.closeout_events == 1
    assert "human.escalate" not in {event.type for event in events}
    consumed = [
        event for event in events
        if event.type == RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED
    ][-1]
    assert consumed.payload["status"] == "wait"
    assert consumed.payload["reason"] == (
        "terminal closeout is handled by deterministic run manager closeout"
    )
    assert any(event.type == "run.completed" for event in events)


def test_completion_profile_ignores_stale_terminal_signal_after_new_work(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        type="loop.started",
        payload={"run_id": "R-NEW"},
    ))
    log.append(ZfEvent(
        type="human.escalate",
        payload={
            "decision_token": "hdec-old",
            "checkpoint_id": "old-human",
            "reason": "old candidate review escalation",
        },
    ))
    log.append(ZfEvent(
        type="run.manager.action.verify.failed",
        payload={
            "checkpoint_id": "old-verify",
            "reason": "expected downstream event not observed",
        },
    ))
    log.append(ZfEvent(
        type="judge.passed",
        payload={"run_id": "R-OLD", "candidate_ref": "cand/old"},
    ))
    log.append(ZfEvent(
        type="task_map.ready",
        payload={"pdd_id": "R-NEW", "task_map_ref": "artifacts/task_map.json"},
    ))
    log.append(ZfEvent(
        type="fanout.started",
        payload={
            "fanout_id": "fanout-new",
            "stage_id": "slice-implementation",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    assert projection["completion_profile"]["status"] == "active"
    assert projection["completion_profile"]["terminal_signal"] == {}
    assert projection["status_explain"]["completion_status"] == "active"


def test_completion_profile_ignores_repair_blockers_superseded_by_verify_success(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        type="human.escalate",
        payload={
            "pdd_id": "CANGJIE-R4",
            "trace_id": "trace-r4",
            "reason": (
                "candidate rework exhausted after 2 attempts; "
                "reviewer findings unresolved"
            ),
        },
    ))
    log.append(ZfEvent(
        type="autoresearch.repair.closeout.required",
        payload={
            "fingerprint": "agent-recommendation-r4",
            "candidate_id": "C-R4",
            "branch": "self-repair/r4",
            "continuation": {
                "resume_original_workflow": True,
            },
        },
    ))
    log.append(ZfEvent(
        type=RUN_MANAGER_ACTION_FAILED,
        payload={
            "action": "repair-closeout-validate",
            "fingerprint": "agent-recommendation-r4",
            "candidate_id": "C-R4",
            "reason": "command_failed",
        },
    ))
    log.append(ZfEvent(
        type="run.manager.action.verify.failed",
        payload={
            "checkpoint_id": "wfres-r4",
            "fingerprint": "wfres-r4",
            "reason": "expected downstream event not observed",
        },
    ))
    log.append(ZfEvent(
        type="verify.passed",
        payload={
            "pdd_id": "CANGJIE-R4",
            "trace_id": "trace-r4",
            "candidate_ref": "cand/CANGJIE-R4",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    completion = projection["completion_profile"]
    assert completion["pending_human_decisions"] == []
    assert completion["open_verify_failures"] == []
    assert completion["repair_closeout_required"] == 0
    assert completion["repair_merge_pending"] == 0
    assert completion["blockers"] == []
    assert projection["monitor"]["state"] != "needs_human"
    assert projection["monitor"]["state"] != "repair_closeout_required"


def test_complete_monitor_marks_residual_projection_reconciliation_needed(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-RESIDUAL",
        title="residual",
        status="in_progress",
        assigned_to="dev-lane-0",
    ))
    log.append(ZfEvent(
        type="judge.passed",
        payload={"run_id": "R-DONE", "candidate_ref": "cand/done"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    monitor = projection["monitor"]
    assert monitor["state"] == "complete"
    assert monitor["closeout_projection_status"] == "needs_reconciliation"
    assert monitor["in_flight_tasks"] == []
    assert monitor["residual_in_flight_tasks"][0]["task_id"] == "TASK-RESIDUAL"


def test_complete_monitor_marks_projection_clear_without_residuals(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        type="judge.passed",
        payload={"run_id": "R-DONE", "candidate_ref": "cand/done"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    assert projection["monitor"]["state"] == "complete"
    assert projection["monitor"]["closeout_projection_status"] == "clear"


def test_run_level_human_escalation_is_superseded_by_later_parity_closure(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        type=HUMAN_ESCALATION_SENT,
        payload={
            "decision_token": "hdec-rm",
            "owner_route": "run_manager",
            "failure_class": "resident_agent_recommendation",
            "reason": "resident Run Manager requested human decision",
        },
    ))
    log.append(ZfEvent(
        type="module.parity.closed",
        payload={
            "pdd_id": "CANGJIE-R4",
            "trace_id": "trace-r4",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    assert projection["completion_profile"]["pending_human_decisions"] == []
    assert "pending_human_decision" not in projection["completion_profile"]["blockers"]


def test_run_level_human_escalation_is_superseded_by_later_work_resume(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        type=HUMAN_ESCALATION_SENT,
        payload={
            "decision_token": "hdec-rm-work",
            "owner_route": "run_manager",
            "failure_class": "resident_agent_recommendation",
            "reason": "resident Run Manager requested human decision",
        },
    ))
    log.append(ZfEvent(
        type="task_map.ready",
        payload={
            "pdd_id": "CANGJIE-R4",
            "trace_id": "trace-r4",
            "task_map_ref": "artifacts/r4/task_map.json",
        },
    ))
    log.append(ZfEvent(
        type="fanout.started",
        payload={
            "pdd_id": "CANGJIE-R4",
            "trace_id": "trace-r4",
            "fanout_id": "fanout-r4-gap-impl",
            "stage_id": "cangjie-slice-implementation",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    assert projection["completion_profile"]["pending_human_decisions"] == []
    assert "pending_human_decision" not in projection["completion_profile"]["blockers"]


def test_repair_merge_queue_and_timeline_are_event_sourced(tmp_path: Path) -> None:
    state_dir, log, _writer = _state(tmp_path)
    closeout = ZfEvent(
        type="autoresearch.repair.closeout.required",
        payload={
            "fingerprint": "stall:rm",
            "candidate_id": "C-RM",
            "candidate_path": "/tmp/C-RM",
            "branch": "self-repair/rm",
            "risk_classification": {
                "risk": "high",
                "controlled_apply_allowed": False,
            },
            "verification_plan": [{
                "kind": "focused_pytest",
                "command": "uv run pytest tests/test_run_manager.py -q",
            }],
        },
    )
    log.append(closeout)

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )
    queue = projection["repair_merge_queue"]
    assert queue["schema_version"] == "repair-merge-queue.v1"
    assert queue["summary"]["pending"] == 1
    assert queue["items"][0]["status"] == "closeout_required"
    assert queue["items"][0]["apply_candidate"]["decision"] == "human_approval_required"
    assert queue["items"][0]["apply_candidate"]["verification_plan_count"] == 1
    assert projection["monitor"]["state"] == "repair_closeout_required"
    assert any(
        item["event_type"] == "autoresearch.repair.closeout.required"
        for item in projection["timeline"]["items"]
    )

    log.append(ZfEvent(
        type=RUN_MANAGER_REPAIR_MERGE_QUEUED,
        payload={"candidate_id": "C-RM", "fingerprint": "stall:rm"},
    ))
    log.append(ZfEvent(
        type=RUN_MANAGER_REPAIR_MERGE_MERGED,
        payload={"candidate_id": "C-RM", "fingerprint": "stall:rm"},
    ))
    merged = build_repair_merge_queue(log.read_all())
    assert merged["summary"]["pending"] == 0
    assert merged["items"][0]["status"] == "merged"


def test_run_completed_demotes_repair_closeout_to_maintenance(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        type="autoresearch.repair.closeout.required",
        payload={
            "fingerprint": "terminal:maintenance",
            "candidate_id": "C-MAINT",
            "branch": "self-repair/maintenance",
            "risk_classification": {
                "risk": "low",
                "controlled_apply_allowed": True,
            },
        },
    ))
    log.append(ZfEvent(
        type="run.completed",
        payload={
            "status": "passed",
            "run_id": "R5",
            "reason": "quality gates completed",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    assert projection["completion_profile"]["status"] == "complete"
    assert projection["completion_profile"]["repair_closeout_required"] == 1
    assert "repair_closeout_pending" not in projection["completion_profile"]["blockers"]
    assert projection["status_explain"]["blocking"] is False
    assert projection["status_explain"]["wait_reason"] == "complete_with_maintenance_pending"
    assert projection["status_explain"]["maintenance_refs"]


def test_pending_human_decisions_deduplicate_repeated_same_lease(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    for index in range(10):
        log.append(ZfEvent(
            type=HUMAN_ESCALATION_SENT,
            id=f"evt-human-{index}",
            payload={
                "decision_token": f"hdec-{index}",
                "owner_route": "run_manager",
                "failure_class": "resident_agent_recommendation",
                "checkpoint_id": "wfres-r5",
                "fingerprint": "stall:r5",
                "reason": "same stale human decision refreshed",
            },
        ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    pending = projection["completion_profile"]["pending_human_decisions"]
    assert len(pending) == 1
    assert pending[0]["lease_key"]
    assert len(pending[0]["source_event_ids"]) == 10
    assert pending[0]["last_refreshed_at"]


def test_run_manager_executes_repair_closeout_validation_plan(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    worktree = tmp_path / "repair-worktree"
    worktree.mkdir()
    subprocess.run(["git", "-C", str(worktree), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "Test User"], check=True)
    (worktree / "README.md").write_text("repair\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(worktree), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(worktree), "commit", "-q", "-m", "fix: repair"], check=True)
    log.append(ZfEvent(
        type="autoresearch.repair.closeout.required",
        payload={
            "fingerprint": "stall:validate",
            "candidate_id": "C-VALIDATE",
            "branch": "self-repair/validate",
            "worktree": str(worktree),
            "source_commit": "abc123",
            "risk_classification": {
                "risk": "low",
                "controlled_apply_allowed": True,
            },
            "verification_plan": [{
                "kind": "diff_integrity",
                "command": "git diff --check",
                "required": "true",
            }],
            "continuation": {
                "restart_required": True,
                "resume_original_workflow": True,
            },
        },
    ))

    projection = build_run_manager_projection(state_dir, events=log.read_all(), config=_config())
    pending = projection["pending_actions"][0]
    assert pending["action"] == "repair-closeout-validate"
    assert pending["policy_decision"]["decision"] == "auto_decide"

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        spawn_repairs=False,
        action_filter={"repair-closeout-validate"},
    )

    assert result.actions_applied == 1
    events = log.read_all()
    applied = [
        event for event in events
        if event.type == RUN_MANAGER_ACTION_APPLIED
        and event.payload.get("action") == "repair-closeout-validate"
    ][-1]
    assert applied.payload["validation_result"]["status"] == "passed"
    assert any(
        event.type == RUN_MANAGER_ACTION_VERIFY_PASSED
        and event.payload.get("action") == "repair-closeout-validate"
        for event in events
    )
    queue = build_repair_merge_queue(events)
    assert queue["items"][0]["validation"]["status"] == "passed"
    assert queue["items"][0]["next_allowed_action"] == "operator_merge_or_reject"


def test_run_manager_repair_closeout_validation_rejects_untrusted_command(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    worktree = tmp_path / "repair-worktree"
    worktree.mkdir()
    log.append(ZfEvent(
        type="autoresearch.repair.closeout.required",
        payload={
            "fingerprint": "stall:unsafe",
            "candidate_id": "C-UNSAFE",
            "branch": "self-repair/unsafe",
            "worktree": str(worktree),
            "source_commit": "def456",
            "risk_classification": {
                "risk": "low",
                "controlled_apply_allowed": True,
            },
            "verification_plan": [{
                "kind": "unsafe",
                "command": "rm -rf /tmp/not-allowed",
                "required": "true",
            }],
        },
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        spawn_repairs=False,
        action_filter={"repair-closeout-validate"},
    )

    assert result.actions_failed == 1
    events = log.read_all()
    failed = [
        event for event in events
        if event.type == RUN_MANAGER_ACTION_FAILED
        and event.payload.get("action") == "repair-closeout-validate"
    ][-1]
    assert failed.payload["validation_result"]["reason"] == "command_not_allowlisted"
    queue = build_repair_merge_queue(events)
    assert queue["items"][0]["validation"]["status"] == "failed"
    assert queue["items"][0]["next_allowed_action"] == "repair_validation_failed"


def test_repair_merge_with_continuation_checkpoint_creates_resume_action(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    continuation = {
        "resume_original_workflow": True,
        "checkpoint_id": "wfres-rm-continuation",
        "safe_resume_action": "repair_failed_children",
    }
    log.append(ZfEvent(
        type="autoresearch.repair.closeout.required",
        payload={
            "fingerprint": "stall:continuation",
            "candidate_id": "C-CONT",
            "continuation": continuation,
        },
    ))
    log.append(ZfEvent(
        type=RUN_MANAGER_REPAIR_MERGE_QUEUED,
        payload={"fingerprint": "stall:continuation", "candidate_id": "C-CONT"},
    ))
    log.append(ZfEvent(
        type=RUN_MANAGER_REPAIR_MERGE_MERGED,
        payload={"fingerprint": "stall:continuation", "candidate_id": "C-CONT"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )
    pending = projection["pending_actions"][0]

    assert pending["action"] == "workflow-batch-resume"
    assert pending["checkpoint_id"] == "wfres-rm-continuation"
    assert pending["safe_resume_action"] == "repair_failed_children"
    assert pending["policy_decision"]["decision"] == "auto_decide"


def test_repair_merge_continuation_without_checkpoint_requires_diagnosis(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    log.append(ZfEvent(
        type="autoresearch.repair.closeout.required",
        payload={
            "fingerprint": "stall:missing-continuation",
            "candidate_id": "C-MISSING",
            "continuation": {"resume_original_workflow": True},
        },
    ))
    log.append(ZfEvent(
        type=RUN_MANAGER_REPAIR_MERGE_MERGED,
        payload={"fingerprint": "stall:missing-continuation", "candidate_id": "C-MISSING"},
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )
    pending = projection["pending_actions"][0]

    assert pending["action"] == "diagnose-attention"
    assert pending["failure_class"] == (
        "self_repair_post_merge_continuation_missing_checkpoint"
    )
    assert pending["policy_decision"]["decision"] == "needs_diagnosis"


def test_repair_merge_continuation_does_not_repeat_after_resume_applied(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    continuation = {
        "resume_original_workflow": True,
        "checkpoint_id": "wfres-rm-done",
        "safe_resume_action": "repair_failed_children",
    }
    log.append(ZfEvent(
        type="autoresearch.repair.closeout.required",
        payload={
            "fingerprint": "stall:done-continuation",
            "candidate_id": "C-DONE",
            "continuation": continuation,
        },
    ))
    log.append(ZfEvent(
        type=RUN_MANAGER_REPAIR_MERGE_MERGED,
        payload={"fingerprint": "stall:done-continuation", "candidate_id": "C-DONE"},
    ))
    log.append(ZfEvent(
        type="workflow.resume.applied",
        payload={
            "checkpoint_id": "wfres-rm-done",
            "resume_checkpoint_ref": "wfres-rm-done",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    assert projection["pending_actions"] == []


def test_human_decision_reject_event_type_is_known() -> None:
    assert RUN_MANAGER_HUMAN_DECISION_APPLIED in KNOWN_EVENT_TYPES
    assert RUN_MANAGER_HUMAN_DECISION_REJECTED in KNOWN_EVENT_TYPES
    assert RUN_MANAGER_REPAIR_MERGE_QUEUED in KNOWN_EVENT_TYPES


def test_run_goal_marks_repeated_blocker_ready_only_after_threshold() -> None:
    events = [
        ZfEvent(
            type="run.goal.started",
            payload={"run_id": "R1", "objective": "refactor hermes"},
        ),
        ZfEvent(
            type=RUN_MANAGER_ACTION_BLOCKED,
            payload={"checkpoint_id": "ck-1", "reason": "needs human"},
        ),
        ZfEvent(
            type=RUN_MANAGER_ACTION_BLOCKED,
            payload={"checkpoint_id": "ck-1", "reason": "needs human"},
        ),
    ]
    assert build_run_goal_projection(events)["blocked_ready"] is False
    events.append(ZfEvent(
        type=RUN_MANAGER_ACTION_BLOCKED,
        payload={"checkpoint_id": "ck-1", "reason": "needs human"},
    ))
    projection = build_run_goal_projection(events)
    assert projection["status"] == "active"
    assert projection["blocked_ready"] is True


def test_run_goal_completion_event_on_judge_passed_when_active() -> None:
    from zf.runtime.run_manager import run_goal_completion_event

    events = [
        ZfEvent(type="run.goal.started", payload={"run_id": "R9", "objective": "ship mdtoc"}),
    ]
    judge = ZfEvent(type="judge.passed", id="evt-judge", payload={"pdd_id": "default"})
    completion = run_goal_completion_event(events, cause=judge)
    assert completion is not None
    assert completion.type == "run.goal.completed"
    assert completion.payload["run_id"] == "R9"
    assert completion.payload["source_event_id"] == "evt-judge"
    assert completion.causation_id


def test_run_goal_completion_event_idempotent_when_already_complete() -> None:
    from zf.runtime.run_manager import run_goal_completion_event

    events = [
        ZfEvent(type="run.goal.started", payload={"run_id": "R9"}),
        ZfEvent(type="run.goal.completed", payload={"run_id": "R9"}),
    ]
    judge = ZfEvent(type="judge.passed", payload={})
    assert run_goal_completion_event(events, cause=judge) is None


def test_run_goal_completion_event_none_without_real_goal() -> None:
    """loop.started→active fallback alone must not synthesize a completion."""
    from zf.runtime.run_manager import run_goal_completion_event

    events = [ZfEvent(type="loop.started", payload={})]
    judge = ZfEvent(type="judge.passed", payload={})
    assert run_goal_completion_event(events, cause=judge) is None


def test_run_goal_completion_claim_is_blocked_while_feedback_is_open() -> None:
    from zf.runtime.run_manager import (
        RUN_GOAL_COMPLETION_CLAIMED,
        RUN_GOAL_COMPLETION_BLOCKED,
        run_goal_completion_claim_event,
        run_goal_completion_gate_event,
    )

    events = [
        ZfEvent(type="run.goal.started", payload={"run_id": "R-GATE"}),
        ZfEvent(
            id="rework-open",
            type="task.rework.requested",
            task_id="T-1",
            payload={
                "task_id": "T-1",
                "dispatch_id": "dispatch-1",
                "finding_ids": ["finding-open"],
            },
        ),
    ]
    judge = ZfEvent(type="judge.passed", id="judge-open", payload={})
    claim = run_goal_completion_claim_event(events, cause=judge)
    assert claim is not None and claim.type == RUN_GOAL_COMPLETION_CLAIMED
    blocked = run_goal_completion_gate_event([*events, claim], claim=claim)
    assert blocked is not None and blocked.type == RUN_GOAL_COMPLETION_BLOCKED
    assert blocked.payload["blockers"] == ["open_feedback", "pending_handoff"]


def test_run_goal_completion_gate_closes_once_after_independent_verify() -> None:
    from zf.runtime.run_manager import (
        run_goal_completion_claim_event,
        run_goal_completion_gate_event,
    )

    target = "b" * 40
    events = [
        ZfEvent(type="run.goal.started", payload={"run_id": "R-CLOSE"}),
        ZfEvent(
            id="rework-1",
            type="task.rework.requested",
            task_id="T-1",
            payload={
                "task_id": "T-1",
                "dispatch_id": "dispatch-1",
                "finding_ids": ["finding-1"],
            },
        ),
        ZfEvent(
            type="task.dispatched",
            task_id="T-1",
            causation_id="rework-1",
            payload={
                "task_id": "T-1",
                "dispatch_id": "dispatch-1",
                "rework_request_event_id": "rework-1",
            },
        ),
        ZfEvent(
            type="dev.build.done",
            task_id="T-1",
            payload={
                "task_id": "T-1",
                "dispatch_id": "dispatch-1",
                "source_commit": target,
            },
        ),
        ZfEvent(
            type="verify.passed",
            task_id="T-1",
            payload={"task_id": "T-1", "target_commit": target},
        ),
    ]
    judge = ZfEvent(type="judge.passed", id="judge-close", payload={})
    claim = run_goal_completion_claim_event(events, cause=judge)
    assert claim is not None
    completion = run_goal_completion_gate_event([*events, claim], claim=claim)
    assert completion is not None and completion.type == "run.goal.completed"
    replay_events = [*events, claim, completion]
    assert run_goal_completion_gate_event(replay_events, claim=claim) is None
    projection = build_run_goal_projection(replay_events)
    assert projection["status"] == "complete"
    assert projection["delivery_phase"] == "goal_completed"
    assert projection["open_feedback_count"] == 0
    assert projection["pending_handoff_count"] == 0


def test_run_goal_completion_gate_blocks_on_blocking_human_decision() -> None:
    from zf.runtime.run_manager import run_goal_completion_event

    events = [
        ZfEvent(type="run.goal.started", payload={"run_id": "R-HUMAN"}),
        ZfEvent(
            id="human-1",
            type="human.escalate",
            payload={
                "decision_token": "decision-1",
                "blocking_scope": "run",
                "reason": "owner approval required",
            },
        ),
    ]
    outcome = run_goal_completion_event(
        events,
        cause=ZfEvent(type="judge.passed", id="judge-human", payload={}),
    )
    assert outcome is not None
    assert outcome.type == "run.goal.completion.blocked"
    assert "pending_human_decision" in outcome.payload["blockers"]


def test_run_manager_routes_scoped_delivery_failure_to_approved_ship_retry() -> None:
    from zf.runtime.run_manager import _pending_semantic_event_actions

    failed = ZfEvent(
        id="delivery-failed-1",
        type="run.delivery.failed",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "run_id": "run-1",
            "goal_id": "GOAL-1",
            "claim_id": "claim-1",
            "delivery_operation_id": "delivery-claim-1",
            "candidate_ref": "candidate/GOAL-1",
            "target_commit": "a" * 40,
            "reason": "temporary merge failure",
        },
    )

    actions = _pending_semantic_event_actions([failed])

    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "ship-retry"
    assert action["safe_resume_action"] == "ship-retry"
    assert action["run_id"] == "run-1"
    assert action["claim_id"] == "claim-1"
    assert action["delivery_operation_id"] == "delivery-claim-1"
    assert action["policy_decision"]["decision"] == "needs_approval"

    settled = ZfEvent(
        type="run.delivery.settled",
        correlation_id="run-1",
        payload={
            "run_id": "run-1",
            "claim_id": "claim-1",
            "delivery_operation_id": "delivery-claim-1",
        },
    )
    assert _pending_semantic_event_actions([failed, settled]) == []


def test_run_manager_deduplicates_completion_blocker_fingerprint() -> None:
    from zf.runtime.run_manager import _pending_semantic_event_actions

    payload = {
        "run_id": "run-1",
        "claim_id": "claim-1",
        "blockers": ["open_feedback"],
        "blocker_fingerprint": "blocker-fingerprint-1",
        "reason": "feedback remains open",
    }
    events = [
        ZfEvent(type="run.goal.completion.blocked", payload=payload),
        ZfEvent(type="run.goal.completion.blocked", payload=payload),
    ]

    actions = _pending_semantic_event_actions(events)

    assert len(actions) == 1
    assert actions[0]["safe_resume_action"] == "diagnose_attention"
    assert actions[0]["suggested_action"]["kind"] == "reconcile_goal_feedback"
    assert actions[0]["expected_downstream_events"] == [
        "rework.feedback.verified_closed",
        "rework.feedback.residual",
    ]


def test_run_manager_classifies_goal_completion_handoff_and_human_blockers() -> None:
    from zf.runtime.run_manager import _pending_semantic_event_actions

    handoff = _pending_semantic_event_actions([ZfEvent(
        id="handoff-blocked",
        type="run.goal.completion.blocked",
        correlation_id="run-1",
        payload={
            "run_id": "run-1",
            "claim_id": "claim-handoff",
            "blockers": ["pending_handoff"],
            "blocker_fingerprint": "handoff-fingerprint",
        },
    )])
    assert handoff[0]["suggested_action"]["kind"] == (
        "reconcile_goal_attempt_handoff"
    )
    assert "attempt.handoff.closed" in handoff[0]["expected_downstream_events"]

    human = _pending_semantic_event_actions([ZfEvent(
        id="human-blocked",
        type="run.goal.completion.blocked",
        correlation_id="run-1",
        payload={
            "run_id": "run-1",
            "claim_id": "claim-human",
            "blockers": ["pending_human_decision"],
            "blocker_fingerprint": "human-fingerprint",
        },
    )])
    assert human[0]["suggested_action"]["kind"] == "await_goal_human_decision"
    assert human[0]["policy_decision"]["decision"] == "needs_approval"
    assert human[0]["policy_decision"]["executable"] is False


def test_run_manager_uses_delivery_failure_as_single_ship_recovery_owner() -> None:
    from zf.runtime.run_manager import _pending_semantic_event_actions

    shared = {
        "workflow_run_id": "run-1",
        "run_id": "run-1",
        "goal_id": "GOAL-1",
        "claim_id": "claim-1",
        "delivery_operation_id": "delivery-claim-1",
        "candidate_ref": "candidate/GOAL-1",
        "target_commit": "a" * 40,
    }
    events = [
        ZfEvent(
            id="delivery-failed-1",
            type="run.delivery.failed",
            correlation_id="run-1",
            payload={**shared, "reason": "temporary merge failure"},
        ),
        ZfEvent(
            id="delivery-gate-blocked-1",
            type="run.goal.completion.blocked",
            correlation_id="run-1",
            payload={
                **shared,
                "blockers": ["delivery_failed"],
                "blocker_fingerprint": "delivery-fingerprint",
            },
        ),
    ]

    actions = _pending_semantic_event_actions(events)

    assert len(actions) == 1
    assert actions[0]["action"] == "ship-retry"


def test_run_goal_completion_gate_rejects_judge_verify_target_mismatch() -> None:
    from zf.runtime.run_manager import run_goal_completion_event

    verified_target = "a" * 40
    judge_target = "b" * 40
    events = [
        ZfEvent(type="run.goal.started", payload={"run_id": "R-TARGET"}),
        ZfEvent(
            type="verify.passed",
            task_id="T-TARGET",
            payload={"target_commit": verified_target},
        ),
    ]
    outcome = run_goal_completion_event(
        events,
        cause=ZfEvent(
            type="judge.passed",
            id="judge-target",
            payload={"target_commit": judge_target},
        ),
    )

    assert outcome is not None
    assert outcome.type == "run.goal.completion.rejected"
    assert "verification_target_mismatch" in outcome.payload["invalid_reasons"]
    assert outcome.payload["target_commit"] == judge_target
    assert outcome.payload["verified_target_commit"] == verified_target


def test_run_goal_completion_gate_isolates_interleaved_runs() -> None:
    from zf.runtime.run_manager import (
        run_goal_completion_claim_event,
        run_goal_completion_gate_event,
    )

    run_a = "RUN-A"
    run_b = "RUN-B"
    target_a = "a" * 40
    target_b = "b" * 40
    events = [
        ZfEvent(
            type="run.goal.started",
            payload={"run_id": run_a, "objective": "ship A"},
            correlation_id=run_a,
        ),
        ZfEvent(
            type="run.goal.started",
            payload={"run_id": run_b, "objective": "ship B"},
            correlation_id=run_b,
        ),
        ZfEvent(
            type="verify.passed",
            task_id="TASK-B",
            correlation_id=run_b,
            payload={"target_commit": target_b},
        ),
        ZfEvent(
            id="human-b",
            type="human.escalate",
            correlation_id=run_b,
            payload={
                "decision_token": "decision-b",
                "blocking_scope": "run",
                "reason": "B needs approval",
            },
        ),
        ZfEvent(
            type="verify.passed",
            task_id="TASK-A",
            correlation_id=run_a,
            payload={"target_commit": target_a},
        ),
    ]
    judge_a = ZfEvent(
        type="judge.passed",
        id="judge-a",
        correlation_id=run_a,
        payload={"target_commit": target_a},
    )

    claim = run_goal_completion_claim_event(events, cause=judge_a)
    assert claim is not None and claim.payload["run_id"] == run_a
    outcome = run_goal_completion_gate_event([*events, claim], claim=claim)

    assert outcome is not None and outcome.type == "run.goal.completed"
    assert outcome.payload["run_id"] == run_a
    assert outcome.payload["verified_target_commit"] == target_a


def test_run_goal_completion_claim_rejects_ambiguous_unscoped_judge() -> None:
    from zf.runtime.run_manager import run_goal_completion_claim_event

    events = [
        ZfEvent(type="run.goal.started", payload={"run_id": "RUN-A"}),
        ZfEvent(type="run.goal.started", payload={"run_id": "RUN-B"}),
    ]

    assert run_goal_completion_claim_event(
        events,
        cause=ZfEvent(type="judge.passed", id="judge-unscoped", payload={}),
    ) is None


def test_run_manager_repair_intake_dispatches_through_executor(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        type="autoresearch.repair.dispatch_requested",
        payload={
            "fingerprint": "stall:rm",
            "attempt": 0,
            "candidate_id": "C-RM",
            "candidate_path": "/tmp/candidate.md",
            "repair_task_payload": {
                "contract": {"scope": ["src/zf/**"], "verification": "pytest"}
            },
        },
    ))

    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
            patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        result = run_manager_tick(
            state_dir=state_dir,
            writer=writer,
            config=_config(),
            event_log=log,
            spawn_repairs=True,
            repair_backend="codex",
        )

    events = log.read_all()
    assert result.repairs_accepted == 1
    assert result.repairs_dispatched == 1
    assert any(event.type == RUN_MANAGER_REPAIR_ACCEPTED for event in events)
    dispatched = [event for event in events if event.type == "autoresearch.repair.dispatched"]
    assert dispatched and dispatched[-1].actor == "run-manager"
    mpopen.assert_called_once()


def test_run_manager_delegates_repair_execution_to_enabled_resident(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        type="autoresearch.repair.dispatch_requested",
        payload={
            "fingerprint": "stall:resident",
            "attempt": 1,
            "candidate_id": "C-RESIDENT",
            "candidate_path": "/tmp/candidate.md",
        },
    ))
    config = _config()
    config.runtime = RuntimeConfig(
        autoresearch_resident=RuntimeAutoresearchResidentConfig(
            enabled=True,
            self_repair_consumer=True,
        ),
    )

    with patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        result = run_manager_tick(
            state_dir=state_dir,
            writer=writer,
            config=config,
            event_log=log,
            spawn_repairs=True,
            repair_backend="codex",
        )

    events = log.read_all()
    assert result.repairs_accepted == 1
    assert result.repairs_dispatched == 0
    assert any(event.type == RUN_MANAGER_REPAIR_ACCEPTED for event in events)
    assert not any(
        event.type == "autoresearch.repair.dispatched" for event in events
    )
    mpopen.assert_not_called()


def test_run_manager_resident_recommendation_can_dispatch_repair_worker(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        type="run.manager.agent.recommendation",
        id="evt-rec-repair",
        actor="run-manager",
        payload={
            "recommended_route": "repair",
            "checkpoint_id": "ck-candidate-hygiene",
            "fingerprint": "candidate_environment_gate:.venv-node_modules",
            "title": "Repair candidate hygiene gate",
            "summary": "candidate_worktree_clean treats .venv/node_modules symlinks as dirty",
            "repair_task_payload": {
                "title": "Repair candidate hygiene gate",
                "contract": {
                    "scope": ["src/zf/runtime/**", "tests/**"],
                    "verification": "uv run pytest tests/test_candidates.py tests/test_run_manager.py",
                },
            },
        },
    ))

    with patch("zf.runtime.self_repair_runner.subprocess.run") as mrun, \
            patch("zf.runtime.self_repair_runner.subprocess.Popen") as mpopen:
        mrun.return_value = MagicMock(returncode=0, stderr="")
        result = run_manager_tick(
            state_dir=state_dir,
            writer=writer,
            config=_source_repair_config(),
            event_log=log,
            spawn_repairs=True,
            repair_backend="claude-code",
        )

    events = log.read_all()
    assert result.agent_recommendations_consumed == 1
    assert result.repairs_accepted == 1
    assert result.repairs_dispatched == 1
    accepted = [event for event in events if event.type == RUN_MANAGER_REPAIR_ACCEPTED]
    assert accepted
    assert accepted[-1].payload["source"] == "run_manager_resident_recommendation"
    assert accepted[-1].payload["repair_task_payload"]["contract"]["scope"] == [
        "src/zf/runtime/**",
        "tests/**",
    ]
    consumed = [
        event for event in events
        if event.type == RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED
    ]
    assert consumed[-1].payload["route"] == "repair"
    assert consumed[-1].payload["downstream_event_ids"] == [accepted[-1].id]
    dispatched = [event for event in events if event.type == "autoresearch.repair.dispatched"]
    assert dispatched and dispatched[-1].actor == "run-manager"
    mpopen.assert_called_once()


def test_run_manager_resident_repair_route_requires_source_repair_enabled(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        type="run.manager.agent.recommendation",
        id="evt-rec-repair-disabled",
        actor="run-manager",
        payload={
            "recommended_route": "repair",
            "checkpoint_id": "ck-disabled",
            "fingerprint": "run-manager-source-repair-disabled",
            "title": "Repair disabled",
            "summary": "source repair should be gated",
        },
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=True,
        repair_backend="codex",
    )

    events = log.read_all()
    assert result.agent_recommendations_consumed == 1
    assert result.repairs_accepted == 0
    assert not any(event.type == RUN_MANAGER_REPAIR_ACCEPTED for event in events)
    consumed = [
        event for event in events
        if event.type == RUN_MANAGER_AGENT_RECOMMENDATION_CONSUMED
    ][-1]
    assert consumed.payload["status"] == "blocked"
    assert "source_repair.enabled is false" in consumed.payload["reason"]
    approvals = [event for event in events if event.type == "approval.requested"]
    assert approvals
    assert approvals[-1].payload["repair_not_allowed"] is True
    assert approvals[-1].payload["repair_permission"]["current"] is False


def test_run_manager_consumes_autoresearch_diagnosis_result(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    request = writer.emit(
        RUN_MANAGER_AUTORESEARCH_REQUESTED,
        actor="run-manager",
        correlation_id="rm-ar-1",
        payload={
            "request_id": "rm-ar-1",
            "fingerprint": "workflow_resume_batch:fanout-1",
            "context_ref": "projections/run_manager.json#run_context_bundle",
        },
    )
    log.append(ZfEvent(
        type="autoresearch.loop.completed",
        actor="autoresearch",
        correlation_id="rm-ar-1",
        causation_id=request.id,
        payload={
            "request_id": "rm-ar-1",
            "fingerprint": "workflow_resume_batch:fanout-1",
            "status": "completed",
            "proposal_ref": ".zf/autoresearch/runs/rm-ar-1/report.md",
        },
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    consumed = [
        event for event in log.read_all()
        if event.type == RUN_MANAGER_AUTORESEARCH_CONSUMED
    ]
    assert result.autoresearch_consumed == 1
    assert len(consumed) == 1
    assert consumed[0].payload["request_id"] == "rm-ar-1"
    assert consumed[0].payload["next_route"] == "proposal_review"


def test_run_manager_owns_autoresearch_repair_exhaustion_escalation(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        id="ar-repair-exhausted",
        type="autoresearch.repair.escalation.requested",
        actor="zf-autoresearch",
        task_id="TASK-1",
        payload={
            "fingerprint": "task-ref-rejected:cap",
            "reason": "repair attempt cap reached",
            "attempt": 3,
            "owner_route": "run_manager",
        },
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    assert result.autoresearch_requested == 1
    assert any(
        event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
        and event.payload["failure_class"] == "autoresearch_repair_attempts_exhausted"
        for event in log.read_all()
    )
    assert not any(event.type == "human.escalate" for event in log.read_all())


def test_raw_autoresearch_trigger_requires_run_manager_intake(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    log.append(ZfEvent(
        id="raw-ar-trigger",
        type="autoresearch.trigger.accepted",
        actor="zf-autoresearch",
        task_id="TASK-1",
        payload={
            "trigger_id": "raw-ar-trigger",
            "fingerprint": "worker-stuck:TASK-1",
            "reason": "worker made no progress",
            "severity": "high",
        },
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(),
        event_log=log,
        spawn_repairs=False,
    )

    assert result.autoresearch_requested == 1
    assert any(
        event.type == RUN_MANAGER_AUTORESEARCH_REQUESTED
        and event.payload["failure_class"] == "autoresearch_trigger_candidate"
        for event in log.read_all()
    )
    assert not any(
        event.type == "autoresearch.loop.requested" for event in log.read_all()
    )


def test_every_pending_action_carries_problem_envelope(tmp_path: Path) -> None:
    """131-P1-3 forcing:RM pending action 必带 problem_envelope,缺失即红。"""
    state_dir, log, _writer = _state(tmp_path)
    _append_repair_failed_children_fixture(log)
    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_lane_resume_config(),
    )
    actions = projection["pending_actions"]
    assert actions, "fixture 应产生至少一个 pending action"
    for action in actions:
        envelope = action.get("problem_envelope")
        assert isinstance(envelope, dict), f"action {action.get('action')} 缺 envelope"
        assert envelope.get("schema_version"), f"action {action.get('action')} envelope 无 schema"


def test_stale_diagnosis_forwarded_request_gets_anchored_window(
    tmp_path: Path,
) -> None:
    """R6: requests arriving while the resident is synchronously inside a
    bounded loop get no ack until that loop ends — the reactor's
    loop.requested forward is the only timely receipt, and it must count as
    an anchor (else a whole burst stales at 300s while queued)."""
    from datetime import datetime, timedelta, timezone

    state_dir, log, writer = _state(tmp_path)
    _write_supervisor_attention(state_dir)
    initial = build_run_manager_projection(
        state_dir, events=log.read_all(), config=_config(),
    )
    pending = initial["pending_actions"][0]
    _stale_diagnosis_request(log, pending, "rmar-fwd-1")
    ten_minutes_ago = (
        datetime.now(timezone.utc) - timedelta(seconds=600)
    ).isoformat()
    log.append(ZfEvent(
        id="evt-loop-fwd",
        type="autoresearch.loop.requested",
        ts=ten_minutes_ago,
        actor="zf-autoresearch",
        payload={"loop_request_id": "rmar-fwd-1"},
    ))

    result = run_manager_tick(
        state_dir=state_dir, writer=writer, config=_config(),
        event_log=log, spawn_repairs=False,
    )

    assert result.actions_failed == 0
