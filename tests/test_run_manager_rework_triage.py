from __future__ import annotations


from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

import pytest

from zf.core.config.loader import load_config
from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowStageConfig,
    WorkflowConfig,
    WorkflowStrictTriggersConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_briefing import build_orchestrator_briefing
from zf.runtime.rework_triage import classify_rework_trigger
from zf.runtime.run_manager import (
    _prioritize_pending_actions,
    build_run_manager_projection,
    run_manager_tick,
)
from zf.runtime.run_manager_rework_triage import (
    TRIAGE_RECORDED,
    TRIAGE_REQUESTED,
    active_immediate_replan_task_ids,
    pending_immediate_replan_actions,
    pending_rework_triage_actions,
)
from zf.runtime.semantic_replan import (
    SEMANTIC_REPLAN_ACTION,
    enrich_semantic_replan_action,
    resolve_semantic_replan_route,
)
from zf.runtime.task_contract_snapshot import current_task_contract_identity
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _config(*, with_orchestrator: bool = False) -> ZfConfig:
    roles = [
        RoleConfig(name="dev", backend="mock", max_rework_attempts=3),
        RoleConfig(name="review", backend="mock"),
    ]
    if with_orchestrator:
        roles.append(RoleConfig(name="orchestrator", backend="mock"))
    return ZfConfig(
        project=ProjectConfig(name="triage-test"),
        session=SessionConfig(tmux_session="triage-test"),
        workflow=WorkflowConfig(
            strict_triggers=WorkflowStrictTriggersConfig(rework_attempts_gte=3),
        ),
        roles=roles,
    )


def _state(tmp_path: Path) -> tuple[Path, EventLog, EventWriter]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(parents=True)
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log)


def _failure(round_number: int) -> ZfEvent:
    return ZfEvent(
        id=f"failure-{round_number}",
        type="review.rejected",
        actor="review",
        task_id="TASK-1",
        payload={
            "fanout_id": f"fanout-{round_number}",
            "failure_fingerprint": "missing-expiry-test",
            "reason": "missing expiry test",
        },
    )


def test_evidenced_scope_blocker_without_failure_class_requests_replan() -> None:
    blocked = ZfEvent(
        id="evt-live-scope-blocker",
        type="dev.blocked",
        actor="issue-fix-lane-0",
        task_id="P0-WEB-GOLDEN",
        payload={
            "summary": (
                "The API cannot supply the selected committed scenario "
                "document, and adding that API is outside this child contract."
            ),
            "evidence_refs": [
                "git:3e46b45",
                "file:src/scenarioforge/api/app.py",
            ],
        },
    )

    result = classify_rework_trigger(blocked)

    assert result.classification == "design_issue"
    assert result.gate_rule == "task_contract"
    assert result.suspected_owner == "planner"
    assert result.recommended_action == "request_replan"
    assert result.should_increment_retry is False


@pytest.mark.parametrize(
    "payload",
    [
        {
            "summary": "Adding that API is outside this child contract.",
            "evidence_refs": [],
        },
        {
            "summary": "The current contract needs more investigation.",
            "evidence_refs": ["file:src/api.py"],
        },
    ],
)
def test_unproven_or_ambiguous_blocker_does_not_request_replan(
    payload: dict[str, object],
) -> None:
    blocked = ZfEvent(
        id="evt-ambiguous-blocker",
        type="dev.blocked",
        actor="dev",
        task_id="TASK-1",
        payload=payload,
    )

    result = classify_rework_trigger(blocked)

    assert result.recommended_action != "request_replan"


def test_semantic_replan_preempts_stale_mechanical_batch_resume() -> None:
    ready = {
        "preflight": {"status": "passed"},
        "policy_decision": {"decision": "auto_decide"},
    }
    actions = _prioritize_pending_actions([
        {
            **ready,
            "action": "workflow-batch-resume",
            "safe_resume_action": "reemit_candidate_ready",
            "checkpoint_id": "stale-batch",
        },
        {
            **ready,
            "action": SEMANTIC_REPLAN_ACTION,
            "safe_resume_action": "request_semantic_replan",
            "checkpoint_id": "current-contract-replan",
        },
    ])

    assert actions[0]["action"] == SEMANTIC_REPLAN_ACTION
    assert actions[1]["action"] == "workflow-batch-resume"


def test_manual_external_gate_preempts_auto_resume_and_diagnosis() -> None:
    ready = {
        "preflight": {"status": "passed"},
        "policy_decision": {"decision": "auto_decide"},
    }
    actions = _prioritize_pending_actions([
        {
            **ready,
            "action": "workflow-batch-resume",
            "safe_resume_action": "repair_failed_children",
            "checkpoint_id": "stale-batch",
        },
        {
            "action": "diagnose-attention",
            "safe_resume_action": "diagnose_attention",
            "checkpoint_id": "stale-diagnosis",
            "preflight": {"status": "passed"},
            "policy_decision": {"decision": "needs_diagnosis"},
        },
        {
            "action": "orchestrator-triage-advice-apply",
            "safe_resume_action": "blocked_external_gate",
            "checkpoint_id": "manual-gate",
            "failure_class": "manual_evidence_required",
            "preflight": {"status": "passed"},
            "policy_decision": {"decision": "human_escalate"},
        },
    ])

    assert actions[0]["checkpoint_id"] == "manual-gate"


def test_cross_task_superseded_replan_is_redriven_once() -> None:
    blocked = ZfEvent(id="failed-a", type="dev.blocked", task_id="A")
    triage = ZfEvent(id="triage-a", type="task.rework.triage.completed", task_id="A",
                     payload={"recommended_action": "request_replan", "retryable": False,
                              "failed_event_id": "failed-a"})
    events = [blocked, triage]
    original = pending_immediate_replan_actions(events)[0]
    events.extend([
        ZfEvent(type="run.manager.action.applied", payload={"checkpoint_id": original["checkpoint_id"]}),
        ZfEvent(id="request-a", type="flow.discovery.requested", payload={
            "schema_version": "semantic-replan-request.v1", "task_id": "A", "rework_of": "failed-a",
            "handoff_kind": "task_base_recovery", "workflow_run_id": "run-1"}),
        ZfEvent(type="fanout.started", payload={"fanout_id": "fanout-a", "trigger_event_id": "request-a"}),
        ZfEvent(id="request-b", type="flow.discovery.requested", payload={
            "schema_version": "semantic-replan-request.v1", "task_id": "B",
            "handoff_kind": "task_base_recovery", "workflow_run_id": "run-1"}),
        ZfEvent(type="fanout.started", payload={"fanout_id": "fanout-b", "trigger_event_id": "request-b"}),
        ZfEvent(id="cancel-a", type="fanout.cancelled", payload={
            "fanout_id": "fanout-a", "superseded_by": "fanout-b", "reason": "superseded_by_latest_fanout"}),
    ])
    redrive = pending_immediate_replan_actions(events)
    assert len(redrive) == 1
    assert redrive[0]["checkpoint_id"] != original["checkpoint_id"]
    assert redrive[0]["request_id"] != original["request_id"]
    assert "cancel-a" in redrive[0]["source_event_ids"]
    events.append(ZfEvent(type="run.manager.action.applied", payload={"checkpoint_id": redrive[0]["checkpoint_id"]}))
    assert pending_immediate_replan_actions(events) == []

    events.pop()
    events[-3].payload["workflow_run_id"] = "other-run"
    assert pending_immediate_replan_actions(events) == []
    events[-3].payload["workflow_run_id"] = "run-1"
    events.append(ZfEvent(type="task.done", task_id="A"))
    assert pending_immediate_replan_actions(events) == []
    events.pop()
    events[-3].payload["task_id"] = "A"
    assert pending_immediate_replan_actions(events) == []


def test_first_unsatisfiable_contract_routes_to_semantic_replan(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-ASSEMBLY",
        title="assemble product evidence",
        status="in_progress",
        assigned_to="dev-lane-1",
        contract=TaskContract(
            feature_id="PRD-SIM",
            behavior="produce passing browser evidence",
        ),
    ))
    task_map = state_dir / "artifacts" / "PRD-SIM" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(
        """{
  "schema_version": "task-map.v1",
  "pdd_id": "PRD-SIM",
  "tasks": [{
    "task_id": "TASK-ASSEMBLY",
    "title": "assemble product evidence",
    "owner_role": "dev-lane-1",
    "wave": 1,
    "allowed_paths": ["app/src/App.tsx"],
    "allowed_paths_reason": "entrypoint owner",
    "acceptance": ["passing browser trace exists"]
  }]
}
""",
        encoding="utf-8",
    )
    writer.emit(
        "task_map.ready",
        actor="zf-cli",
        correlation_id="sim-run",
        payload={
            "pdd_id": "PRD-SIM",
            "feature_id": "PRD-SIM",
            "task_map_ref": str(task_map),
            "source_index_ref": "artifacts/PRD-SIM/source_index.json",
            "target_ref": "main",
        },
    )
    blocked = writer.emit(
        "dev.blocked",
        actor="dev-lane-1",
        task_id="TASK-ASSEMBLY",
        correlation_id="sim-run",
        payload={
            "fanout_id": "fanout-impl",
            "child_id": "assembly-child",
            "failure_class": "task_contract_unsatisfiable",
            "recommended_action": "replan",
            "reason": (
                "the provided Python environment cannot import the package, "
                "while an upstream script rejects the required profile"
            ),
            "blocker_task_ids": ["TASK-BACKEND"],
            "evidence_refs": [
                "artifact:artifacts/contracts/assembly.json",
                "command:ASSEMBLY-SMOKE",
            ],
        },
    )
    triage_result = classify_rework_trigger(blocked)
    triage = writer.emit(
        "task.rework.triage.completed",
        actor="zf-cli",
        task_id="TASK-ASSEMBLY",
        correlation_id="sim-run",
        payload=triage_result.to_payload(blocked),
    )
    writer.emit(
        "fanout.aggregate.completed",
        actor="zf-cli",
        correlation_id="sim-run",
        payload={
            "fanout_id": "fanout-impl",
            "stage_id": "prd-lanes-impl",
            "status": "failed",
            "pdd_id": "PRD-SIM",
            "feature_id": "PRD-SIM",
            "task_map_ref": str(task_map),
            "failed_children": ["assembly-child"],
        },
    )
    writer.emit(
        "integration.failed",
        actor="zf-cli",
        correlation_id="sim-run",
        payload={
            "fanout_id": "fanout-impl",
            "stage_id": "prd-lanes-impl",
            "pdd_id": "PRD-SIM",
            "feature_id": "PRD-SIM",
            "task_map_ref": str(task_map),
            "target_ref": "main",
            "candidate_base_commit": "abc123",
            "failed_children": ["assembly-child"],
            "failed_task_ids": ["TASK-ASSEMBLY"],
        },
    )
    config = _config(with_orchestrator=True)
    config.roles.append(RoleConfig(
        name="flow-discovery",
        backend="mock",
        skills=["zf-gap-task-synth"],
    ))
    config.workflow.stages.append(WorkflowStageConfig(
        id="prd-post-impl-discovery",
        trigger="flow.discovery.requested",
        topology="fanout_reader",
        roles=["flow-discovery"],
    ))

    actions = pending_immediate_replan_actions(log.read_all())
    assert len(actions) == 1
    assert actions[0]["recorded_event_id"] == triage.id
    assert actions[0]["task_id"] == "TASK-ASSEMBLY"

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=config,
    )
    pending = projection["pending_actions"]
    assert pending[0]["action"] == SEMANTIC_REPLAN_ACTION
    assert not any(
        action["action"] == "candidate-rework-apply"
        for action in pending
    )
    assert pending[0]["semantic_replan_trigger"] == "flow.discovery.requested"
    assert not [
        action for action in pending
        if action.get("action") == "workflow-batch-resume"
    ]

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        spawn_repairs=False,
    )
    assert result.actions_applied == 1
    requests = [
        event for event in log.read_all()
        if event.type == "flow.discovery.requested"
    ]
    assert len(requests) == 1
    assert requests[0].payload["task_id"] == "TASK-ASSEMBLY"
    assert requests[0].payload["task_map_ref"] == str(task_map)
    assert blocked.id in requests[0].payload["failure_event_ids"]

    second = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        spawn_repairs=False,
    )
    assert second.actions_applied == 0
    assert len([
        event for event in log.read_all()
        if event.type == "flow.discovery.requested"
    ]) == 1


def test_required_manual_evidence_waits_at_recoverable_human_gate(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    required_ref = "artifacts/evidence/manual/acceptance.json"
    store.add(Task(
        id="TASK-MANUAL-AUDIT",
        title="run write-free release audit",
        status="in_progress",
        assigned_to="dev-lane-1",
        contract=TaskContract(
            feature_id="PRD-MANUAL",
            behavior="audit release evidence without fabricating human input",
            evidence_contract={"required_manual_evidence": required_ref},
        ),
    ))
    blocked = writer.emit(
        "dev.blocked",
        actor="dev-lane-1",
        task_id="TASK-MANUAL-AUDIT",
        correlation_id="manual-run",
        payload={
            "workflow_run_id": "manual-run",
            "failure_class": "upstream_contract_gap",
            "blocker_kind": "upstream_contract_gap",
            "reason": "independent human acceptance receipt is absent",
            "evidence_refs": ["command:release-audit#exit=1"],
        },
    )
    triage = writer.emit(
        "task.rework.triage.completed",
        actor="zf-cli",
        task_id="TASK-MANUAL-AUDIT",
        correlation_id="manual-run",
        payload={
            "task_id": "TASK-MANUAL-AUDIT",
            "failed_event_id": blocked.id,
            "classification": "design_issue",
            "recommended_action": "request_replan",
            "retryable": False,
        },
    )
    legacy_replan = pending_immediate_replan_actions(log.read_all())[0]
    assert legacy_replan["action"] == SEMANTIC_REPLAN_ACTION
    writer.emit(
        "run.manager.action.applied",
        actor="run-manager",
        task_id="TASK-MANUAL-AUDIT",
        payload={
            "checkpoint_id": legacy_replan["checkpoint_id"],
            "action": SEMANTIC_REPLAN_ACTION,
            "status": "applied",
        },
    )

    actions = pending_immediate_replan_actions(
        log.read_all(),
        task_store=store,
    )

    assert len(actions) == 1
    assert actions[0]["safe_resume_action"] == "blocked_external_gate"
    assert actions[0]["owner_route"] == "human"
    assert actions[0]["required_evidence_refs"] == [required_ref]
    assert actions[0]["recorded_event_id"] == triage.id

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(with_orchestrator=True),
        event_log=log,
        spawn_repairs=False,
    )

    assert result.actions_blocked == 1
    events = log.read_all()
    blocked_action = next(
        event
        for event in events
        if event.type == "run.manager.action.blocked"
    )
    escalation = next(event for event in events if event.type == "human.escalate")
    assert blocked_action.task_id == "TASK-MANUAL-AUDIT"
    assert blocked_action.correlation_id == "manual-run"
    assert blocked_action.payload["suggested_options"] == [
        "provide_required_evidence",
        "safe_halt",
    ]
    assert blocked_action.payload["question"] == (
        "请按任务合约提供独立人工验收证据，完成后由 operator 恢复重验。"
    )
    assert escalation.correlation_id == "manual-run"
    assert escalation.payload["run_id"] == "manual-run"
    assert escalation.payload["workflow_run_id"] == "manual-run"
    assert escalation.payload["failure_class"] == "manual_evidence_required"
    assert escalation.payload["blocker_kind"] == "external_gate"
    assert escalation.payload["required_evidence_refs"] == [required_ref]
    assert escalation.payload["resume_condition"] == (
        "required_manual_evidence_present_and_reverified"
    )
    assert not {
        "flow.discovery.requested",
        "verify.parity_scan.requested",
        "run.manager.autoresearch.requested",
        "run.goal.blocked",
    }.intersection(event.type for event in events)
    assert pending_immediate_replan_actions(
        events,
        task_store=store,
    ) == []


def test_manual_evidence_contract_does_not_capture_code_replan(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-CODE-GAP",
        title="repair code contract",
        status="in_progress",
        contract=TaskContract(
            evidence_contract={
                "required_manual_evidence": "artifacts/evidence/manual.json",
            },
        ),
    ))
    blocked = writer.emit(
        "dev.blocked",
        actor="dev",
        task_id="TASK-CODE-GAP",
        payload={
            "failure_class": "task_contract_unsatisfiable",
            "reason": "owned code paths cannot satisfy the current contract",
        },
    )
    writer.emit(
        "task.rework.triage.completed",
        actor="zf-cli",
        task_id="TASK-CODE-GAP",
        payload={
            "task_id": "TASK-CODE-GAP",
            "failed_event_id": blocked.id,
            "recommended_action": "request_replan",
            "retryable": False,
        },
    )

    actions = pending_immediate_replan_actions(
        log.read_all(),
        task_store=store,
    )

    assert len(actions) == 1
    assert actions[0]["action"] == SEMANTIC_REPLAN_ACTION


def test_semantic_replan_preserves_current_handoff_and_candidate_identity(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    task_map = state_dir / "artifacts" / "RF-PARITY" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(
        '{"schema_version":"task-map.v1","pdd_id":"RF-PARITY","tasks":[]}\n',
        encoding="utf-8",
    )
    generation = "generation-current"
    package_id = "planpkg-current"
    package_ref = "artifacts/plan-packages/current.json"
    package_digest = "a" * 64
    store.add(Task(
        id="TASK-PARITY",
        title="repair browser parity",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(
            feature_id="RF-PARITY",
            behavior="repair browser parity",
            acceptance_criteria=["browser parity is stable"],
            evidence_contract={
                "workflow_run_id": "rf-parity-run",
                "source_refs": {
                    "task_map_ref": str(task_map),
                    "task_map_generation": generation,
                    "plan_artifact_package_id": package_id,
                    "plan_artifact_package_ref": package_ref,
                    "plan_artifact_package_digest": package_digest,
                },
            },
        ),
    ))
    identity = current_task_contract_identity(
        store.get("TASK-PARITY"),
        task_map_ref=str(task_map),
    )
    writer.emit(
        "task_map.ready",
        actor="zf-cli",
        correlation_id="rf-parity-run",
        payload={
            "workflow_run_id": "rf-parity-run",
            "pdd_id": "RF-PARITY",
            "feature_id": "RF-PARITY",
            "task_map_ref": str(task_map),
            "source_index_ref": "docs/plans/rf-parity/source-index.json",
            "source_commit": "base123",
            "candidate_base_commit": "base123",
            "target_ref": "base123",
            "task_map_generation": generation,
            "plan_artifact_package_id": package_id,
            "plan_artifact_package_ref": package_ref,
            "plan_artifact_package_digest": package_digest,
        },
    )
    candidate = writer.emit(
        "candidate.ready",
        actor="zf-cli",
        correlation_id="rf-parity-run",
        payload={
            "workflow_run_id": "rf-parity-run",
            "pdd_id": "RF-PARITY",
            "feature_id": "RF-PARITY",
            "task_map_ref": str(task_map),
            "candidate_ref": "candidate/RF-PARITY",
            "candidate_head_commit": "candidate123",
            "candidate_base_commit": "base123",
            "completed_task_ids": ["TASK-PARITY"],
            "task_map_generation": generation,
        },
    )
    blocked = writer.emit(
        "dev.blocked",
        actor="dev",
        task_id="TASK-PARITY",
        correlation_id="rf-parity-run",
        payload={
            "workflow_run_id": "rf-parity-run",
            "task_id": "TASK-PARITY",
            "failure_class": "task_contract_unsatisfiable",
            "reason": "browser evidence is outside the current task allowlist",
            "contract_revision": identity["contract_revision"],
            "task_map_generation": generation,
            "task_ref": "task/TASK-PARITY",
            "base_commit": "base123",
            "contract_snapshot_ref": "artifacts/contracts/current.json",
            "contract_snapshot_digest": "b" * 64,
            "plan_artifact_package_id": package_id,
            "plan_artifact_package_ref": package_ref,
            "plan_artifact_package_digest": package_digest,
            "workflow_proposal_ref": {"ref": "artifacts/proposals/current.json"},
            "workflow_proposal_digest": "c" * 64,
            "effective_config_ref": {"ref": "artifacts/config/current.json"},
            "effective_config_digest": "d" * 64,
            "run_contract_ref": "artifacts/run-contracts/current.json",
            "run_contract_digest": "e" * 64,
            "goal_claim_set_ref": "artifacts/claims/current.json",
            "goal_claim_set_digest": "f" * 64,
        },
    )
    writer.emit(
        "fanout.child.failed",
        actor="zf-cli",
        causation_id=blocked.id,
        correlation_id="rf-parity-run",
        payload={
            "workflow_run_id": "rf-parity-run",
            "task_id": "TASK-PARITY",
            "contract_revision": identity["contract_revision"],
            "task_map_generation": generation,
            "base_commit": "candidate123",
            "continuation_commit": "candidate123",
            "continuation_ref": "worker/dev",
        },
    )
    writer.emit(
        "task.rework.triage.completed",
        actor="zf-cli",
        task_id="TASK-PARITY",
        correlation_id="rf-parity-run",
        payload={
            "task_id": "TASK-PARITY",
            "failed_event_id": blocked.id,
            "failed_event_type": blocked.type,
            "classification": "design_issue",
            "suspected_owner": "planner",
            "recommended_action": "request_replan",
            "retryable": False,
            "is_terminal": False,
            "notes": "task contract cannot contain the required parity repair",
        },
    )
    config = _config(with_orchestrator=True)
    config.roles.append(RoleConfig(
        name="module-parity-scan",
        backend="mock",
        skills=["zf-gap-task-synth"],
    ))
    config.workflow.stages.append(WorkflowStageConfig(
        id="flow-module-parity-scan",
        trigger="verify.parity_scan.requested",
        topology="fanout_reader",
        roles=["module-parity-scan"],
        flow_kind="refactor",
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        spawn_repairs=False,
    )

    assert result.actions_applied == 1
    requests = [
        event for event in log.read_all()
        if event.type == "verify.parity_scan.requested"
    ]
    assert len(requests) == 1
    assert requests[0].task_id is None
    payload = requests[0].payload
    assert payload["task_id"] == "TASK-PARITY"
    assert payload["rework_of"] == blocked.id
    assert payload["flow_kind"] == "refactor"
    assert payload["workflow_run_id"] == "rf-parity-run"
    assert payload["contract_revision"] == identity["contract_revision"]
    assert payload["task_map_generation"] == generation
    assert payload["contract_snapshot_ref"] == "artifacts/contracts/current.json"
    assert payload["contract_snapshot_digest"] == "b" * 64
    assert payload["plan_artifact_package_id"] == package_id
    assert payload["candidate_event_id"] == candidate.id
    assert payload["candidate_head_commit"] == "candidate123"
    assert payload["target_ref"] == "candidate/RF-PARITY"
    assert payload["workflow_proposal_ref"] == {
        "ref": "artifacts/proposals/current.json",
    }


def test_semantic_replan_keeps_admitted_task_map_over_stale_candidate(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    old_task_map = state_dir / "artifacts" / "PRD-MAP" / "task-map-v1.json"
    current_task_map = state_dir / "artifacts" / "PRD-MAP" / "task-map-v2.json"
    old_task_map.parent.mkdir(parents=True)
    old_task_map.write_text(
        '{"schema_version":"task-map.v1","tasks":[]}\n',
        encoding="utf-8",
    )
    current_task_map.write_text(
        '{"schema_version":"task-map.v1","tasks":[]}\n',
        encoding="utf-8",
    )
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-MAP-CURRENT",
        title="repair current task map",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(
            feature_id="PRD-MAP",
            behavior="repair current task map",
            evidence_contract={
                "source_refs": {"task_map_ref": str(old_task_map)},
            },
        ),
    ))
    writer.emit(
        "task_map.admitted",
        actor="zf-cli",
        correlation_id="prd-map-run",
        payload={
            "pdd_id": "PRD-MAP",
            "feature_id": "PRD-MAP",
            "task_map_ref": str(current_task_map),
        },
    )
    writer.emit(
        "candidate.ready",
        actor="zf-cli",
        correlation_id="prd-map-run",
        payload={
            "pdd_id": "PRD-MAP",
            "feature_id": "PRD-MAP",
            "task_map_ref": str(old_task_map),
            "candidate_ref": "candidate/PRD-MAP",
            "candidate_head_commit": "candidate-current",
        },
    )
    config = _config()
    config.roles.append(RoleConfig(
        name="flow-discovery",
        backend="mock",
        skills=["zf-gap-task-synth"],
    ))
    config.workflow.stages.append(WorkflowStageConfig(
        id="prd-post-verify-discovery",
        trigger="flow.discovery.requested",
        topology="fanout_reader",
        roles=["flow-discovery"],
        flow_kind="prd",
    ))

    action = enrich_semantic_replan_action(
        {
            "action": SEMANTIC_REPLAN_ACTION,
            "task_id": "TASK-MAP-CURRENT",
            "source_event_id": "blocked-current-map",
        },
        state_dir=state_dir,
        events=log.read_all(),
        config=config,
        project_root=tmp_path,
    )

    assert action["task_map_ref"] == str(current_task_map)
    assert action["candidate_head_commit"] == "candidate-current"


def test_semantic_replan_does_not_target_candidate_behind_task_base(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "zf@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "ZaoFu Test"],
        check=True,
    )
    (tmp_path / "product.txt").write_text("candidate\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "product.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "candidate"],
        check=True,
    )
    candidate_head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (tmp_path / "product.txt").write_text("task base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "product.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "task base"],
        check=True,
    )
    task_base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    state_dir, log, writer = _state(tmp_path)
    task_map = state_dir / "artifacts" / "PRD-BASE" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(
        '{"schema_version":"task-map.v1","pdd_id":"PRD-BASE","tasks":[]}\n',
        encoding="utf-8",
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-BASE",
        title="continue from task base",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(
            feature_id="PRD-BASE",
            behavior="continue from task base",
            acceptance_criteria=["remaining gap closes"],
            evidence_contract={
                "workflow_run_id": "prd-base-run",
                "source_refs": {"task_map_ref": str(task_map)},
            },
        ),
    ))
    identity = current_task_contract_identity(
        store.get("TASK-BASE"),
        task_map_ref=str(task_map),
    )
    writer.emit(
        "candidate.ready",
        actor="zf-cli",
        correlation_id="prd-base-run",
        payload={
            "workflow_run_id": "prd-base-run",
            "pdd_id": "PRD-BASE",
            "task_map_ref": str(task_map),
            "candidate_ref": candidate_head,
            "candidate_head_commit": candidate_head,
            "candidate_base_commit": candidate_head,
        },
    )
    blocked = writer.emit(
        "dev.blocked",
        actor="dev",
        task_id="TASK-BASE",
        correlation_id="prd-base-run",
        payload={
            "workflow_run_id": "prd-base-run",
            "task_id": "TASK-BASE",
            "contract_revision": identity["contract_revision"],
            "task_map_generation": identity["task_map_generation"],
            "task_ref": "task/TASK-BASE",
            "base_commit": task_base,
        },
    )

    config = _config()
    config.roles.append(RoleConfig(
        name="prd-flow-discovery",
        backend="mock",
        skills=["zf-gap-task-synth"],
    ))
    config.workflow.stages.append(WorkflowStageConfig(
        id="prd-post-verify-discovery",
        trigger="flow.discovery.requested",
        topology="fanout_reader",
        roles=["prd-flow-discovery"],
        flow_kind="prd",
    ))
    action = enrich_semantic_replan_action(
        {
            "action": SEMANTIC_REPLAN_ACTION,
            "task_id": "TASK-BASE",
            "source_event_id": blocked.id,
            "failure_event_ids": [blocked.id],
        },
        state_dir=state_dir,
        events=log.read_all(),
        config=config,
        project_root=tmp_path,
    )

    assert action["base_commit"] == task_base
    assert action["target_ref"] == task_base
    assert action["handoff_kind"] == "task_base_recovery"
    assert action["previous_candidate_ref"] == candidate_head
    assert action["previous_candidate_head_commit"] == candidate_head
    assert action["candidate_head_commit"] == candidate_head


def test_semantic_replan_without_candidate_targets_immutable_task_base(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    task_map = state_dir / "artifacts" / "PRD-NO-CANDIDATE" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(
        '{"schema_version":"task-map.v1","pdd_id":"PRD-NO-CANDIDATE","tasks":[]}\n',
        encoding="utf-8",
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-WEB",
        title="repair a missing API predecessor",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(
            feature_id="PRD-NO-CANDIDATE",
            behavior="repair a missing API predecessor",
            acceptance_criteria=["the Web path loads an authoritative sample"],
            evidence_contract={
                "workflow_run_id": "prd-no-candidate-run",
                "source_refs": {"task_map_ref": str(task_map)},
            },
        ),
    ))
    identity = current_task_contract_identity(
        store.get("TASK-WEB"),
        task_map_ref=str(task_map),
    )
    writer.emit(
        "task_map.ready",
        actor="zf-cli",
        correlation_id="prd-no-candidate-run",
        payload={
            "workflow_run_id": "prd-no-candidate-run",
            "pdd_id": "PRD-NO-CANDIDATE",
            "task_map_ref": str(task_map),
            "target_ref": "HEAD",
        },
    )
    blocked = writer.emit(
        "dev.blocked",
        actor="dev",
        task_id="TASK-WEB",
        correlation_id="prd-no-candidate-run",
        payload={
            "workflow_run_id": "prd-no-candidate-run",
            "task_id": "TASK-WEB",
            "contract_revision": identity["contract_revision"],
            "task_map_generation": identity["task_map_generation"],
            "task_ref": "task/TASK-WEB",
            "base_commit": "task-base-commit",
        },
    )
    config = _config()
    config.roles.append(RoleConfig(
        name="prd-flow-discovery",
        backend="mock",
        skills=["zf-gap-task-synth"],
    ))
    config.workflow.stages.append(WorkflowStageConfig(
        id="prd-post-verify-discovery",
        trigger="flow.discovery.requested",
        topology="fanout_reader",
        roles=["prd-flow-discovery"],
        flow_kind="prd",
    ))

    action = enrich_semantic_replan_action(
        {
            "action": SEMANTIC_REPLAN_ACTION,
            "task_id": "TASK-WEB",
            "source_event_id": blocked.id,
            "failure_event_ids": [blocked.id],
        },
        state_dir=state_dir,
        events=log.read_all(),
        config=config,
        project_root=tmp_path,
    )

    assert "candidate_ref" not in action
    assert action["base_commit"] == "task-base-commit"
    assert action["target_ref"] == "task-base-commit"
    assert action["handoff_kind"] == "task_base_recovery"


def test_semantic_replan_prefers_failed_task_committed_continuation(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    task_map = state_dir / "artifacts" / "RF-CONT" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(
        '{"schema_version":"task-map.v1","pdd_id":"RF-CONT","tasks":[]}\n',
        encoding="utf-8",
    )
    generation = "generation-continuation"
    store.add(Task(
        id="TASK-CONT",
        title="continue accepted partial delivery",
        status="in_progress",
        assigned_to="dev",
        contract=TaskContract(
            feature_id="RF-CONT",
            behavior="continue accepted partial delivery",
            acceptance_criteria=["delivery closes the remaining contract gap"],
            evidence_contract={
                "workflow_run_id": "rf-cont-run",
                "source_refs": {
                    "task_map_ref": str(task_map),
                    "task_map_generation": generation,
                },
            },
        ),
    ))
    identity = current_task_contract_identity(
        store.get("TASK-CONT"),
        task_map_ref=str(task_map),
    )
    writer.emit(
        "task_map.ready",
        actor="zf-cli",
        correlation_id="rf-cont-run",
        payload={
            "workflow_run_id": "rf-cont-run",
            "pdd_id": "RF-CONT",
            "feature_id": "RF-CONT",
            "task_map_ref": str(task_map),
            "source_commit": "base123",
            "candidate_base_commit": "base123",
            "task_map_generation": generation,
        },
    )
    candidate = writer.emit(
        "candidate.ready",
        actor="zf-cli",
        correlation_id="rf-cont-run",
        payload={
            "workflow_run_id": "rf-cont-run",
            "pdd_id": "RF-CONT",
            "feature_id": "RF-CONT",
            "task_map_ref": str(task_map),
            "candidate_ref": "candidate/RF-CONT",
            "candidate_head_commit": "candidate123",
            "candidate_base_commit": "base123",
            "task_map_generation": generation,
        },
    )
    blocked = writer.emit(
        "dev.blocked",
        actor="dev",
        task_id="TASK-CONT",
        correlation_id="rf-cont-run",
        payload={
            "workflow_run_id": "rf-cont-run",
            "task_id": "TASK-CONT",
            "failure_class": "task_contract_unsatisfiable",
            "reason": "one required path is outside the current contract",
            "contract_revision": identity["contract_revision"],
            "task_map_generation": generation,
            "task_ref": "task/TASK-CONT",
            "base_commit": "candidate123",
        },
    )
    failed = writer.emit(
        "fanout.child.failed",
        actor="zf-cli",
        causation_id=blocked.id,
        correlation_id="rf-cont-run",
        payload={
            "workflow_run_id": "rf-cont-run",
            "task_id": "TASK-CONT",
            "failure_class": "task_contract_unsatisfiable",
            "contract_revision": identity["contract_revision"],
            "task_map_generation": generation,
            "task_ref": "task/TASK-CONT",
            "base_commit": "candidate123",
            "source_branch": "worker/dev-1",
            "partial_head_commit": "partial456",
            "partial_source_branch": "worker/dev-1",
            "partial_worktree_clean": True,
            "partial_dirty_files": [],
            "continuation_commit": "partial456",
            "continuation_ref": "worker/dev-1",
        },
    )
    writer.emit(
        "fanout.child.failed",
        actor="zf-cli",
        causation_id="unrelated-failure",
        correlation_id="rf-cont-run",
        payload={
            "workflow_run_id": "rf-cont-run",
            "task_id": "TASK-CONT",
            "contract_revision": identity["contract_revision"],
            "task_map_generation": generation,
            "continuation_commit": "stale789",
            "continuation_ref": "worker/stale",
        },
    )
    writer.emit(
        "task.rework.triage.completed",
        actor="zf-cli",
        task_id="TASK-CONT",
        correlation_id="rf-cont-run",
        payload={
            "task_id": "TASK-CONT",
            "failed_event_id": blocked.id,
            "failed_event_type": blocked.type,
            "classification": "design_issue",
            "suspected_owner": "planner",
            "recommended_action": "request_replan",
            "retryable": False,
            "is_terminal": False,
        },
    )
    config = _config(with_orchestrator=True)
    config.roles.append(RoleConfig(
        name="module-parity-scan",
        backend="mock",
        skills=["zf-gap-task-synth"],
    ))
    config.workflow.stages.append(WorkflowStageConfig(
        id="flow-module-parity-scan",
        trigger="verify.parity_scan.requested",
        topology="fanout_reader",
        roles=["module-parity-scan"],
        flow_kind="refactor",
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        spawn_repairs=False,
    )

    assert result.actions_applied == 1
    request = next(
        event
        for event in log.read_all()
        if event.type == "verify.parity_scan.requested"
    )
    payload = request.payload
    assert payload["candidate_event_id"] == candidate.id
    assert payload["previous_candidate_ref"] == "candidate/RF-CONT"
    assert payload["previous_candidate_head_commit"] == "candidate123"
    assert payload["handoff_kind"] == "partial_task_continuation"
    assert payload["continuation_source_event_id"] == failed.id
    assert payload["continuation_commit"] == "partial456"
    assert payload["continuation_ref"] == "worker/dev-1"
    assert payload["candidate_ref"] == "worker/dev-1"
    assert payload["candidate_head_commit"] == "partial456"
    assert payload["target_ref"] == "partial456"
    assert payload["output_profile_id"] == "global-rescan"
    assert payload["output_profile_revision"] == "1"
    assert payload["source_commit"] == "base123"
    assert payload["candidate_base_commit"] == "base123"


def test_immediate_replan_suppression_uses_latest_task_triage() -> None:
    blocked = ZfEvent(
        id="blocked-1",
        type="dev.blocked",
        task_id="TASK-1",
        payload={"failure_class": "task_contract_unsatisfiable"},
    )
    request_replan = ZfEvent(
        id="triage-1",
        type="task.rework.triage.completed",
        task_id="TASK-1",
        payload={
            "failed_event_id": blocked.id,
            "recommended_action": "request_replan",
            "retryable": False,
        },
    )
    retry_current_contract = ZfEvent(
        id="triage-2",
        type="task.rework.triage.completed",
        task_id="TASK-1",
        payload={
            "failed_event_id": "blocked-2",
            "recommended_action": "dispatch_rework",
            "retryable": True,
        },
    )

    assert active_immediate_replan_task_ids([blocked, request_replan]) == {
        "TASK-1"
    }
    assert active_immediate_replan_task_ids([
        blocked,
        request_replan,
        retry_current_contract,
    ]) == set()
    assert pending_immediate_replan_actions([
        blocked,
        request_replan,
        retry_current_contract,
    ]) == []


def test_legacy_typed_scope_blocker_promotes_stale_evidence_triage() -> None:
    blocked = ZfEvent(
        id="blocked-legacy",
        type="dev.blocked",
        task_id="TASK-1",
        payload={
            "summary": (
                "The upstream simulation controls are outside this task scope."
            ),
            "evidence_refs": ["file:src/upstream-controls.ts"],
            "known_gaps": [
                "The upstream producer is outside this task's allowed paths."
            ],
            "impl_self_check": {
                "acceptance_results": [{
                    "acceptance_id": "AC15",
                    "status": "blocked",
                    "evidence_refs": ["file:src/upstream-controls.ts"],
                    "residual_risks": [
                        "Correcting the producer is outside this task scope."
                    ],
                }],
            },
        },
    )
    stale_triage = ZfEvent(
        id="triage-legacy",
        type="task.rework.triage.completed",
        task_id="TASK-1",
        payload={
            "failed_event_id": blocked.id,
            "classification": "evidence_payload_gap",
            "recommended_action": "request_evidence_reissue",
            "retryable": False,
        },
    )

    actions = pending_immediate_replan_actions([blocked, stale_triage])

    assert len(actions) == 1
    assert actions[0]["action"] == SEMANTIC_REPLAN_ACTION
    assert actions[0]["source_event_id"] == blocked.id
    assert active_immediate_replan_task_ids([blocked, stale_triage]) == {
        "TASK-1"
    }


def test_immediate_replan_suppression_ends_when_task_map_supersedes_task() -> None:
    request_replan = ZfEvent(
        id="triage-1",
        type="task.rework.triage.completed",
        task_id="TASK-1",
        payload={
            "failed_event_id": "blocked-1",
            "recommended_action": "request_replan",
            "retryable": False,
        },
    )
    replacement = ZfEvent(
        id="task-map-2",
        type="task_map.ready",
        payload={
            "task_map_generation": 2,
            "supersedes_task_ids": ["TASK-1"],
        },
    )

    assert active_immediate_replan_task_ids([
        request_replan,
        replacement,
    ]) == set()
    assert pending_immediate_replan_actions([
        request_replan,
        replacement,
    ]) == []


def test_l1_dispatches_first_two_failures_and_stops_at_third(tmp_path: Path) -> None:
    transport = TmuxTransport(TmuxSession(session_name="triage", dry_run=True))

    first_state, first_log, _ = _state(tmp_path / "first")
    first_store = TaskStore(first_state / "kanban.json")
    first_store.add(Task(
        id="TASK-1",
        title="fix expiry",
        status="review",
        assigned_to="dev",
        retry_count=2,
        contract=TaskContract(behavior="expiry remains enforced"),
    ))
    first_log.append(_failure(1))
    first_log.append(_failure(2))
    first_orchestrator = Orchestrator(first_state, _config(), transport)

    assert first_orchestrator._dispatch_rework(
        first_store.get("TASK-1"),
        _failure(2),
    ) == "dev"
    assert not any(
        event.type == "task.rework.capped" for event in first_log.read_all()
    )

    third_state, third_log, _ = _state(tmp_path / "third")
    third_store = TaskStore(third_state / "kanban.json")
    third_store.add(Task(
        id="TASK-1",
        title="fix expiry",
        status="review",
        assigned_to="dev",
        retry_count=3,
        contract=TaskContract(behavior="expiry remains enforced"),
    ))
    for round_number in range(1, 4):
        third_log.append(_failure(round_number))
    third_orchestrator = Orchestrator(third_state, _config(), transport)

    assert third_orchestrator._dispatch_rework(
        third_store.get("TASK-1"),
        _failure(3),
    ) is None
    capped = [
        event for event in third_log.read_all()
        if event.type == "task.rework.capped"
    ]
    assert len(capped) == 1
    assert capped[0].payload["failure_count"] == 3
    assert capped[0].payload["semantic_triage_required"] is True
    assert not any(event.type == "human.escalate" for event in third_log.read_all())


def test_run_manager_requests_and_applies_precise_rework_advice(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="TASK-1",
        title="fix expiry",
        status="review",
        assigned_to="dev",
    ))
    log.append(ZfEvent(
        id="cap-1",
        type="task.rework.capped",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "failure_fingerprint": "missing-expiry-test",
            "failure_count": 3,
            "role": "dev",
            "flow_kind": "issue",
            "workflow_run_id": "run-issue-1",
            "stage_id": "issue-verify",
            "failure_event_ids": ["failure-1", "failure-2", "failure-3"],
            "semantic_triage_required": True,
            "last_reason": "missing expiry test",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(with_orchestrator=True),
    )
    assert projection["pending_actions"][0]["action"] == "orchestrator-rework-triage"
    assert projection["pending_actions"][0]["policy_decision"]["decision"] == "auto_decide"
    assert projection["pending_actions"][0]["flow_kind"] == "issue"
    assert projection["pending_actions"][0]["workflow_run_id"] == "run-issue-1"

    first = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(with_orchestrator=True),
        event_log=log,
        spawn_repairs=False,
    )
    assert first.actions_applied == 1
    request = next(event for event in log.read_all() if event.type == TRIAGE_REQUESTED)
    assert request.actor == "run-manager"
    assert request.payload["apply_policy"] == "proposal_only"
    assert request.payload["flow_kind"] == "issue"
    assert request.payload["stage_id"] == "issue-verify"

    log.append(ZfEvent(
        type=TRIAGE_RECORDED,
        actor="orchestrator",
        task_id="TASK-1",
        payload={
            "request_id": request.payload["request_id"],
            "failure_fingerprint": "missing-expiry-test",
            "recommended_action": "precise_rework",
            "guidance": "add the missing expiry regression evidence",
            "apply_policy": "proposal_only",
        },
    ))
    assert task_store.get("TASK-1").status == "review"
    second = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(with_orchestrator=True),
        event_log=log,
        spawn_repairs=False,
    )

    assert second.actions_applied == 1
    reworks = [
        event for event in log.read_all()
        if event.type == "task.rework.requested"
    ]
    assignments = [
        event for event in log.read_all()
        if event.type == "task.assigned"
    ]
    assert len(reworks) == 1
    assert len(assignments) == 1
    assert reworks[0].actor == "run-manager"
    assert reworks[0].payload["recommended_action"] == "precise_rework"
    assert not any(event.type == "human.escalate" for event in log.read_all())
    assert not any(
        event.type == "orchestrator.replan_requested" for event in log.read_all()
    )
    task = task_store.get("TASK-1")
    assert task.status == "in_progress"
    assert task.assigned_to == "dev"


def test_semantic_triage_suppresses_same_task_attention_diagnosis(
    tmp_path: Path,
) -> None:
    state_dir, log, _writer = _state(tmp_path)
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="fix expiry",
        status="review",
        assigned_to="dev",
    ))
    log.append(ZfEvent(
        id="cap-owned",
        type="task.rework.capped",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "failure_fingerprint": "same-task-failure",
            "failure_count": 3,
            "failure_event_ids": ["f1", "f2", "f3"],
            "semantic_triage_required": True,
        },
    ))
    log.append(ZfEvent(
        id="attention-owned",
        type="runtime.attention.needed",
        actor="zf-supervisor",
        task_id="TASK-1",
        payload={
            "attention_id": "attn-same-task",
            "fingerprint": "repeated:review.rejected:TASK-1",
            "title": "Repeated review.rejected",
            "summary": "three failures",
            "suggested_route": "run_manager_recovery",
            "source_event_ids": ["f1", "f2", "f3"],
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(with_orchestrator=True),
    )

    same_task_actions = [
        action for action in projection["pending_actions"]
        if str(action.get("task_id") or "") == "TASK-1"
    ]
    assert [action["action"] for action in same_task_actions] == [
        "orchestrator-rework-triage"
    ]


def test_split_task_advice_requires_artifact_diagnosis_before_apply(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="TASK-1",
        title="fix expiry",
        status="review",
        assigned_to="dev",
    ))
    log.append(ZfEvent(
        id="cap-split",
        type="task.rework.capped",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "role": "dev",
            "failure_fingerprint": "mixed-responsibilities",
            "failure_count": 3,
            "failure_event_ids": ["failure-1", "failure-2", "failure-3"],
        },
    ))
    run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(with_orchestrator=True),
        event_log=log,
        spawn_repairs=False,
    )
    request = next(event for event in log.read_all() if event.type == TRIAGE_REQUESTED)
    log.append(ZfEvent(
        type=TRIAGE_RECORDED,
        actor="orchestrator",
        task_id="TASK-1",
        payload={
            "request_id": request.payload["request_id"],
            "failure_fingerprint": "mixed-responsibilities",
            "recommended_action": "split_task",
            "guidance": "materialize two scoped gap tasks",
            "apply_policy": "proposal_only",
        },
    ))

    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=_config(with_orchestrator=True),
        event_log=log,
        spawn_repairs=False,
    )

    assert result.autoresearch_requested == 1
    assert task_store.get("TASK-1").status == "review"
    assert not any(
        event.type in {
            "orchestrator.replan_requested",
            "task.rework.requested",
            "task.assigned",
        }
        for event in log.read_all()
    )


def test_unanswered_triage_falls_back_to_run_manager_diagnosis() -> None:
    now = datetime.now(timezone.utc)
    request_id = "ortriage-request"
    events = [
        ZfEvent(
            type="task.rework.capped",
            task_id="TASK-1",
            ts=(now - timedelta(minutes=10)).isoformat(),
            payload={
                "failure_fingerprint": "same-gap",
                "failure_count": 3,
                "failure_event_ids": ["f1", "f2", "f3"],
            },
        ),
        ZfEvent(
            type=TRIAGE_REQUESTED,
            task_id="TASK-1",
            ts=(now - timedelta(minutes=9)).isoformat(),
            payload={
                "request_id": request_id,
                "failure_fingerprint": "same-gap",
            },
        ),
    ]
    # Use the deterministic request id derived by the builder.
    initial = pending_rework_triage_actions(
        events[:1], threshold=3, stale_seconds=300, now=now,
    )[0]
    events[1].payload["request_id"] = initial["request_id"]

    actions = pending_rework_triage_actions(
        events,
        threshold=3,
        stale_seconds=300,
        now=now,
    )

    assert len(actions) == 1
    assert actions[0]["action"] == "diagnose-attention"
    assert actions[0]["failure_class"] == "orchestrator_triage_timeout"
    assert actions[0]["owner_route"] == "run_manager"


def test_missing_orchestrator_advisor_falls_back_without_waiting(
    tmp_path: Path,
) -> None:
    state_dir, log, _ = _state(tmp_path)
    log.append(ZfEvent(
        id="cap-no-advisor",
        type="task.rework.capped",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "failure_fingerprint": "same-gap",
            "failure_count": 3,
            "failure_event_ids": ["f1", "f2", "f3"],
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(),
    )

    action = projection["pending_actions"][0]
    assert action["action"] == "diagnose-attention"
    assert action["failure_class"] == "orchestrator_triage_unavailable"
    assert action["policy_decision"]["decision"] == "needs_diagnosis"
    assert not any(event.type == TRIAGE_REQUESTED for event in log.read_all())


def test_task_pipeline_semantic_exhaustion_uses_orchestrator_triage(
    tmp_path: Path,
) -> None:
    state_dir, log, _ = _state(tmp_path)
    log.append(ZfEvent(
        id="pipeline-semantic-cap",
        type="task.pipeline.semantic_rework.exhausted",
        actor="zf-runtime",
        task_id="TASK-1",
        correlation_id="RUN-1",
        payload={
            "semantic_triage_contract_version": 1,
            "task_id": "TASK-1",
            "workflow_run_id": "RUN-1",
            "stage_id": "verify",
            "failure_class": "task_pipeline_semantic_rework_exhausted",
            "failure_fingerprint": "task-pipeline-semantic:TASK-1:verify:blocked",
            "failure_count": 3,
            "retry_count": 2,
            "failure_event_ids": ["verify-1"],
            "semantic_triage_required": True,
            "last_reason": "verify exhausted two semantic reworks",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(with_orchestrator=True),
    )

    actions = [
        item for item in projection["pending_actions"]
        if item.get("task_id") == "TASK-1"
    ]
    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "orchestrator-rework-triage"
    assert action["failure_class"] == (
        "task_pipeline_semantic_rework_exhausted"
    )
    assert action["policy_decision"]["decision"] == "auto_decide"
    assert action["expected_downstream_events"] == [TRIAGE_REQUESTED]


def test_aggregate_retry_cap_uses_generic_diagnosis_not_semantic_triage(
    tmp_path: Path,
) -> None:
    state_dir, log, _ = _state(tmp_path)
    log.append(ZfEvent(
        id="cap-mixed-fingerprints",
        type="task.rework.capped",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "failure_fingerprint": "latest-only",
            "failure_count": 1,
            "retry_count": 4,
            "semantic_triage_required": False,
            "recovery_owner": "run_manager",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=_config(with_orchestrator=True),
    )

    assert not [
        item for item in projection["pending_actions"]
        if item.get("action") == "orchestrator-rework-triage"
    ]
    assert [
        item for item in projection["pending_actions"]
        if "cap-mixed-fingerprints" in item.get("source_event_ids", [])
        and item.get("policy_decision", {}).get("decision") == "needs_diagnosis"
    ]


def test_missing_orchestrator_uses_live_resident_as_semantic_advisor() -> None:
    events = [ZfEvent(
        id="cap-resident",
        type="task.rework.capped",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "role": "dev",
            "failure_fingerprint": "same-gap",
            "failure_count": 3,
            "failure_event_ids": ["f1", "f2", "f3"],
        },
    )]

    actions = pending_rework_triage_actions(
        events,
        threshold=3,
        stale_seconds=300,
        advisor_available=False,
        resident_advisor={
            "status": "running",
            "tmux_session": "zf-run-manager",
            "briefing_path": "/tmp/run-manager-briefing.md",
            "instance_id": "run-manager",
        },
    )

    assert len(actions) == 1
    assert actions[0]["action"] == "resident-agent-reprompt"
    assert actions[0]["semantic_triage_request_id"]
    assert actions[0]["expected_output"] == [TRIAGE_RECORDED]


def test_resident_semantic_advisor_timeout_falls_back_to_diagnosis() -> None:
    cap = ZfEvent(
        id="cap-resident",
        type="task.rework.capped",
        ts="2026-07-10T12:00:00+00:00",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "failure_fingerprint": "same-gap",
            "failure_count": 3,
            "failure_event_ids": ["f1", "f2", "f3"],
        },
    )
    resident = {
        "status": "running",
        "tmux_session": "zf-run-manager",
        "briefing_path": "/tmp/run-manager-briefing.md",
        "instance_id": "run-manager",
    }
    initial = pending_rework_triage_actions(
        [cap],
        threshold=3,
        stale_seconds=300,
        advisor_available=False,
        resident_advisor=resident,
    )[0]
    applied = ZfEvent(
        id="resident-applied",
        type="run.manager.action.applied",
        ts="2026-07-10T12:00:01+00:00",
        actor="run-manager",
        payload={"checkpoint_id": initial["checkpoint_id"]},
    )

    assert pending_rework_triage_actions(
        [cap, applied],
        threshold=3,
        stale_seconds=300,
        advisor_available=False,
        resident_advisor=resident,
        now=datetime(2026, 7, 10, 12, 4, tzinfo=timezone.utc),
    ) == []

    actions = pending_rework_triage_actions(
        [cap, applied],
        threshold=3,
        stale_seconds=300,
        advisor_available=False,
        resident_advisor=resident,
        now=datetime(2026, 7, 10, 12, 6, tzinfo=timezone.utc),
    )

    assert len(actions) == 1
    assert actions[0]["action"] == "diagnose-attention"
    assert actions[0]["failure_class"] == "resident_orchestrator_triage_timeout"
    assert actions[0]["source_event_id"] == "resident-applied"


def test_resident_triage_advice_counts_as_prompt_response(tmp_path: Path) -> None:
    state_dir, _, _ = _state(tmp_path)
    events = [
        ZfEvent(
            id="resident-spawned",
            type="run.manager.resident.spawned",
            ts="2026-07-10T12:00:00+00:00",
            payload={"ready": True},
        ),
        ZfEvent(
            id="resident-prompted",
            type="run.manager.resident.prompted",
            ts="2026-07-10T12:00:01+00:00",
            payload={"prompted": True},
        ),
        ZfEvent(
            id="resident-triage",
            type=TRIAGE_RECORDED,
            ts="2026-07-10T12:00:02+00:00",
            payload={
                "request_id": "request-1",
                "recommended_action": "precise_rework",
            },
        ),
    ]

    projection = build_run_manager_projection(
        state_dir,
        events=events,
        config=_config(),
    )

    assert projection["resident_agent"]["status"] == "observing"
    assert projection["resident_agent"]["latest_agent_event_id"] == "resident-triage"


def test_split_task_advice_routes_to_declared_gap_planner_and_writes_context(
    tmp_path: Path,
) -> None:
    state_dir, log, writer = _state(tmp_path)
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(
        id="TASK-1",
        title="fix expiry",
        status="review",
        assigned_to="dev",
        contract=TaskContract(
            feature_id="ISSUE-1",
            behavior="expiry remains enforced",
            plan_ref="docs/plans/issue-1.md",
        ),
    ))
    task_map = state_dir / "artifacts" / "ISSUE-1" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text("{}\n", encoding="utf-8")
    log.append(ZfEvent(
        id="task-map-1",
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "ISSUE-1",
            "feature_id": "ISSUE-1",
            "task_map_ref": str(task_map),
            "source_index_ref": "docs/plans/issue-1-source-index.json",
            "source_commit": "abc123",
            "candidate_base_commit": "abc123",
        },
    ))
    log.append(ZfEvent(
        id="cap-semantic",
        type="task.rework.capped",
        actor="zf-cli",
        task_id="TASK-1",
        payload={
            "role": "dev",
            "failure_fingerprint": "mixed-responsibilities",
            "failure_count": 3,
            "failure_event_ids": ["failure-1", "failure-2", "failure-3"],
        },
    ))
    config = _config(with_orchestrator=True)
    config.roles.append(RoleConfig(
        name="flow-discovery",
        backend="mock",
        skills=["zf-gap-task-synth"],
    ))
    config.workflow.stages.append(WorkflowStageConfig(
        id="issue-post-verify-discovery",
        trigger="flow.discovery.requested",
        topology="fanout_reader",
        roles=["flow-discovery"],
    ))
    run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        spawn_repairs=False,
    )
    triage_request = next(event for event in log.read_all() if event.type == TRIAGE_REQUESTED)
    log.append(ZfEvent(
        id="advice-split",
        type=TRIAGE_RECORDED,
        actor="orchestrator",
        task_id="TASK-1",
        payload={
            "request_id": triage_request.payload["request_id"],
            "failure_fingerprint": "mixed-responsibilities",
            "recommended_action": "split_task",
            "guidance": "replace the mixed task with two bounded slices",
            "apply_policy": "proposal_only",
        },
    ))

    projection = build_run_manager_projection(
        state_dir,
        events=log.read_all(),
        config=config,
    )
    action = projection["pending_actions"][0]
    assert action["action"] == SEMANTIC_REPLAN_ACTION
    assert action["semantic_replan_trigger"] == "flow.discovery.requested"
    assert action["supersedes_task_ids"] == ["TASK-1"]
    result = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        spawn_repairs=False,
    )

    assert result.actions_applied == 1
    request = [
        event for event in log.read_all()
        if event.type == "flow.discovery.requested"
    ][-1]
    assert request.payload["task_map_ref"] == str(task_map)
    assert request.payload["supersedes_task_ids"] == ["TASK-1"]
    context_ref = request.payload["recovery_context_ref"]
    assert (state_dir / context_ref["ref"]).exists()
    assert not any(
        event.type == "run.manager.autoresearch.requested"
        for event in log.read_all()
    )


def test_semantic_replan_survives_restart_and_adopts_replacement_task(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ZaoFu Test",
            "-c",
            "user.email=zf@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        cwd=tmp_path,
        check=True,
    )
    base_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        text=True,
    ).strip()
    state_dir, log, writer = _state(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-OLD",
        title="mixed expiry task",
        status="review",
        assigned_to="dev",
        contract=TaskContract(feature_id="ISSUE-RESTART", behavior="expiry works"),
    ))
    task_map = state_dir / "artifacts" / "ISSUE-RESTART" / "task_map.json"
    task_map.parent.mkdir(parents=True)
    task_map.write_text(
        """{
  "schema_version": "task-map.v1",
  "feature_id": "ISSUE-RESTART",
  "tasks": [{
    "task_id": "TASK-OLD",
    "title": "mixed expiry task",
    "owner_role": "dev",
    "wave": 0,
    "allowed_paths": ["src/**", "tests/**"],
    "allowed_paths_reason": "initial issue slice",
    "acceptance": ["expiry works"]
  }]
}\n""",
        encoding="utf-8",
    )
    log.append(ZfEvent(
        id="task-map-restart",
        type="task_map.ready",
        actor="zf-cli",
        payload={
            "pdd_id": "ISSUE-RESTART",
            "feature_id": "ISSUE-RESTART",
            "task_map_ref": str(task_map),
        },
    ))
    cap = ZfEvent(
        id="cap-restart",
        type="task.rework.capped",
        actor="zf-cli",
        task_id="TASK-OLD",
        payload={
            "role": "dev",
            "failure_fingerprint": "mixed-task",
            "failure_count": 3,
            "failure_event_ids": ["fail-1", "fail-2", "fail-3"],
        },
    )
    log.append(cap)
    config = _config(with_orchestrator=True)
    config.workflow.stages.extend([
        WorkflowStageConfig(
            id="issue-post-verify-discovery",
            trigger="flow.discovery.requested",
            topology="fanout_reader",
            roles=["flow-discovery"],
        ),
        WorkflowStageConfig(
            id="issue-gap-impl",
            trigger="task_map.ready",
            topology="fanout_writer_scoped",
            roles=["dev"],
            task_map="${task_map_ref}",
            synthesize_canonical_tasks=True,
            aggregate=FanoutAggregateConfig(
                mode="candidate_integration",
                success_event="candidate.ready",
                failure_event="integration.failed",
            ),
        ),
        WorkflowStageConfig(
            id="issue-gap-verify",
            trigger="candidate.ready",
            topology="fanout_reader",
            roles=["verify"],
            aggregate=FanoutAggregateConfig(
                mode="wait_for_all",
                child_success_event="verify.child.completed",
                child_failure_event="verify.child.failed",
                success_event="verify.passed",
                failure_event="verify.failed",
            ),
        ),
    ])
    config.roles.extend([
        RoleConfig(
            name="flow-discovery",
            backend="mock",
            role_kind="reader",
            skills=["zf-gap-task-synth"],
        ),
        RoleConfig(name="verify", backend="mock", role_kind="reader"),
    ])
    run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        spawn_repairs=False,
    )
    triage_request = next(event for event in log.read_all() if event.type == TRIAGE_REQUESTED)
    log.append(ZfEvent(
        id="advice-restart",
        type=TRIAGE_RECORDED,
        actor="orchestrator",
        task_id="TASK-OLD",
        payload={
            "request_id": triage_request.payload["request_id"],
            "failure_fingerprint": "mixed-task",
            "recommended_action": "split_task",
            "guidance": "replace with one bounded core task",
            "apply_policy": "proposal_only",
        },
    ))
    run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        event_log=log,
        spawn_repairs=False,
    )
    request = [event for event in log.read_all() if event.type == "flow.discovery.requested"][-1]

    # Process restart: reconstruct the log, writer, transport, and orchestrator.
    restarted_log = EventLog(state_dir / "events.jsonl")
    restarted_writer = EventWriter(restarted_log)
    restarted_transport = TmuxTransport(TmuxSession(session_name="restart", dry_run=True))
    restarted_orchestrator = Orchestrator(state_dir, config, restarted_transport)
    restarted_orchestrator.run_once(events=[request])
    restarted_orchestrator.run_once(events=[ZfEvent(
        id="discovery-restart-completed",
        type="flow.discovery.completed",
        actor="flow-discovery",
        correlation_id=request.correlation_id,
        payload={
            **request.payload,
            "goal_kind": "issue",
            "gap_category": "issue_gap",
            "supersedes_task_ids": ["TASK-OLD"],
                "gap_tasks": [{
                    "task_id": "TASK-CORE",
                    "parent_task_id": "TASK-OLD",
                    "owner_role": "dev",
                    "base_commit": base_commit,
                    "claim_paths": ["src/core/**", "tests/test_core.py"],
                    "acceptance": ["expiry core behavior works"],
                    "verify_commands": ["uv run pytest tests/test_core.py"],
                    "source_refs": [
                        "docs/issues/restart.md",
                        f"git:{base_commit}",
                    ],
                }],
        },
    )])

    restarted_events = restarted_log.read_all()
    assert len([event for event in restarted_events if event.type == "flow.discovery.requested"]) == 1
    assert any(event.type == "task_map.amended" for event in restarted_events)
    assert any(event.type == "task.superseded" and event.task_id == "TASK-OLD" for event in restarted_events)
    assert TaskStore(state_dir / "kanban.json").get("TASK-OLD").status == "cancelled"
    assert TaskStore(state_dir / "kanban.json").get("TASK-CORE").status == "in_progress"
    assert any(event.type == "run.manager.action.verify.passed" for event in restarted_events)

    candidate = ZfEvent(
        id="candidate-restart-ready",
        type="candidate.ready",
        actor="zf-cli",
        correlation_id=request.correlation_id,
        payload={
            "pdd_id": "ISSUE-RESTART",
            "feature_id": "ISSUE-RESTART",
            "candidate_ref": "candidate/ISSUE-RESTART",
            "completed_task_ids": ["TASK-CORE"],
        },
    )
    restarted_orchestrator.run_once(events=[candidate])
    verify_dispatch = [
        event for event in restarted_log.read_all()
        if event.type == "fanout.child.dispatched"
        and event.payload.get("stage_id") == "issue-gap-verify"
    ][-1]
    restarted_orchestrator.run_once(events=[ZfEvent(
        id="verify-restart-completed",
        type="verify.child.completed",
        actor="verify",
        correlation_id=request.correlation_id,
        payload={
            "fanout_id": verify_dispatch.payload["fanout_id"],
            "child_id": verify_dispatch.payload["child_id"],
            "run_id": verify_dispatch.payload["run_id"],
            "role_instance": "verify",
            "status": "completed",
            "report": {
                "status": "passed",
                "summary": "replacement task verified after restart",
                "findings": [],
                "recommendation": "approve",
                "evidence_refs": ["artifacts/ISSUE-RESTART/verify.json"],
            },
        },
    )])
    assert any(
        event.type == "verify.passed"
        for event in restarted_log.read_all()
    )

    # A restarted Run Manager must not mint the same mutating request again.
    rerun = run_manager_tick(
        state_dir=state_dir,
        writer=restarted_writer,
        config=config,
        event_log=restarted_log,
        spawn_repairs=False,
    )
    assert rerun.actions_applied == 0
    assert len([event for event in restarted_log.read_all() if event.type == "flow.discovery.requested"]) == 1


def test_duplicate_caps_produce_one_mutating_recovery_action() -> None:
    events = [
        ZfEvent(
            id=f"cap-{index}",
            type="task.rework.capped",
            task_id="TASK-1",
            payload={
                "failure_fingerprint": "same-gap",
                "failure_count": 3,
                "failure_event_ids": ["f1", "f2", "f3"],
            },
        )
        for index in range(2)
    ]

    actions = pending_rework_triage_actions(
        events,
        threshold=3,
        stale_seconds=300,
    )

    assert len(actions) == 1
    assert actions[0]["action"] == "orchestrator-rework-triage"


def test_later_mainline_progress_supersedes_old_triage_action() -> None:
    events = [
        ZfEvent(
            id="cap-before-progress",
            type="task.rework.capped",
            task_id="TASK-1",
            payload={
                "failure_fingerprint": "same-gap",
                "failure_count": 3,
            },
        ),
        ZfEvent(
            id="verify-success",
            type="verify.passed",
            task_id="TASK-1",
            payload={"evidence_ref": "artifacts/TASK-1/verify.json"},
        ),
    ]

    assert pending_rework_triage_actions(
        events,
        threshold=3,
        stale_seconds=300,
    ) == []


def test_new_failure_episode_does_not_reuse_old_triage_advice() -> None:
    first_cap = ZfEvent(
        id="cap-old",
        type="task.rework.capped",
        task_id="TASK-1",
        payload={
            "failure_fingerprint": "same-gap",
            "failure_count": 3,
            "failure_event_ids": ["old-1", "old-2", "old-3"],
        },
    )
    old_action = pending_rework_triage_actions(
        [first_cap], threshold=3, stale_seconds=300,
    )[0]
    events = [
        first_cap,
        ZfEvent(
            type=TRIAGE_REQUESTED,
            task_id="TASK-1",
            payload={"request_id": old_action["request_id"]},
        ),
        ZfEvent(
            type=TRIAGE_RECORDED,
            task_id="TASK-1",
            payload={
                "request_id": old_action["request_id"],
                "recommended_action": "precise_rework",
            },
        ),
        ZfEvent(type="verify.passed", task_id="TASK-1"),
        ZfEvent(
            id="cap-new",
            type="task.rework.capped",
            task_id="TASK-1",
            payload={
                "failure_fingerprint": "same-gap",
                "failure_count": 3,
                "failure_event_ids": ["new-1", "new-2", "new-3"],
            },
        ),
    ]

    actions = pending_rework_triage_actions(
        events,
        threshold=3,
        stale_seconds=300,
    )

    assert len(actions) == 1
    assert actions[0]["action"] == "orchestrator-rework-triage"
    assert actions[0]["request_id"] != old_action["request_id"]


def test_orchestrator_briefing_keeps_triage_advisory_only(tmp_path: Path) -> None:
    state_dir, _, _ = _state(tmp_path)
    event = ZfEvent(
        type=TRIAGE_REQUESTED,
        actor="run-manager",
        task_id="TASK-1",
        payload={
            "request_id": "request-1",
            "failure_fingerprint": "same-gap",
            "failure_count": 3,
            "failure_event_ids": ["f1", "f2", "f3"],
        },
    )

    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=_config(with_orchestrator=True),
        trigger_event=event,
    )

    assert "proposal-only semantic triage" in briefing
    assert "orchestrator.rework.triage.recorded" in briefing
    assert "Do not dispatch, reassign, edit TaskStore" in briefing


def test_candidate_cap_routes_through_orchestrator_advice_before_replan() -> None:
    cap = ZfEvent(
        id="candidate-cap-1",
        type="candidate.rework.capped",
        actor="run-manager",
        correlation_id="sim4-run",
        payload={
            "pdd_id": "SIM4-PRD",
            "trace_id": "sim4-run",
            "failure_scope": "candidate",
            "failure_fingerprint": "candidate-failure-stable",
            "failure_count": 3,
            "failure_event_ids": ["integration-1", "integration-2", "integration-3"],
            "semantic_triage_required": True,
            "candidate_rework_context": {
                "pdd_id": "SIM4-PRD",
                "trace_id": "sim4-run",
                "task_map_ref": "artifacts/plan/task-map.json",
                "source_commit": "base-1",
                "candidate_base_commit": "base-1",
                "source_event_id": "integration-3",
                "source_event_type": "integration.failed",
                "rework_attempt": 3,
                "rework_feedback": ["same candidate quality failure"],
            },
        },
    )
    requested_actions = pending_rework_triage_actions(
        [cap],
        threshold=2,
        stale_seconds=300,
    )
    assert len(requested_actions) == 1
    request_action = requested_actions[0]
    assert request_action["action"] == "orchestrator-rework-triage"
    assert request_action["recovery_scope"] == "candidate"
    assert request_action["pdd_id"] == "SIM4-PRD"

    request = ZfEvent(
        type=TRIAGE_REQUESTED,
        actor="run-manager",
        task_id="SIM4-PRD",
        payload={"request_id": request_action["request_id"]},
    )
    recorded = ZfEvent(
        id="candidate-advice-1",
        type=TRIAGE_RECORDED,
        actor="orchestrator",
        task_id="SIM4-PRD",
        payload={
            "request_id": request_action["request_id"],
            "recommended_action": "replan",
            "guidance": "replace the stale quality command with the declared run contract",
            "apply_policy": "proposal_only",
        },
    )

    advice_actions = pending_rework_triage_actions(
        [cap, request, recorded],
        threshold=2,
        stale_seconds=300,
    )

    assert len(advice_actions) == 1
    action = advice_actions[0]
    assert action["action"] == "candidate-rework-apply"
    assert action["candidate_rework_action"] == "replan"
    assert action["orchestrator_triage_applied"] is True
    assert action["source_event_id"] == "integration-3"
    assert "declared run contract" in action["rework_feedback"][-1]


def test_orchestrator_briefing_names_candidate_recovery_scope(tmp_path: Path) -> None:
    state_dir, _, _ = _state(tmp_path)
    event = ZfEvent(
        type=TRIAGE_REQUESTED,
        actor="run-manager",
        task_id="SIM4-PRD",
        payload={
            "request_id": "candidate-request-1",
            "task_id": "SIM4-PRD",
            "pdd_id": "SIM4-PRD",
            "recovery_scope": "candidate",
            "failure_fingerprint": "candidate-failure-stable",
            "failure_count": 3,
        },
    )

    briefing = build_orchestrator_briefing(
        state_dir=state_dir,
        config=_config(with_orchestrator=True),
        trigger_event=event,
    )

    assert "candidate scope `SIM4-PRD`" in briefing
    assert "recovery_scope" in briefing
    assert "candidate-request-1" in briefing
    assert "proposal-only semantic triage" in briefing


@pytest.mark.parametrize(
    "relative_path",
    [
        "examples/prod/controller/prd-fanout-v3.yaml",
        "examples/prod/controller/prd-fanout-v3-claude.yaml",
        "examples/prod/controller/prd-light-v3.yaml",
        "examples/prod/controller/prd-light-v3-claude.yaml",
        "examples/prod/controller/issue-fanout-v3.yaml",
        "examples/prod/controller/issue-fanout-v3-claude.yaml",
        "examples/prod/controller/refactor-lane-v3.yaml",
        "examples/prod/controller/refactor-lane-v3-claude.yaml",
    ],
)
def test_prod_controller_profiles_triage_after_two_failed_rework_attempts(
    relative_path: str,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(repo_root / relative_path)

    assert config.workflow.strict_triggers.rework_attempts_gte == 2


@pytest.mark.parametrize(
    ("relative_path", "expected_trigger"),
    [
        ("examples/prod/controller/prd-fanout-v3.yaml", "flow.discovery.requested"),
        ("examples/prod/controller/prd-fanout-v3-claude.yaml", "flow.discovery.requested"),
        ("examples/prod/controller/issue-fanout-v3.yaml", "flow.discovery.requested"),
        ("examples/prod/controller/issue-fanout-v3-claude.yaml", "flow.discovery.requested"),
        ("examples/prod/controller/refactor-lane-v3.yaml", "verify.parity_scan.requested"),
        ("examples/prod/controller/refactor-lane-v3-claude.yaml", "verify.parity_scan.requested"),
        ("examples/prod/controller/prd-light-v3.yaml", ""),
        ("examples/prod/controller/prd-light-v3-claude.yaml", ""),
    ],
)
def test_prod_controller_semantic_replan_route_is_declared_or_light_falls_back(
    relative_path: str,
    expected_trigger: str,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(repo_root / relative_path)

    route = resolve_semantic_replan_route(config)
    assert (route.trigger_event if route is not None else "") == expected_trigger
    assert config.runtime.run_manager.resident_agent.enabled is True


@pytest.mark.parametrize(
    "relative_path",
    [
        "zf.yaml",
        "examples/prod/new/prd-fanout-v2.yaml",
        "examples/prod/new/issue-fanout-v2.yaml",
        "examples/prod/new/refactor-lane-v2.yaml",
    ],
)
def test_configs_with_orchestrator_role_close_triage_event_contract(
    relative_path: str,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(repo_root / relative_path)
    role = next(item for item in config.roles if item.name == "orchestrator")

    assert TRIAGE_REQUESTED in role.triggers
    assert TRIAGE_RECORDED in role.publishes


def test_admitted_child_completion_supersedes_old_task_failure():
    from zf.core.events.model import ZfEvent
    from zf.runtime.run_manager_rework_triage import _has_later_task_progress
    failure = ZfEvent(type="dev.blocked", task_id="A")
    completed = ZfEvent(type="fanout.child.completed", payload={
        "task_id": "A", "status": "completed", "admitted_call_result_ref": {"ref": "result", "sha256": "digest"},
    })
    assert _has_later_task_progress([failure, completed], 0, "A")
    assert not _has_later_task_progress([failure, completed], 0, "B")
    completed.payload["status"] = "failed"
    assert not _has_later_task_progress([failure, completed], 0, "A")
    completed.payload["status"] = "completed"
    completed.payload.pop("admitted_call_result_ref")
    assert not _has_later_task_progress([failure, completed], 0, "A")
