"""Real blocking dispatch to an inert transport; no provider process is spawned.

Admission is fixture input, not proof of the Plan approval workflow. Only
on-demand physical activation and the transport are substituted. Worktree,
contract snapshot, call preparation, briefing, session binding, attempts,
ledger and projections use production implementations.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.testkit.blocking_fixture import _blocking_runtime_at
from tests.test_plan_artifact_package import _package
from tests.test_scheduler_task_attempt import _RecordingTransport
from tests.test_task_pipeline_generation import _git, _init_master_repo
from tests.test_task_pipeline_reconciler import _policy
from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    RoleLifecycleConfig,
    RuntimeConfig,
    WorkdirConfig,
    WorkflowConfig,
    WorkflowTaskAttemptConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task, TaskContract
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.plan_artifact_package import (
    package_event_payload,
    write_plan_artifact_package,
)
from zf.runtime.workflow_operation import WorkflowOperationService, reduce_workflow_operations
from zf.runtime.task_contract_snapshot import hydrate_task_contract_snapshot
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.result_submit import (
    ResultSubmitError,
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.task_pipeline_result import is_admitted_task_pipeline_stage_result
from zf.runtime.impl_self_check import completion_payload_template
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.task_pipeline_briefing import verification_result_template
from zf.runtime.task_pipeline_runtime import admit_task_pipeline_generation
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_is_current


@pytest.fixture
def blocking_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Orchestrator, _RecordingTransport, ZfEvent]:
    return _blocking_runtime_at(tmp_path / "project", monkeypatch)


def test_blocking_dispatch_prompt_uses_stage_contract(blocking_runtime, monkeypatch):
    runtime, transport, _ = blocking_runtime
    prompts = []
    original_send = transport.send_task

    def observe_send(role_name, briefing_path, prompt, *, context=None):
        prompts.append(prompt)
        return original_send(role_name, briefing_path, prompt, context=context)

    monkeypatch.setattr(transport, "send_task", observe_send)
    assert runtime._dispatch_ready()
    [prompt] = prompts
    assert "Task Pipeline stage briefing" in prompt
    assert "load the kernel-managed task.md" not in prompt
    assert "do not substitute a generic role completion event" in prompt
    assert "Controlled Artifact Inputs" in transport.observed_briefing


@pytest.mark.parametrize("action", ["repeat", "reopen", "old-operation-replay"])
def test_real_blocking_dispatch_persists_attempt_and_does_not_resend(
    blocking_runtime: tuple[Orchestrator, _RecordingTransport, ZfEvent], action: str,
) -> None:
    runtime, transport, admission = blocking_runtime
    state, project, config = runtime.state_dir, runtime.project_root, runtime.config
    task = runtime.task_store.get("TASK-BLOCKING")
    assert task is not None
    old_request = {
        "workflow_run_id": "run-1", "operation_id": "op-old-map-impl-99",
        "operation_type": "task-stage", "task_id": task.id,
        "parent_stage_id": "impl",
        "request": {
            "task_pipeline_stage": "impl", "task_map_generation": "map-old",
            "operation_generation": 99,
        },
    }
    if action == "old-operation-replay":
        WorkflowOperationService(
            state_dir=state, event_log=runtime.event_log, event_writer=runtime.event_writer,
        ).ensure_operation(**old_request)

    first = runtime._dispatch_ready()
    events = runtime.event_log.read_all()
    assert [decision.action for decision in first] == ["task_pipeline_dispatch"], [
        (event.type, event.payload) for event in events
    ]
    assert len(transport.contexts) == 1
    context = transport.contexts[0]
    assert transport.observed_status == "delivering"
    assert transport.observed_started
    assert f"Active task: {task.id}" in transport.observed_briefing
    assert context.briefing_path.is_file()
    attempts = TaskAttemptStore(state / "task_attempts.json").current_rows()
    assert len(attempts) == 1
    assert attempts[0]["status"] == "sent"
    assert attempts[0]["operation_id"] == context.operation_id
    assert attempts[0]["attempt_id"] == context.attempt_id
    operation = reduce_workflow_operations(events)[context.operation_id]
    assert operation["status"] == "running"
    assert operation["task_map_generation"] == "map-current"
    assert operation["operation_generation"] == 1
    dispatched = [e for e in events if e.type == "task.pipeline.stage.dispatched"]
    assert len(dispatched) == 1
    assert dispatched[0].causation_id == admission.id
    assert dispatched[0].payload["attempt_id"] == context.attempt_id
    payload = dispatched[0].payload
    workspace = Path(payload["workdir"])
    assert workspace != project
    assert _git(workspace, "rev-parse", "HEAD") == admission.payload["dispatch_base_commit"]
    snapshot = hydrate_task_contract_snapshot(state, {
        "ref": payload["contract_snapshot_ref"],
        "sha256": payload["contract_snapshot_digest"],
    })
    assert snapshot["task_id"] == task.id
    assert snapshot["allowed_paths"] == ["app/**"]
    assert snapshot["task_map_generation"] == "map-current"
    binding = RoleSessionRegistry(
        state / "role_sessions.yaml", project_root=str(project),
    ).task_stage_binding(
        workflow_run_id="run-1", task_id=task.id, stage="impl",
        rework_affinity_id="map-current:impl",
    )
    assert binding["binding_key"] == context.task_stage_session_binding
    projection = state / "projections" / "task-pipeline.json"
    assert json.loads(projection.read_text())["tasks"]
    assert runtime.task_store.get(task.id).active_dispatch_id == context.dispatch_id

    if action != "repeat":
        runtime = Orchestrator(state, config, transport, project_root=project)
    if action == "old-operation-replay":
        replayed = WorkflowOperationService(
            state_dir=state, event_log=runtime.event_log, event_writer=runtime.event_writer,
        ).ensure_operation(**old_request)
        assert replayed.replay_hit
        assert not replayed.created
    for _ in range(2):
        assert runtime._dispatch_ready() == []
    assert len(transport.contexts) == 1
    assert TaskAttemptStore(state / "task_attempts.json").current_rows() == attempts
    assert reduce_workflow_operations(runtime.event_log.read_all())[context.operation_id] == operation
    assert len([
        e for e in runtime.event_log.read_all()
        if e.type == "task.pipeline.stage.dispatched"
    ]) == 1
    assert not any(
        e.type in {"kernel.housekeeping.failed", "task.pipeline.stage.dispatch_deferred"}
        for e in runtime.event_log.read_all()
    )


def test_blocking_dispatch_rejects_wrong_package_before_transport_or_attempt(
    blocking_runtime: tuple[Orchestrator, _RecordingTransport, ZfEvent],
) -> None:
    runtime, transport, _admission = blocking_runtime
    task = runtime.task_store.get("TASK-BLOCKING")
    assert task is not None
    task.contract.evidence_contract["source_refs"]["plan_artifact_package_id"] = "wrong-package"
    runtime.task_store.update(task.id, contract=task.contract)

    assert runtime._dispatch_ready() == []
    assert transport.contexts == []
    assert TaskAttemptStore(runtime.state_dir / "task_attempts.json").current_rows() == []
    assert runtime.task_store.get(task.id).status == "backlog"
    events = runtime.event_log.read_all()
    deferred = [e for e in events if e.type == "task.pipeline.stage.dispatch_deferred"]
    assert len(deferred) == 1
    assert "plan_artifact_package_id does not match" in deferred[0].payload["reason"]
    assert not any(e.type in {
        "workflow.operation.requested", "task.dispatched", "task.pipeline.stage.dispatched",
    } for e in events)


def test_semantic_verify_rejection_advances_operation_generation_and_caps_rework() -> None:
    """A rejected verify result opens exactly the next semantic generation."""
    policy = _policy()
    policy["max_rework_attempts"] = 1
    task = {
        "id": "TASK-GENERATION",
        "status": "in_progress",
        "priority": 1,
        "created_at": "2026-09-23T00:00:00Z",
        "blocked_by": [],
        "contract": {},
    }

    def operation(stage: str, generation: int, verdict: str) -> dict[str, object]:
        return {
            "task_id": task["id"], "task_pipeline_stage": stage,
            "status": "settled", "operation_generation": generation,
            "operation_id": f"op-{stage}-{generation}",
            "semantic_verdict": verdict, "workflow_run_id": "run-generation",
            "task_map_generation": "map-generation",
        }

    first = TaskPipelineReconciler().reconcile(
        policy=policy, tasks=[task], operations=[
            operation("impl", 1, "passed"), operation("verify", 1, "rejected"),
        ], attempts=[],
    )
    view = first["tasks"][0]
    assert view["stage"] == "impl_rework_ready"
    assert view["next_operation_generation"] == 2
    assert view["rework_count"] == 1
    assert first["dispatchable"]["impl"] == [{
        "task_id": "TASK-GENERATION", "stage": "impl",
        "role_instance": "impl-1", "operation_generation": "2",
    }]
    assert first["dispatchable"]["verify"] == []

    second = TaskPipelineReconciler().reconcile(
        policy=policy, tasks=[task], operations=[
            operation("impl", 1, "passed"), operation("verify", 1, "rejected"),
            operation("impl", 2, "passed"), operation("verify", 2, "rejected"),
        ], attempts=[],
    )
    exhausted = second["tasks"][0]
    assert exhausted["stage"] == "blocked"
    assert exhausted["next_operation_generation"] == 2
    assert exhausted["rework_count"] == 2
    assert "semantic_rework_exhausted" in exhausted["blockers"]
    assert exhausted["semantic_blocker"]["operation_generation"] == 2
    assert second["dispatchable"]["impl"] == []


@pytest.mark.parametrize("reopen", [False, True])
@pytest.mark.parametrize(
    ("rolling_smoke", "smoke_fails"),
    [(False, False), (True, False), (True, True)],
    ids=["missing-smoke", "candidate-smoke", "failed-smoke"],
)
def test_blocking_dispatch_semantic_submit_settles_and_dispatches_verify(
    blocking_runtime: tuple[Orchestrator, _RecordingTransport, ZfEvent], reopen: bool,
    rolling_smoke: bool, smoke_fails: bool,
) -> None:
    """A deterministic worker fixture checks an existing file, not product quality."""
    runtime, transport, _admission = blocking_runtime
    integrates = rolling_smoke and not smoke_fails
    state = runtime.state_dir
    runtime.config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    verify_token = provision_role_submit_credential(state, "verify-1")
    task = runtime.task_store.get("TASK-BLOCKING")
    assert task is not None
    task.contract.acceptance_criteria = ["README.md exists in the pinned commit"]
    task.contract.verification = "git cat-file -e HEAD:README.md"
    if rolling_smoke:
        task.contract.scope = ["README.md"]
        task.contract.validation = {"commands": [{
            "id": "contract-verification", "command": task.contract.verification,
            "tier": "runtime", "rolling_smoke": True,
            "deterministic": True, "reusable": True,
        }]}
        if smoke_fails:
            task.contract.validation["commands"].append({
                "id": "candidate-only-probe",
                "command": "git cat-file -e HEAD:missing-smoke-file",
                "owner": "candidate_verify", "tier": "runtime", "rolling_smoke": True,
                "deterministic": True, "reusable": True,
            })
    runtime.task_store.update(task.id, contract=task.contract)
    token_path = provision_role_submit_credential(state, "impl-1")
    assert len(runtime._dispatch_ready()) == 1
    [context] = transport.contexts
    operation = reduce_workflow_operations(runtime.event_log.read_all())[context.operation_id]
    request = hydrate_sidecar_ref(state, operation["request_ref"]).payload["request"]
    assert request["output_profile_id"] == "implementation"
    dispatched = next(
        event for event in runtime.event_log.read_all()
        if event.type == "task.pipeline.stage.dispatched"
    )
    manifest = hydrate_sidecar_ref(state, dispatched.payload["attempt_source_manifest"]).payload
    policy = hydrate_sidecar_ref(state, request["input_consumption_policy_ref"]).payload
    assert policy["required_reads"]
    for required in policy["required_reads"]:
        read_attempt_artifact(
            state, manifest=manifest, source_id=required["source_id"],
            artifact_id=required["artifact_id"], json_path=required["json_path"],
            actor="impl-1", role="impl-1", provider="mock",
        )
    contract = hydrate_task_contract_snapshot(state, {
        "ref": dispatched.payload["contract_snapshot_ref"],
        "sha256": dispatched.payload["contract_snapshot_digest"],
    })
    task_item = {
        "attempt_id": context.attempt_id,
        "contract_snapshot_ref": dispatched.payload["contract_snapshot_ref"],
        "contract_snapshot_digest": dispatched.payload["contract_snapshot_digest"],
    }
    template = completion_payload_template(
        contract_snapshot=contract, task_item=task_item,
        task_id="TASK-BLOCKING", run_id="run-1", child_id="impl-1",
    )
    self_check = template["impl_self_check"]
    workspace = Path(dispatched.payload["workdir"])
    if rolling_smoke:
        (workspace / "README.md").write_text("K461 candidate patch\n", encoding="utf-8")
        _git(workspace, "add", "README.md")
        _git(workspace, "commit", "-q", "-m", "test: bounded candidate patch")
    target_commit = _git(workspace, "rev-parse", "HEAD")
    checked = subprocess.run(
        ["git", "cat-file", "-e", "HEAD:README.md"], cwd=workspace,
        capture_output=True, text=True, check=False,
    )
    assert checked.returncode == 0
    evidence = write_sidecar_json(
        state, "artifacts/testkit/k459-readme-check.json",
        {"command": checked.args, "cwd": str(workspace), "target_commit": target_commit,
         "exit_code": checked.returncode, "stdout": checked.stdout, "stderr": checked.stderr},
        kind="testkit_probe", schema_version="testkit-probe.v1", created_by="test-worker",
    )
    evidence_refs = [evidence["ref"]]
    self_check.update(source_commit=target_commit, target_commit=target_commit,
                      evidence_refs=evidence_refs)
    for receipt in self_check["command_receipts"]:
        receipt.update(target_commit=target_commit, evidence_refs=evidence_refs,
                       exit_code=checked.returncode)
    for result in self_check["acceptance_results"]:
        result["evidence_refs"] = evidence_refs
    semantic = {
        "verdict": "passed", "target_commit": target_commit,
        "changed_files": ["README.md"] if rolling_smoke else [],
        "evidence_refs": evidence_refs, "self_check": self_check,
        "known_gaps": [], "summary": "Fixture README exists at the pinned commit",
    }
    scratch = state / request["result_scratch_ref"]
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_text(json.dumps(semantic), encoding="utf-8")
    if reopen:
        runtime = Orchestrator(state, runtime.config, transport, project_root=runtime.project_root)
    service = SemanticResultSubmitService(
        state_dir=state, event_log=runtime.event_log, event_writer=runtime.event_writer,
    )
    before_ids = [event.id for event in runtime.event_log.read_all()]
    with pytest.raises(ResultSubmitError) as unauthorized:
        service.submit(
            operation_id=context.operation_id, use_operation_scratch=True,
            role_instance="impl-1", credential="invalid-fixture-credential",
        )
    assert unauthorized.value.code == "capability_invalid"
    assert [event.id for event in runtime.event_log.read_all()] == before_ids
    with pytest.raises(ResultSubmitError) as incomplete:
        service.submit(
            operation_id=context.operation_id, semantic_result={**semantic, "self_check": {}},
            role_instance="impl-1", credential=token_path.read_text().strip(),
        )
    assert incomplete.value.code == "result_not_admitted"
    assert any(issue["code"] == "impl_self_check_invalid" for issue in incomplete.value.details["issues"])
    assert reduce_workflow_operations(runtime.event_log.read_all())[context.operation_id]["status"] == "running"
    assert not any(event.type in {"dev.build.done", "workflow.operation.settled"}
                   for event in runtime.event_log.read_all())
    submitted = service.submit(
        operation_id=context.operation_id, use_operation_scratch=True,
        role_instance="impl-1", credential=token_path.read_text().strip(),
    )
    events = runtime.event_log.read_all()
    canonical = next(event for event in events if event.id == submitted.canonical_event_id)
    assert canonical.type == "dev.build.done"
    assert is_admitted_task_pipeline_stage_result(runtime, canonical)
    [admitted] = [event for event in events if event.type == "workflow.call.result.admitted"]
    [settled] = [event for event in events if event.type == "workflow.operation.settled"]
    assert admitted.id == submitted.admitted_event_id
    assert settled.causation_id == admitted.id
    assert admitted.payload["source_event_id"] == canonical.id
    assert canonical.payload["attempt_id"] == context.attempt_id
    assert canonical.payload["control_result_ref"] == submitted.control_result_ref
    control = hydrate_sidecar_ref(state, submitted.control_result_ref).payload
    assert control["self_check"] == self_check
    assert control["target_commit"] == target_commit
    assert hydrate_sidecar_ref(state, evidence).payload["exit_code"] == 0
    assert reduce_workflow_operations(events)[context.operation_id]["status"] == "settled"
    assert TaskAttemptStore(state / "task_attempts.json").current_rows()[0]["status"] == "sent"

    runtime._react_to_events()
    assert TaskAttemptStore(state / "task_attempts.json").current_rows()[0]["status"] == "succeeded"
    verify_decisions = runtime._dispatch_ready()
    assert [decision.action for decision in verify_decisions] == [
        "task_pipeline_dispatch"
    ]
    assert verify_decisions[0].role == "verify-1"
    assert len(transport.contexts) == 2
    verify_context = transport.contexts[1]
    assert verify_context.task_pipeline_stage == "verify"
    assert verify_context.operation_id != context.operation_id
    attempts = {row["attempt_id"]: row for row in TaskAttemptStore(state / "task_attempts.json").current_rows()}
    assert attempts[context.attempt_id]["status"] == "succeeded"
    assert attempts[verify_context.attempt_id]["status"] == "sent"
    events = runtime.event_log.read_all()
    [task_ref] = [event for event in events if event.type == "task.ref.updated"]
    assert task_ref.causation_id == canonical.id
    assert task_ref.payload["source_commit"] == target_commit
    assert _git(workspace, "rev-parse", task_ref.payload["task_ref"]) == target_commit
    [verify_dispatch] = [
        event for event in events if event.type == "task.pipeline.stage.dispatched"
        and event.payload["task_pipeline_stage"] == "verify"
    ]
    assert verify_dispatch.payload["target_commit"] == target_commit
    assert verify_dispatch.payload["contract_snapshot_digest"] == dispatched.payload["contract_snapshot_digest"]
    assert verify_dispatch.payload["impl_self_check_ref"]
    assert reduce_workflow_operations(events)[verify_context.operation_id]["status"] == "running"
    waiting = [event for event in events if event.type == "task.pipeline.stage.waiting"]
    assert not waiting, [(event.type, event.payload) for event in events]
    assert not any(event.type == "task.pipeline.verify.completed" for event in events)
    verify_operation = reduce_workflow_operations(events)[verify_context.operation_id]
    verify_request = hydrate_sidecar_ref(
        state, verify_operation["request_ref"],
    ).payload["request"]
    verify_dispatch_payload = verify_dispatch.payload
    verify_manifest = hydrate_sidecar_ref(
        state, verify_dispatch_payload["attempt_source_manifest"],
    ).payload
    verify_policy = hydrate_sidecar_ref(
        state, verify_request["input_consumption_policy_ref"],
    ).payload
    for required in verify_policy["required_reads"]:
        read_attempt_artifact(
            state, manifest=verify_manifest, source_id=required["source_id"],
            artifact_id=required["artifact_id"], json_path=required["json_path"],
            actor="verify-1", role="verify-1", provider="mock",
        )
    verify_contract = hydrate_task_contract_snapshot(state, {
        "ref": verify_dispatch_payload["contract_snapshot_ref"],
        "sha256": verify_dispatch_payload["contract_snapshot_digest"],
    })
    acceptance_id = str(
        verify_contract["acceptance_criteria"][0].get("acceptance_id")
        or "AC-1"
    )
    verify_workspace = Path(verify_dispatch_payload["workdir"])
    verify_target_commit = _git(verify_workspace, "rev-parse", "HEAD")
    assert verify_target_commit == target_commit
    verify_checked = subprocess.run(
        ["git", "cat-file", "-e", "HEAD:README.md"], cwd=verify_workspace,
        capture_output=True, text=True, check=False,
    )
    assert verify_checked.returncode == 0
    verify_evidence = write_sidecar_json(
        state, "artifacts/testkit/k460-verify-readme-check.json",
        {"command": verify_checked.args, "cwd": str(verify_workspace),
         "target_commit": verify_target_commit, "exit_code": verify_checked.returncode,
         "stdout": verify_checked.stdout, "stderr": verify_checked.stderr},
        kind="testkit_probe", schema_version="testkit-probe.v1", created_by="verify-worker",
    )
    verify_semantic = {
        "schema_version": "verification-result.v1",
        "execution_status": "completed", "verdict": "passed", "failure_class": "none",
        "verification_owner": verify_contract["verification_owner"],
        "verification_tier": verify_contract["verification_tier"],
        "summary": "Pinned README probe passed", "evidence_refs": [verify_evidence["ref"]],
        "probe_receipts": [{
            "probe_id": "readme-check", "status": "passed",
            "command_id": "contract-verification",
            "command": "git cat-file -e HEAD:README.md",
            "command_digest": verify_contract["verification_commands"][0]["command_digest"],
            "target_commit": verify_target_commit, "exit_code": verify_checked.returncode,
            "evidence_refs": [verify_evidence["ref"]],
        }],
        "requirement_results": [{
            "acceptance_id": acceptance_id, "status": "passed",
            "verification_owner": str(verify_contract.get("verification_owner") or "task_verify"),
            "verification_tier": str(verify_contract.get("verification_tier") or "runtime"),
            "evidence_refs": [verify_evidence["ref"]], "findings": [],
            "reproduction_commands": ["git cat-file -e HEAD:README.md"],
        }],
    }
    verify_service = SemanticResultSubmitService(
        state_dir=state, event_log=runtime.event_log, event_writer=runtime.event_writer,
    )
    with pytest.raises(ResultSubmitError) as missing_receipt:
        verify_service.submit(
            operation_id=verify_context.operation_id,
            semantic_result={
                **verify_semantic,
                "probe_receipts": [{
                    **verify_semantic["probe_receipts"][0],
                    "command": "git status --short",
                }],
            },
            role_instance="verify-1", credential=verify_token.read_text().strip(),
        )
    assert missing_receipt.value.code == "result_not_admitted"
    assert any(
        issue["code"] == "verification_probe_command_mismatch"
        for issue in missing_receipt.value.details["issues"]
    )
    assert reduce_workflow_operations(runtime.event_log.read_all())[verify_context.operation_id]["status"] == "running"
    verify_submitted = verify_service.submit(
        operation_id=verify_context.operation_id, semantic_result=verify_semantic,
        role_instance="verify-1", credential=verify_token.read_text().strip(),
    )
    verify_events = runtime.event_log.read_all()
    verify_canonical = next(
        event for event in verify_events if event.id == verify_submitted.canonical_event_id
    )
    assert verify_canonical.type == "task.pipeline.verify.completed"
    assert is_admitted_task_pipeline_stage_result(runtime, verify_canonical)
    assert reduce_workflow_operations(verify_events)[verify_context.operation_id]["status"] == "settled"
    verify_admission = next(
        event for event in verify_events
        if event.id == verify_submitted.admitted_event_id
    )
    assert verify_admission.type == "workflow.call.result.admitted"
    assert verify_admission.payload["source_event_id"] == verify_canonical.id
    verify_settled = next(
        event for event in verify_events if event.type == "workflow.operation.settled"
        and event.payload["operation_id"] == verify_context.operation_id
    )
    assert verify_settled.causation_id == verify_admission.id
    assert verify_canonical.payload["attempt_id"] == verify_context.attempt_id
    assert verify_canonical.payload["control_result_ref"] == verify_submitted.control_result_ref
    verify_control = hydrate_sidecar_ref(state, verify_submitted.control_result_ref).payload
    assert verify_control["probe_receipts"] == verify_semantic["probe_receipts"]
    assert hydrate_sidecar_ref(state, verify_evidence).payload["exit_code"] == verify_checked.returncode
    runtime._react_to_events()
    settled_attempts = {
        row["attempt_id"]: row
        for row in TaskAttemptStore(state / "task_attempts.json").current_rows()
    }
    assert settled_attempts[verify_context.attempt_id]["status"] == "succeeded"
    assert not any(
        event.type == "task.pipeline.verify.failed" for event in runtime.event_log.read_all()
    )
    with pytest.raises(ResultSubmitError) as verify_duplicate:
        verify_service.submit(
            operation_id=verify_context.operation_id, semantic_result=verify_semantic,
            role_instance="verify-1", credential=verify_token.read_text().strip(),
        )
    assert verify_duplicate.value.code == "duplicate_submit"
    with pytest.raises(ResultSubmitError) as duplicate:
        service.submit(
            operation_id=context.operation_id, use_operation_scratch=True,
            role_instance="impl-1", credential=token_path.read_text().strip(),
        )
    assert duplicate.value.code == "duplicate_submit"
    # Replay canonical result through the reactor after a clean object reconstruction.
    runtime = Orchestrator(state, runtime.config, transport, project_root=runtime.project_root)
    runtime._react_to_events(pushed=[canonical, verify_canonical])
    integration = runtime._dispatch_ready()
    actions = [decision.action for decision in integration]
    if integrates:
        assert actions[0] == "task_pipeline_integrated"
        assert set(actions[1:]) == {
            "task_pipeline_task_done", "task_pipeline_sessions_archived",
            "task_pipeline_candidate_frozen",
        }
    else:
        assert actions == ["task_pipeline_integration_blocked"]
        assert "needs_review" in integration[0].reason
    assert runtime._dispatch_ready() == []
    assert len(transport.contexts) == 2
    final = runtime.event_log.read_all()
    if integrates:
        [integrated] = [event for event in final if event.type == "integration.queue.integrated"]
        receipt = hydrate_sidecar_ref(state, integrated.payload["receipt_ref"]).payload
        assert receipt["status"] == "integrated"
        assert receipt["task_commit"] == target_commit
        assert receipt["applied_commits"] == [target_commit]
        candidate_head = receipt["new_candidate_head"]
        assert _git(runtime.project_root, "rev-parse", receipt["candidate_branch"]) == candidate_head
        assert _git(runtime.project_root, "show", f"{candidate_head}:README.md") == "K461 candidate patch"
        [smoke_ref] = receipt["rolling_smoke_receipt_refs"]
        smoke = hydrate_sidecar_ref(state, smoke_ref).payload
        assert smoke["exit_code"] == 0
        assert smoke["target_commit"] == candidate_head
        assert smoke["command_digest"] == verify_contract["verification_commands"][0]["command_digest"]
        assert Path(smoke["execution_root"]) != workspace
        assert _git(Path(smoke["execution_root"]), "status", "--porcelain") == ""
        assert not any(event.type == "integration.queue.needs_review" for event in final)
        integration_operation = reduce_workflow_operations(final)[receipt["integration_operation_id"]]
        assert integration_operation["status"] == "settled"
        [task_done] = [event for event in final if event.type == "task.done"]
        assert task_done.causation_id == integrated.id
        assert task_done.payload["integration_receipt_ref"] == integrated.payload["receipt_ref"]
        completed_task = runtime.task_store.get(task.id)
        assert completed_task.status == "done"
        assert not completed_task.active_dispatch_id
        [archived] = [event for event in final if event.type == "task.pipeline.sessions.archived"]
        assert archived.payload["active_binding_count"] == 0
        registry = RoleSessionRegistry(state / "role_sessions.yaml", project_root=str(runtime.project_root))
        for stage in ("impl", "verify"):
            assert registry.task_stage_binding(
                workflow_run_id="run-1", task_id=task.id, stage=stage,
                rework_affinity_id=f"map-current:{stage}",
            )["status"] == "archived"
        [ready] = [event for event in final if event.type == "candidate.ready"]
        assert ready.causation_id == integrated.id
        frozen = hydrate_sidecar_ref(state, ready.payload["freeze_receipt_ref"]).payload
        assert frozen["status"] == "frozen"
        assert frozen["task_integration_receipt_refs"] == [integrated.payload["receipt_ref"]]
        assert frozen["candidate_head"] == ready.payload["commit"] == candidate_head
        assert frozen["completed_task_ids"] == [task.id]
        assert _git(runtime.project_root, "diff", "--name-only", frozen["diff_ref"]) == "README.md"
        runtime = Orchestrator(state, runtime.config, transport, project_root=runtime.project_root)
        for _ in range(2):
            assert runtime._dispatch_ready() == []
        assert _git(runtime.project_root, "rev-parse", receipt["candidate_branch"]) == candidate_head
        after_reopen = runtime.event_log.read_all()
        for event_type in (
            "task.done", "candidate.ready", "candidate.updated", "integration.queue.integrated",
            "task.pipeline.sessions.archived",
        ):
            assert sum(event.type == event_type for event in after_reopen) == 1
        assert len(transport.contexts) == 2
    else:
        [blocked] = [event for event in final if event.type == "integration.queue.needs_review"]
        detail = "rolling smoke failed: candidate-only-probe" if smoke_fails else "rolling_smoke_command_missing"
        assert blocked.payload["reason"] == f"CandidateIncrementalError: {detail}"
        assert runtime.task_store.get(task.id).status != "done"
        assert not any(event.type in {
            "integration.queue.integrated", "candidate.updated", "candidate.ready", "task.done",
        } for event in final)
        if smoke_fails:
            receipts = [
                json.loads(path.read_text())
                for path in (state / "artifacts/rolling-smoke-receipts").glob("*.json")
            ]
            [failed_probe] = [row for row in receipts if row["command_id"] == "candidate-only-probe"]
            assert failed_probe["exit_code"] != 0
            assert failed_probe["stderr_tail"]
            assert failed_probe["target_commit"] == _git(Path(failed_probe["execution_root"]), "rev-parse", "HEAD")
    for event_type, expected in {
        "workflow.operation.settled": 3 if integrates else 2,
        "workflow.call.result.admitted": 2,
        "dev.build.done": 1, "task.pipeline.verify.completed": 1,
        "task.pipeline.stage.dispatched": 2,
    }.items():
        assert sum(event.type == event_type for event in final) == expected, {
            "event_type": event_type,
            "counts": {item: sum(event.type == item for event in final) for item in {
                "workflow.operation.settled", "workflow.call.result.admitted", "dev.build.done",
                "task.pipeline.verify.completed", "task.pipeline.stage.dispatched", "task.ref.updated",
            }},
        }
    forbidden = {"kernel.housekeeping.failed", "task.pipeline.stage.dispatch_deferred"}
    if not integrates:
        forbidden.add("task.done")
    assert not any(event.type in forbidden for event in final)
    if integrates:
        assert sum(event.type == "task.done" for event in final) == 1
        assert sum(event.type == "candidate.ready" for event in final) == 1


def _submit_generation_stage(runtime, context, token: Path, *, repair: bool = False, writes=None):
    """Bounded worker: its initial semantic claim is intentionally wrong, not its receipts."""
    state = runtime.state_dir
    dispatched = next(
        event for event in runtime.event_log.read_all()
        if event.type == "task.pipeline.stage.dispatched"
        and event.payload["operation_id"] == context.operation_id
        and event.payload["attempt_id"] == context.attempt_id
    )
    payload = dispatched.payload
    manifest = hydrate_sidecar_ref(state, payload["attempt_source_manifest"]).payload
    policy = hydrate_sidecar_ref(state, payload["input_consumption_policy_ref"]).payload
    for required in policy["required_reads"]:
        read_attempt_artifact(
            state, manifest=manifest, source_id=required["source_id"],
            artifact_id=required["artifact_id"], json_path=required["json_path"],
            actor=context.instance_id, role=context.instance_id, provider="mock",
        )
    contract = hydrate_task_contract_snapshot(state, {
        "ref": payload["contract_snapshot_ref"], "sha256": payload["contract_snapshot_digest"],
    })
    workspace = Path(payload["workdir"])
    writes = writes if writes is not None else ({"README.md": "K462 repaired\n"} if repair else {})
    if writes:
        for name, content in writes.items():
            (workspace / name).write_text(content, encoding="utf-8")
        _git(workspace, "add", "--", *writes)
        _git(workspace, "commit", "-q", "-m", "test: repair bounded fixture")
    target = _git(workspace, "rev-parse", "HEAD")
    is_impl = payload["task_pipeline_stage"] == "impl"
    if is_impl:
        self_check = completion_payload_template(
            contract_snapshot=contract, task_item=payload,
            task_id=payload["task_id"], run_id="run-1", child_id="impl-1",
        )["impl_self_check"]
        body = {"verdict": "passed", "summary": "Worker semantic claim to be independently checked",
                "target_commit": target, "changed_files": list(writes),
                "self_check": self_check, "known_gaps": []}
        receipts = self_check["command_receipts"]
    else:
        assert payload["target_commit"] == target
        body = verification_result_template(contract)
        receipts = body["probe_receipts"]
    refs = []
    for receipt in receipts:
        command = next(item for item in contract["verification_commands"]
                       if item["command_id"] == receipt["command_id"])
        checked = subprocess.run(command["command"], shell=True, cwd=workspace,
                                 capture_output=True, text=True, check=False)
        evidence = write_sidecar_json(
            state, f"artifacts/testkit/k462/{context.attempt_id}-{receipt['command_id']}.json",
            {"command": checked.args, "cwd": str(workspace), "target_commit": target,
             "exit_code": checked.returncode, "stdout": checked.stdout, "stderr": checked.stderr},
            kind="testkit_probe", schema_version="testkit-probe.v1", created_by="test-worker",
        )
        refs.append(evidence["ref"])
        receipt.update(target_commit=target, exit_code=checked.returncode,
                       status="passed" if checked.returncode == 0 else "failed",
                       evidence_refs=[evidence["ref"]])
    passed = all(receipt["exit_code"] == 0 for receipt in receipts)
    body["evidence_refs"] = refs
    if is_impl:
        assert passed
        self_check.update(source_commit=target, target_commit=target, evidence_refs=refs)
        for criterion in self_check["acceptance_results"]:
            criterion["evidence_refs"] = refs
    else:
        finding = {"finding_id": "readme-content", "reason": "README does not contain K462 repaired"}
        body.update(verdict="passed" if passed else "rejected",
                    failure_class="none" if passed else "product_rejection",
                    summary="Independent content check", findings=[] if passed else [finding])
        for criterion in body["requirement_results"]:
            criterion.update(status="passed" if passed else "failed", evidence_refs=refs,
                             findings=[] if passed else [finding])
        if not passed:
            body["rework_items"] = [{
                "rework_item_id": "fix-readme", "acceptance_id": body["requirement_results"][0]["acceptance_id"],
                "status": "incorrect", "expected": "K462 repaired", "observed": "hello",
                "required_delta": "write K462 repaired", "done_when": "content probe passes",
                "next_gate": "task_verify", "owner": "impl", "allowed_scope": ["README.md"],
            }]
    result = SemanticResultSubmitService(
        state_dir=state, event_log=runtime.event_log, event_writer=runtime.event_writer,
    ).submit(operation_id=context.operation_id, semantic_result=body,
             role_instance=context.instance_id, credential=token.read_text().strip())
    event = next(e for e in runtime.event_log.read_all() if e.id == result.canonical_event_id)
    assert is_admitted_task_pipeline_stage_result(runtime, event)
    return event, body


@pytest.mark.parametrize("reopen", [False, True])
@pytest.mark.parametrize("repair", [False, True], ids=["exhaust", "repair"])
def test_real_semantic_rework_two_generations(blocking_runtime, reopen: bool, repair: bool) -> None:
    runtime, transport, _admission = blocking_runtime
    state = runtime.state_dir
    runtime.config.workflow.flow_metadata["task_pipeline"]["max_rework_attempts"] = 1
    runtime.config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    tokens = {role: provision_role_submit_credential(state, role) for role in ("impl-1", "verify-1")}
    task = runtime.task_store.get("TASK-BLOCKING")
    task.contract.scope = ["README.md"]
    task.contract.acceptance_criteria = ["README contains K462 repaired"]
    task.contract.validation = {"commands": [
        {"id": "show-readme", "command": "git show HEAD:README.md", "owner": "impl_self_check"},
        {"id": "content", "command": "git show HEAD:README.md | grep -qx 'K462 repaired'",
         "owner": "task_verify", "rolling_smoke": True, "tier": "runtime"},
    ]}
    runtime.task_store.update(task.id, contract=task.contract)
    previous = []
    for generation in (1, 2):
        assert [d.action for d in runtime._dispatch_ready()] == ["task_pipeline_dispatch"]
        impl = transport.contexts[-1]
        assert impl.task_pipeline_stage == "impl"
        assert impl.operation_id not in [context.operation_id for context in previous]
        operation = reduce_workflow_operations(runtime.event_log.read_all())[impl.operation_id]
        assert operation["operation_generation"] == generation
        assert operation["task_map_generation"] == "map-current"
        if generation == 2:
            assert "fix-readme" in impl.briefing_path.read_text()
            assert runtime._dispatch_ready() == []
        impl_event, _ = _submit_generation_stage(
            runtime, impl, tokens["impl-1"], repair=repair and generation == 2,
        )
        runtime._react_to_events()
        assert [d.action for d in runtime._dispatch_ready()] == ["task_pipeline_dispatch"]
        verify = transport.contexts[-1]
        assert verify.task_pipeline_stage == "verify"
        assert reduce_workflow_operations(runtime.event_log.read_all())[verify.operation_id]["operation_generation"] == generation
        verified, body = _submit_generation_stage(runtime, verify, tokens["verify-1"])
        expected = "passed" if repair and generation == 2 else "rejected"
        assert body["verdict"] == expected
        operation = reduce_workflow_operations(runtime.event_log.read_all())[verify.operation_id]
        assert operation["status"] == "settled"
        assert operation["semantic_verdict"] == expected
        runtime._react_to_events()
        previous.extend([impl, verify])
        if reopen:
            runtime = Orchestrator(state, runtime.config, transport, project_root=runtime.project_root)
        runtime._react_to_events(pushed=[impl_event, verified])
        assert len(transport.contexts) == generation * 2
    decisions = runtime._dispatch_ready()
    events = runtime.event_log.read_all()
    if repair:
        assert "task_pipeline_candidate_frozen" in [d.action for d in decisions]
        assert runtime.task_store.get(task.id).status == "done"
        [integrated] = [e for e in events if e.type == "integration.queue.integrated"]
        assert integrated.payload["operation_generation"] == 2
        [ready] = [e for e in events if e.type == "candidate.ready"]
        assert _git(runtime.project_root, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"
    else:
        assert decisions == []
        [exhausted] = [e for e in events if e.type == "task.pipeline.semantic_rework.exhausted"]
        assert exhausted.payload["operation_id"] == previous[-1].operation_id
        assert exhausted.payload["operation_generation"] == 2
        assert exhausted.payload["owner_route"] == "run_manager"
        assert not any(e.type in {"task.done", "candidate.ready"} for e in events)
    for _ in range(2):
        runtime = Orchestrator(state, runtime.config, transport, project_root=runtime.project_root)
        assert runtime._dispatch_ready() == []
    assert len(transport.contexts) == 4
    final = runtime.event_log.read_all()
    assert sum(e.type == "task.pipeline.semantic_rework.exhausted" for e in final) == (0 if repair else 1)
    assert sum(e.type == "task.done" for e in final) == int(repair)
    assert sum(e.type == "candidate.ready" for e in final) == int(repair)
    assert not any(e.type in {"kernel.housekeeping.failed", "task.pipeline.stage.dispatch_deferred"} for e in final)


@pytest.mark.parametrize("old_stages", [("impl",), ("impl", "verify")], ids=["impl-settled", "verify-settled"])
def test_new_task_map_rebinds_task_and_isolates_old_results(blocking_runtime, old_stages) -> None:
    """Plan/TaskMap inputs are Given; admission and the downstream pipeline are real."""
    runtime, transport, old_admission = blocking_runtime
    state = runtime.state_dir
    runtime.config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    tokens = {role: provision_role_submit_credential(state, role) for role in ("impl-1", "verify-1")}
    task = runtime.task_store.get("TASK-BLOCKING")
    task.contract.scope = ["README.md"]
    task.contract.acceptance_criteria = ["README exists at the pinned commit"]
    task.contract.verification = "git cat-file -e HEAD:README.md"
    task.contract.validation = {"commands": [{
        "id": "readme", "command": task.contract.verification,
        "rolling_smoke": True, "tier": "runtime", "deterministic": True, "reusable": True,
    }]}
    runtime.task_store.update(task.id, contract=task.contract)
    old_results = []
    for stage in old_stages:
        assert [d.action for d in runtime._dispatch_ready()] == ["task_pipeline_dispatch"]
        context = transport.contexts[-1]
        assert context.task_pipeline_stage == stage
        event, _ = _submit_generation_stage(runtime, context, tokens[context.instance_id])
        old_results.append(event)
        runtime._react_to_events()
    old_contexts = list(transport.contexts)
    old_operations = reduce_workflow_operations(runtime.event_log.read_all())
    assert all(row["status"] == "settled" for row in old_operations.values())
    package = _package(state, revision="r2", generation="map-new")
    descriptor = write_plan_artifact_package(state, package)
    identity = {"plan_artifact_package_id": descriptor["package_id"],
                "plan_artifact_package_ref": descriptor["ref"],
                "plan_artifact_package_digest": descriptor["sha256"]}
    runtime.event_writer.append(ZfEvent(
        type="plan.artifact_package.admitted", correlation_id="run-1",
        payload=package_event_payload(package, descriptor, status="admitted"),
    ))
    task = runtime.task_store.get(task.id)
    task.contract.evidence_contract.update(task_map_generation="map-new", source_refs=identity)
    runtime.task_store.update(task.id, contract=task.contract)
    trigger = runtime.event_writer.append(ZfEvent(
        type="task_map.ready", correlation_id="run-1", payload={"task_map_generation": "map-new"},
    ))
    admitted_map = runtime.event_writer.append(ZfEvent(
        type="task_map.admitted", correlation_id="run-1", causation_id=trigger.id,
        payload={"task_map_generation": "map-new", "task_ids": [task.id]},
    ))
    inputs = dict(trigger_event=trigger, task_map_admitted_event=admitted_map, stage_id="impl",
                  trace_id="run-1", task_items=[{"task_id": task.id}], loaded=SimpleNamespace(
                      workflow_run_id="run-1", task_map_generation="map-new",
                      dispatch_base_commit=old_admission.payload["dispatch_base_commit"], **identity))
    admitted = admit_task_pipeline_generation(runtime, **inputs)
    assert admitted.causation_id == admitted_map.id
    assert admitted.payload["generation_id"] != old_admission.payload["generation_id"]
    runtime = Orchestrator(state, runtime.config, transport, project_root=runtime.project_root)
    before = [e.id for e in runtime.event_log.read_all()]
    assert admit_task_pipeline_generation(runtime, **inputs).id == admitted.id
    assert [e.id for e in runtime.event_log.read_all()] == before
    assert task_pipeline_generation_is_current(runtime.event_log.read_all(), workflow_run_id="run-1",
                                               generation_id=admitted.payload["generation_id"])
    assert not task_pipeline_generation_is_current(runtime.event_log.read_all(), workflow_run_id="run-1",
                                                   generation_id=old_admission.payload["generation_id"])
    for stage in ("impl", "verify"):
        assert [d.action for d in runtime._dispatch_ready()] == ["task_pipeline_dispatch"]
        context = transport.contexts[-1]
        assert context.task_pipeline_stage == stage
        for key in ("operation_id", "attempt_id", "dispatch_id", "task_stage_session_binding"):
            assert getattr(context, key) not in {getattr(old, key) for old in old_contexts}
        operation = reduce_workflow_operations(runtime.event_log.read_all())[context.operation_id]
        assert operation["task_map_generation"] == "map-new"
        assert operation["operation_generation"] == 1
        dispatch = next(e for e in runtime.event_log.read_all()
                        if e.type == "task.pipeline.stage.dispatched"
                        and e.payload["operation_id"] == context.operation_id)
        assert dispatch.causation_id == admitted.id
        snapshot = hydrate_task_contract_snapshot(state, {
            "ref": dispatch.payload["contract_snapshot_ref"], "sha256": dispatch.payload["contract_snapshot_digest"],
        })
        assert snapshot["task_map_generation"] == "map-new"
        assert snapshot["plan_artifact_package_id"] == descriptor["package_id"]
        old_dirs = {e.payload["workdir"] for e in runtime.event_log.read_all()
                    if e.type == "task.pipeline.stage.dispatched" and e.causation_id == old_admission.id}
        assert dispatch.payload["workdir"] not in old_dirs
        runtime._react_to_events(pushed=old_results)
        assert runtime.task_store.get(task.id).active_dispatch_id == context.dispatch_id
        assert TaskAttemptStore(state / "task_attempts.json").get(context.attempt_id)["status"] == "sent"
        assert runtime._dispatch_ready() == []
        assert not any(e.type in {"candidate.ready", "task.done"} for e in runtime.event_log.read_all())
        _submit_generation_stage(runtime, context, tokens[context.instance_id], repair=stage == "impl")
        runtime._react_to_events()
    assert "task_pipeline_candidate_frozen" in [d.action for d in runtime._dispatch_ready()]
    assert runtime.task_store.get(task.id).status == "done"
    for _ in range(2):
        runtime = Orchestrator(state, runtime.config, transport, project_root=runtime.project_root)
        assert admit_task_pipeline_generation(runtime, **inputs).id == admitted.id
        assert runtime._dispatch_ready() == []
    events = runtime.event_log.read_all()
    final_operations = reduce_workflow_operations(events)
    assert all(final_operations[key] == row for key, row in old_operations.items())
    assert len(transport.contexts) == len(old_stages) + 2
    assert sum(e.type == "task.pipeline.generation.admitted" for e in events) == 2
    [ready] = [e for e in events if e.type == "candidate.ready"]
    assert ready.payload["task_map_generation"] == "map-new"
    assert _git(runtime.project_root, "show", f"{ready.payload['commit']}:README.md") == "K462 repaired"
    assert not any(e.type in {"kernel.housekeeping.failed", "task.pipeline.stage.dispatch_deferred"} for e in events)
