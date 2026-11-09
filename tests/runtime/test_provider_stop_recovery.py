from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowTaskAttemptConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import (
    TASK_ATTEMPT_IDENTITY_OPERATION_V2,
    TaskAttemptStore,
)
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_attempt_recovery import (
    pending_task_attempt_recovery_actions,
)
from zf.runtime.task_pipeline_recovery import (
    reconcile_task_pipeline_redrives,
)
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
)
from zf.runtime.workflow_resume import build_workflow_operation_resume_checkpoints


class _StubTransport:
    def __init__(self) -> None:
        self.sends: list[tuple[str, Path, str]] = []

    def send_task(self, role_name: str, briefing_path: Path, prompt: str) -> None:
        self.sends.append((role_name, briefing_path, prompt))
        pass

    def is_alive(self, role_name: str) -> bool:
        return True

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return ""


def _config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        roles=[
            RoleConfig(name="dev", backend="mock", publishes=["dev.build.done"]),
        ],
    )


def _arch_config(state_dir: Path) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        roles=[
            RoleConfig(
                name="arch",
                backend="mock",
                instance_id="arch",
                publishes=[
                    "artifact.manifest.published",
                    "arch.proposal.done",
                ],
            ),
        ],
    )


def test_completed_without_terminal_event_requeues_task(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="recover",
        status="in_progress",
        assigned_to="dev",
    ))
    orch = Orchestrator(state_dir, _config(state_dir), _StubTransport())

    decision = orch._on_codex_hook_stop(ZfEvent(  # type: ignore[attr-defined]
        type="codex.hook.stop",
        actor="dev",
        task_id="T1",
        payload={"provider_stop_reason": "completed_without_terminal_event"},
    ))

    assert decision is not None
    assert decision.action == "dispatch"
    task = store.get("T1")
    assert task is not None
    assert task.status == "backlog"
    assert task.assigned_to == "dev"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(e.type == "provider.stop.recovery" for e in events)


def test_completed_without_terminal_event_after_green_check_requests_terminal(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="recover",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-dev",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
        payload={"role": "dev", "assignee": "dev", "dispatch_id": "disp-dev"},
    ))
    log.append(ZfEvent(
        type="codex.hook.post_tool_use",
        actor="dev",
        task_id="T1",
        payload={
            "tool_input": {
                "command": "python3 -m pytest tests/e2e/test_calc_e2e.py -q",
            },
            "tool_response": "Process exited with code 0\n3 passed in 0.21s\n",
        },
    ))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, _config(state_dir), transport)

    decision = orch._on_codex_hook_stop(ZfEvent(  # type: ignore[attr-defined]
        type="codex.hook.stop",
        actor="dev",
        task_id="T1",
        payload={"provider_stop_reason": "completed_without_terminal_event"},
    ))

    assert decision is not None
    assert decision.action == "recover"
    task = store.get("T1")
    assert task is not None
    assert task.status == "in_progress"
    events = EventLog(state_dir / "events.jsonl").read_all()
    recovered = [e for e in events if e.type == "worker.stuck.recovered"]
    assert recovered[-1].payload["recovery_action"] == (
        "terminal_completion_requested_after_green_verification"
    )
    assert recovered[-1].payload["expected_event"] == "dev.build.done"
    assert transport.sends


def test_completed_without_terminal_event_after_manifest_requests_terminal(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="plan",
        status="in_progress",
        assigned_to="arch",
        active_dispatch_id="disp-plan",
    ))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
        payload={"role": "arch", "assignee": "arch", "dispatch_id": "disp-plan"},
    ))
    log.append(ZfEvent(
        type="artifact.manifest.published",
        actor="arch",
        task_id="T1",
        payload={
            "manifest": {
                "task_id": "T1",
                "role": "arch",
                "artifact_refs": [
                    {
                        "kind": "spec",
                        "path": "docs/specs/demo.md",
                        "sha256": "a" * 64,
                        "summary": "demo spec",
                    }
                ],
            }
        },
    ))
    transport = _StubTransport()
    orch = Orchestrator(state_dir, _arch_config(state_dir), transport)

    decision = orch._on_codex_hook_stop(ZfEvent(  # type: ignore[attr-defined]
        type="codex.hook.stop",
        actor="arch",
        task_id="T1",
        payload={"provider_stop_reason": "completed_without_terminal_event"},
    ))

    assert decision is not None
    assert decision.action == "recover"
    task = store.get("T1")
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "arch"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not any(e.type == "provider.stop.recovery" for e in events)
    recovered = [e for e in events if e.type == "worker.stuck.recovered"]
    assert recovered[-1].payload["recovery_action"] == "terminal_completion_requested"
    assert recovered[-1].payload["expected_event"] == "arch.proposal.done"
    assert transport.sends


def test_auth_error_suspends_task_for_operator(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="recover",
        status="in_progress",
        assigned_to="dev",
    ))
    orch = Orchestrator(state_dir, _config(state_dir), _StubTransport())

    decision = orch._on_agent_api_blocked(ZfEvent(  # type: ignore[attr-defined]
        type="agent.api_blocked",
        actor="dev",
        task_id="T1",
        payload={"provider_stop_reason": "auth_error"},
    ))

    assert decision is not None
    assert decision.action == "block"
    task = store.get("T1")
    assert task is not None
    assert task.status == "blocked"
    assert task.blocked_reason == "provider_stop:auth_error"
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert any(e.type == "provider.stop.recovery" for e in events)
    assert any(e.type == "human.escalate" for e in events)


def test_provider_policy_rejection_blocks_task(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="surface provider policy rejection",
        status="in_progress",
        assigned_to="dev",
    ))
    orch = Orchestrator(state_dir, _config(state_dir), _StubTransport())

    decision = orch._on_agent_api_blocked(ZfEvent(  # type: ignore[attr-defined]
        type="agent.api_blocked",
        actor="dev",
        task_id="T1",
        payload={"provider_stop_reason": "provider_policy_rejected"},
    ))

    assert decision is not None
    assert decision.action == "block"
    task = store.get("T1")
    assert task is not None
    assert task.status == "blocked"
    events = EventLog(state_dir / "events.jsonl").read_all()
    recovered = [event for event in events if event.type == "provider.stop.recovery"]
    assert recovered[-1].payload["action"] == "suspend"


def test_rate_limit_updates_run_goal_usage_limited(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="recover",
        status="in_progress",
        assigned_to="dev",
    ))
    orch = Orchestrator(state_dir, _config(state_dir), _StubTransport())

    decision = orch._on_agent_api_blocked(ZfEvent(  # type: ignore[attr-defined]
        type="agent.api_blocked",
        actor="dev",
        task_id="T1",
        payload={"provider_stop_reason": "rate_limited"},
    ))

    assert decision is not None
    assert decision.action == "skip"
    events = EventLog(state_dir / "events.jsonl").read_all()
    goal_updates = [e for e in events if e.type == "run.goal.updated"]
    assert goal_updates[-1].payload["status"] == "usage_limited"
    assert goal_updates[-1].payload["source"] == "provider_stop_recovery"
    assert any(e.type == "provider.stop.recovery" for e in events)


def test_reader_transport_stop_closes_operation_and_attempt_lease(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="candidate verify",
        status="in_progress",
        assigned_to="dev",
    ))
    orch = Orchestrator(state_dir, _config(state_dir), _StubTransport())
    operations = WorkflowOperationService(
        state_dir=state_dir,
        event_log=orch.event_log,
        event_writer=orch.event_writer,
    )
    ensured = operations.ensure_operation(
        workflow_run_id="RUN-1",
        operation_id="op-reader-1",
        operation_type="fanout_reader_child",
        request={
            "workflow_run_id": "RUN-1",
            "operation_type": "fanout_reader_child",
            "stage_id": "verify",
            "fanout_id": "F-VERIFY",
            "child_id": "verify-lane-0",
            "role_instance": "dev",
            "result_identity": {"run_id": "run-F-VERIFY-lane-0"},
        },
        task_id="T1",
        role_instance="dev",
        active_attempt_id="attempt-reader-1",
        lease_id="lease-reader-1",
    )
    attempts = TaskAttemptStore(state_dir / "task_attempts.json")
    attempt = attempts.ensure_for_dispatch(
        run_id="RUN-1",
        task_id="T1",
        dispatch_id="dispatch-reader-1",
        role="dev",
        instance_id="dev",
        operation_id="op-reader-1",
        briefing_ref="briefings/verify.md",
        created_at="2026-08-03T00:00:00+00:00",
        lease_expires_at="2099-08-03T00:00:00+00:00",
        max_attempts=3,
    ).attempt
    attempts.claim_delivery(
        str(attempt["attempt_id"]),
        updated_at="2026-08-03T00:00:01+00:00",
    )
    attempts.mark_sent(
        str(attempt["attempt_id"]),
        updated_at="2026-08-03T00:00:02+00:00",
    )
    operations.mark_started(
        operation_id="op-reader-1",
        request_hash=ensured.request_hash,
        workflow_run_id="RUN-1",
        task_id="T1",
        dispatch_id="dispatch-reader-1",
        role_instance="dev",
        active_attempt_id=str(attempt["attempt_id"]),
        lease_id=str(attempt["lease_id"]),
    )

    decision = orch._on_agent_api_blocked(ZfEvent(  # type: ignore[attr-defined]
        type="agent.api_blocked",
        actor="dev",
        task_id="T1",
        correlation_id="RUN-1",
        payload={
            "dispatch_id": "dispatch-reader-1",
            "provider_stop_reason": "transport_error",
        },
    ))

    assert decision is not None
    assert decision.action == "recover"
    current_task = store.get("T1")
    assert current_task is not None
    assert current_task.status == "in_progress"
    operation = load_workflow_operation(orch.event_log, "op-reader-1")
    assert operation is not None
    assert operation["status"] == "failed"
    current_attempt = attempts.current(run_id="RUN-1", task_id="T1")
    assert current_attempt is not None
    assert current_attempt["status"] == "failed"
    failed = [
        event for event in orch.event_log.read_all()
        if event.type == "workflow.operation.failed"
    ]
    assert failed[-1].payload["provider_failure_projection"] is True
    assert failed[-1].payload["fanout_id"] == "F-VERIFY"
    assert build_workflow_operation_resume_checkpoints(
        state_dir,
        events=orch.event_log.read_all(),
    ) == []


def test_reader_transport_stop_closes_operation_without_canonical_task(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    orch = Orchestrator(state_dir, _config(state_dir), _StubTransport())
    operations = WorkflowOperationService(
        state_dir=state_dir,
        event_log=orch.event_log,
        event_writer=orch.event_writer,
    )
    ensured = operations.ensure_operation(
        workflow_run_id="RUN-1",
        operation_id="op-global-reader-1",
        operation_type="fanout_reader_child",
        request={
            "workflow_run_id": "RUN-1",
            "operation_type": "fanout_reader_child",
            "stage_id": "global-verify",
            "fanout_id": "F-GLOBAL-VERIFY",
            "child_id": "verify-lane-0",
            "role_instance": "dev",
            "result_identity": {"run_id": "run-F-GLOBAL-VERIFY-lane-0"},
        },
        task_id="WORKFLOW-ANCHOR-1",
        role_instance="dev",
        active_attempt_id="attempt-global-reader-1",
        lease_id="lease-global-reader-1",
    )
    attempts = TaskAttemptStore(state_dir / "task_attempts.json")
    attempt = attempts.ensure_for_dispatch(
        run_id="RUN-1",
        task_id="WORKFLOW-ANCHOR-1",
        dispatch_id="dispatch-global-reader-1",
        role="dev",
        instance_id="dev",
        operation_id="op-global-reader-1",
        briefing_ref="briefings/global-verify.md",
        created_at="2026-08-03T00:00:00+00:00",
        lease_expires_at="2099-08-03T00:00:00+00:00",
        max_attempts=3,
    ).attempt
    attempt_id = str(attempt["attempt_id"])
    lease_id = str(attempt["lease_id"])
    attempts.claim_delivery(
        attempt_id,
        updated_at="2026-08-03T00:00:01+00:00",
    )
    attempts.mark_sent(
        attempt_id,
        updated_at="2026-08-03T00:00:02+00:00",
    )
    operations.mark_started(
        operation_id="op-global-reader-1",
        request_hash=ensured.request_hash,
        workflow_run_id="RUN-1",
        task_id="WORKFLOW-ANCHOR-1",
        dispatch_id="dispatch-global-reader-1",
        role_instance="dev",
        active_attempt_id=attempt_id,
        lease_id=lease_id,
    )

    decision = orch._on_agent_api_blocked(ZfEvent(  # type: ignore[attr-defined]
        type="agent.api_blocked",
        actor="dev",
        task_id="WORKFLOW-ANCHOR-1",
        correlation_id="RUN-1",
        payload={
            "operation_id": "op-global-reader-1",
            "attempt_id": attempt_id,
            "lease_id": lease_id,
            "dispatch_id": "dispatch-global-reader-1",
            "instance_id": "dev",
            "provider_stop_reason": "transport_error",
        },
    ))

    assert decision is not None
    assert decision.action == "recover"
    assert TaskStore(state_dir / "kanban.json").get("WORKFLOW-ANCHOR-1") is None
    operation = load_workflow_operation(orch.event_log, "op-global-reader-1")
    assert operation is not None
    assert operation["status"] == "failed"
    current_attempt = attempts.current(
        run_id="RUN-1",
        task_id="WORKFLOW-ANCHOR-1",
    )
    assert current_attempt is not None
    assert current_attempt["status"] == "failed"
    events = orch.event_log.read_all()
    failed = [event for event in events if event.type == "workflow.operation.failed"]
    assert failed[-1].payload["provider_failure_projection"] is True
    assert failed[-1].payload["fanout_id"] == "F-GLOBAL-VERIFY"
    recovery = [event for event in events if event.type == "provider.stop.recovery"]
    assert recovery[-1].task_id == "WORKFLOW-ANCHOR-1"
    assert recovery[-1].payload["dispatch_id"] == "dispatch-global-reader-1"
    assert build_workflow_operation_resume_checkpoints(
        state_dir,
        events=events,
    ) == []


def test_task_pipeline_provider_stop_waits_for_run_manager_then_redrives(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-VERIFY-1",
        title="verify candidate",
        status="in_progress",
        assigned_to="verify-lane-0",
    ))
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        workflow=WorkflowConfig(
            task_attempt=WorkflowTaskAttemptConfig(
                mode="enforce",
                max_attempts=3,
            ),
        ),
        roles=[RoleConfig(
            name="verify-lane-0",
            instance_id="verify-lane-0",
            backend="mock",
        )],
    )
    orch = Orchestrator(state_dir, config, _StubTransport())
    operations = WorkflowOperationService(
        state_dir=state_dir,
        event_log=orch.event_log,
        event_writer=orch.event_writer,
    )
    ensured = operations.ensure_operation(
        workflow_run_id="RUN-1",
        operation_id="op-task-verify-1",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "verify",
            "operation_generation": 1,
            "task_map_generation": "map-g1",
            "workspace_generation": 1,
            "pipeline_key": "tp-task-verify-1",
        },
        task_id="TASK-VERIFY-1",
        role_instance="verify-lane-0",
    )
    attempts = TaskAttemptStore(state_dir / "task_attempts.json")
    attempt = attempts.ensure_for_dispatch(
        run_id="RUN-1",
        task_id="TASK-VERIFY-1",
        dispatch_id="dispatch-verify-1",
        role="verify-lane-0",
        instance_id="verify-lane-0",
        operation_id="op-task-verify-1",
        briefing_ref="briefings/verify.md",
        created_at="2026-08-03T00:00:00+00:00",
        lease_expires_at="2099-08-03T00:00:00+00:00",
        max_attempts=3,
        identity_version=TASK_ATTEMPT_IDENTITY_OPERATION_V2,
        placement_epoch=1,
    ).attempt
    attempt_id = str(attempt["attempt_id"])
    lease_id = str(attempt["lease_id"])
    attempts.claim_delivery(
        attempt_id,
        updated_at="2026-08-03T00:00:01+00:00",
    )
    attempts.mark_sent(
        attempt_id,
        updated_at="2026-08-03T00:00:02+00:00",
    )
    operations.mark_started(
        operation_id="op-task-verify-1",
        request_hash=ensured.request_hash,
        workflow_run_id="RUN-1",
        task_id="TASK-VERIFY-1",
        dispatch_id="dispatch-verify-1",
        role_instance="verify-lane-0",
        active_attempt_id=attempt_id,
        lease_id=lease_id,
    )

    decision = orch._on_agent_api_blocked(ZfEvent(  # type: ignore[attr-defined]
        type="agent.api_blocked",
        actor="verify-lane-0",
        task_id="TASK-VERIFY-1",
        correlation_id="RUN-1",
        payload={
            "workflow_run_id": "RUN-1",
            "operation_id": "op-task-verify-1",
            "attempt_id": attempt_id,
            "lease_id": lease_id,
            "dispatch_id": "dispatch-verify-1",
            "instance_id": "verify-lane-0",
            "provider_stop_reason": "pending_todos",
        },
    ))

    assert decision is not None
    assert decision.action == "recover"
    task = store.get("TASK-VERIFY-1")
    assert task is not None
    assert task.status == "in_progress"
    operation = load_workflow_operation(orch.event_log, "op-task-verify-1")
    assert operation is not None
    assert operation["status"] == "suspended"
    stopped_attempt = attempts.get(attempt_id)
    assert stopped_attempt is not None
    assert stopped_attempt["status"] == "expired"
    assert stopped_attempt["retryable"] is True
    assert stopped_attempt["recovery_owner"] == "run_manager"
    events = orch.event_log.read_all()
    assert not [
        event for event in events
        if event.type == "workflow.operation.failed"
        and event.payload.get("operation_id") == "op-task-verify-1"
    ]
    attempt_failures = [
        event for event in events
        if event.type == "task.attempt.failed"
        and event.payload.get("attempt_id") == attempt_id
    ]
    assert len(attempt_failures) == 1
    assert attempt_failures[0].payload["recovery_owner"] == "run_manager"
    assert attempt_failures[0].payload["failure_class"] == (
        "task_pipeline_provider_stop"
    )

    duplicate = orch._on_agent_api_blocked(ZfEvent(  # type: ignore[attr-defined]
        type="agent.api_blocked",
        actor="verify-lane-0",
        task_id="TASK-VERIFY-1",
        correlation_id="RUN-1",
        payload={
            "workflow_run_id": "RUN-1",
            "operation_id": "op-task-verify-1",
            "attempt_id": attempt_id,
            "lease_id": lease_id,
            "dispatch_id": "dispatch-verify-1",
            "instance_id": "verify-lane-0",
            "provider_stop_reason": "pending_todos",
        },
    ))
    assert duplicate is not None
    assert duplicate.action == "recover"
    task = store.get("TASK-VERIFY-1")
    assert task is not None
    assert task.status == "in_progress"

    pending = pending_task_attempt_recovery_actions(
        state_dir / "projections",
        canonical_store_path=attempts.path,
    )
    assert len(pending) == 1
    assert pending[0]["action"] == "worker-lifecycle-recover"
    assert pending[0]["policy_decision"]["decision"] == "auto_decide"

    request = orch.event_writer.append(ZfEvent(
        type="worker.respawn.requested",
        actor="run-manager",
        task_id="TASK-VERIFY-1",
        payload={
            "operation_id": "op-task-verify-1",
            "attempt_id": attempt_id,
            "recovery_decision_owner": "run_manager",
            "recovery_effect_owner": "workflow_runtime_coordinator",
        },
    ))
    orch.event_writer.append(ZfEvent(
        type="worker.respawn.completed",
        actor="verify-lane-0",
        task_id="TASK-VERIFY-1",
        causation_id=request.id,
        payload={"reason": "respawned"},
    ))

    redrive = reconcile_task_pipeline_redrives(
        orch,
        generation_contexts={
            "TASK-VERIFY-1": {"workflow_run_id": "RUN-1"},
        },
    )

    assert [item.action for item in redrive] == [
        "task_pipeline_operation_redrive_admitted"
    ]
    operation = load_workflow_operation(orch.event_log, "op-task-verify-1")
    assert operation is not None
    assert operation["status"] == "requested"
    assert operation["redrive_count"] == 1


def test_taskless_orchestrator_timeout_closes_durable_operation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        roles=[RoleConfig(
            name="orchestrator",
            backend="mock",
            instance_id="orchestrator",
        )],
    )
    orch = Orchestrator(state_dir, config, _StubTransport())
    operations = WorkflowOperationService(
        state_dir=state_dir,
        event_log=orch.event_log,
        event_writer=orch.event_writer,
    )
    ensured = operations.ensure_operation(
        workflow_run_id="RUN-1",
        operation_id="op-oa-plan-1",
        operation_type="orchestrator_agent_semantic",
        request={
            "workflow_run_id": "RUN-1",
            "operation_type": "orchestrator_agent_semantic",
            "stage_id": "oa-plan_candidate",
            "role_instance": "orchestrator",
        },
        role_instance="orchestrator",
        active_attempt_id="oa-op-oa-plan-1",
        lease_id="oa-op-oa-plan-1",
    )
    operations.mark_started(
        operation_id="op-oa-plan-1",
        request_hash=ensured.request_hash,
        workflow_run_id="RUN-1",
        dispatch_id="checkpoint-request-1",
        role_instance="orchestrator",
        active_attempt_id="oa-op-oa-plan-1",
        lease_id="oa-op-oa-plan-1",
    )

    decision = orch._on_agent_timeout(ZfEvent(  # type: ignore[attr-defined]
        type="agent.timeout",
        actor="orchestrator",
        correlation_id="RUN-1",
        payload={
            "operation_id": "op-oa-plan-1",
            "instance_id": "orchestrator",
            "provider_stop_reason": "timeout",
        },
    ))

    assert decision is not None
    assert decision.action == "recover"
    operation = load_workflow_operation(orch.event_log, "op-oa-plan-1")
    assert operation is not None
    assert operation["status"] == "failed"
    events = orch.event_log.read_all()
    failed = [event for event in events if event.type == "workflow.operation.failed"]
    assert failed[-1].task_id is None
    recovery = [event for event in events if event.type == "provider.stop.recovery"]
    assert recovery[-1].task_id is None
    assert recovery[-1].payload["operation_id"] == "op-oa-plan-1"
    assert build_workflow_operation_resume_checkpoints(
        state_dir,
        events=events,
    ) == []


def test_cost_budget_exceeded_updates_run_goal_budget_limited(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    orch = Orchestrator(state_dir, _config(state_dir), _StubTransport())

    decision = orch._on_cost_budget_exceeded(ZfEvent(  # type: ignore[attr-defined]
        type="cost.budget.exceeded",
        actor="zf-cli",
        payload={
            "scope": "global",
            "budget_usd": 1.0,
            "current_usd": 1.2,
        },
    ))

    assert decision is not None
    assert decision.action == "skip"
    events = EventLog(state_dir / "events.jsonl").read_all()
    goal_updates = [e for e in events if e.type == "run.goal.updated"]
    assert goal_updates[-1].payload["status"] == "budget_limited"
    assert goal_updates[-1].payload["source"] == "cost_budget_exceeded"
