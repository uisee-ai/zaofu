from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.schema import RoleConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_admission import (
    CallResultAdmissionOutcome,
    dispatch_call_result_correction,
)
from zf.runtime.call_result_adapters import ControlResultAdapterRegistry
from zf.runtime.call_result_envelope import (
    normalize_call_result_envelope,
    write_immutable_json_sidecar,
)
from zf.runtime.call_result_runtime import (
    admit_runtime_call_result,
    mark_call_operation_started,
    prepare_call_operation,
)
from zf.runtime.operation_projection import project_workflow_operation
from zf.runtime.plan_synth_handoff import build_plan_synth_call_payload
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_attempt_runtime import reconcile_task_attempts
from zf.runtime.workflow_anchor import mark_workflow_fanout_anchor


def _runtime(tmp_path: Path) -> SimpleNamespace:
    project_root = tmp_path / "project"
    state_dir = project_root / ".zf"
    state_dir.mkdir(parents=True, exist_ok=True)
    log = EventLog(state_dir / "events.jsonl")
    return SimpleNamespace(
        state_dir=state_dir,
        project_root=project_root,
        event_log=log,
        event_writer=EventWriter(log),
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={"result_protocol": {"mode": "blocking"}}
            )
        ),
    )


def test_generic_workflow_read_operation_uses_semantic_result_scratch(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata = {
        "flow_kind": "workflow",
        "result_protocol": {
            "mode": "blocking",
            "semantic_submit_profiles": {
                "workflow-read": "blocking",
                "artifact-delivery": "blocking",
            },
        },
    }
    payload = {
        "workflow_run_id": "run-generic-read",
        "flow_kind": "workflow",
        "generic_workflow_operation": "agent.read",
        "fanout_id": "fanout-collect",
        "child_id": "collector-b",
        "role_instance": "collector-b",
        "stage_id": "collect-b",
        "canonical_success_event": "workflow.child.completed",
        "canonical_failure_event": "workflow.child.failed",
    }

    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="collector-b",
        stage_id="collect-b",
        task_id="",
        dispatch_id="attempt-collector-b",
    )

    assert prepared.output_profile_id == "workflow-read"
    assert payload["semantic_result_submit_mode"] == "blocking"
    requested = next(
        event
        for event in runtime.event_log.read_all()
        if event.type == "workflow.operation.requested"
    )
    request = hydrate_sidecar_ref(
        runtime.state_dir,
        requested.payload["request_ref"],
    ).payload["request"]
    assert request["semantic_result_submit_mode"] == "blocking"
    assert request["result_scratch_ref"] == payload["result_scratch_ref"]


def test_stream_json_role_keeps_configured_semantic_submit_mode(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.config.roles = [RoleConfig(
        name="fix-lane",
        instance_id="fix-lane-0",
        backend="claude-code",
        transport="stream-json",
    )]
    runtime.config.workflow.flow_metadata = {
        "flow_kind": "issue",
        "result_protocol": {
            "mode": "blocking",
            "semantic_submit_profiles": {"implementation": "blocking"},
        },
    }
    payload = {
        "workflow_run_id": "run-stream-json-impl",
        "role_instance": "fix-lane-0",
        "stage_id": "impl",
        "task_pipeline_stage": "impl",
        "task_id": "TASK-IMPL",
        "output_profile_id": "implementation",
        "output_profile_revision": "1",
        "canonical_success_event": "dev.build.done",
        "canonical_failure_event": "dev.blocked",
    }

    prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="task-stage",
        operation_key="task-impl:impl:g1",
        stage_id="impl",
        task_id="TASK-IMPL",
        dispatch_id="attempt-stream-json-impl",
    )

    assert payload["semantic_result_submit_mode"] == "blocking"
    requested = next(
        event
        for event in runtime.event_log.read_all()
        if event.type == "workflow.operation.requested"
    )
    request = hydrate_sidecar_ref(
        runtime.state_dir,
        requested.payload["request_ref"],
    ).payload["request"]
    assert request["output_profile_id"] == "implementation"
    assert request["semantic_result_submit_mode"] == "blocking"


def test_workflow_read_operation_accepts_bootstrap_task_without_task_map(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.task_store = TaskStore(runtime.state_dir / "kanban.json")
    runtime.task_store.add(mark_workflow_fanout_anchor(
        Task(id="FLOW-ANCHOR", title="workflow anchor"),
        request_id="run-read",
        workflow_run_id="run-read",
        pattern_id="issue-triage",
    ))
    payload = {
        "workflow_run_id": "run-read",
        "fanout_id": "fanout-scan",
        "child_id": "issue-triage",
        "stage_id": "issue-triage",
        "task_id": "FLOW-ANCHOR",
        "canonical_success_event": "issue.triage.child.completed",
        "canonical_failure_event": "issue.triage.child.failed",
    }

    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="issue-triage",
        stage_id="issue-triage",
        task_id="FLOW-ANCHOR",
        dispatch_id="run-fanout-issue-triage",
    )

    assert prepared.output_profile_id == "workflow-read"
    assert payload["attempt_source_manifest_ref"]
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="FLOW-ANCHOR",
        dispatch_id="run-fanout-issue-triage",
    )
    attempt_store = TaskAttemptStore(
        runtime.state_dir / "task_attempts.json"
    )
    attempt_store.ensure_for_dispatch(
        run_id="run-read",
        task_id="FLOW-ANCHOR",
        dispatch_id="run-fanout-issue-triage",
        role="issue-triage",
        instance_id="issue-triage",
        operation_id=prepared.operation_id,
        briefing_ref="briefings/issue-triage.md",
        created_at="2026-07-28T00:00:00+00:00",
        lease_expires_at="2099-07-28T00:00:00+00:00",
        max_attempts=0,
    )
    result = ZfEvent(
        type="issue.triage.child.completed",
        actor="issue-triage",
        payload={
            "report": {
                "status": "passed",
                "summary": "triage complete",
                "recommendation": "approve",
                "findings": [],
            },
        },
        correlation_id="run-read",
    )
    runtime.event_log.append(result)

    outcome = admit_runtime_call_result(
        runtime,
        result,
        merged_payload=payload,
        mode="blocking",
    )

    assert outcome.admitted
    events = runtime.event_log.read_all()
    admitted = next(
        event
        for event in events
        if event.type == "workflow.call.result.admitted"
    )
    settled = next(
        event
        for event in events
        if event.type == "workflow.operation.settled"
    )
    assert admitted.payload["control_result_schema"] == "workflow-read-result.v1"
    assert settled.task_id == "FLOW-ANCHOR"
    assert settled.payload["operation_id"] == prepared.operation_id
    assert reconcile_task_attempts(runtime) >= 1
    assert reconcile_task_attempts(runtime) == 0
    attempt = attempt_store.current(
        run_id="run-read",
        task_id="FLOW-ANCHOR",
    )
    assert attempt is not None
    assert attempt["status"] == "succeeded"


def test_post_verify_discovery_candidate_evidence_stays_workflow_read(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.task_store = TaskStore(runtime.state_dir / "kanban.json")
    runtime.task_store.add(mark_workflow_fanout_anchor(
        Task(id="PRD-ANCHOR", title="PRD workflow anchor"),
        request_id="run-prd",
        workflow_run_id="run-prd",
        pattern_id="prd-scan",
    ))
    evidence = runtime.project_root / "artifacts" / "candidate-evidence.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({"verified": True}), encoding="utf-8")
    payload = {
        "workflow_run_id": "run-prd",
        "fanout_id": "fanout-prd-post-verify-discovery",
        "child_id": "flow-discovery",
        "stage_id": "prd-post-verify-discovery",
        "task_id": "PRD-ANCHOR",
        "task_map_generation": "generation-1",
        "candidate_head_commit": "candidate-head",
        "artifact_refs": [str(evidence)],
        "canonical_success_event": "flow.discovery.child.completed",
        "canonical_failure_event": "flow.discovery.child.failed",
    }

    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="flow-discovery",
        stage_id="prd-post-verify-discovery",
        task_id="PRD-ANCHOR",
        dispatch_id="run-flow-discovery",
    )

    assert prepared.output_profile_id == "workflow-read"
    assert payload["handoff_authority_contract"]["requires_candidate_lineage"] is False
    assert payload["attempt_source_manifest_ref"]


def test_taskless_child_inherits_workflow_parent_lineage(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.event_log.append(ZfEvent(
        type="workflow.invoke.accepted",
        actor="zf-cli",
        task_id="FLOW-PARENT",
        payload={
            "task_id": "FLOW-PARENT",
            "workflow_run_id": "run-parent",
        },
        correlation_id="run-parent",
    ))
    payload = {
        "workflow_run_id": "run-parent",
        "fanout_id": "fanout-plan",
        "child_id": "planner",
        "stage_id": "issue-triage",
    }

    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="planner",
        stage_id="issue-triage",
        task_id="",
        dispatch_id="dispatch-plan",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="",
        dispatch_id="dispatch-plan",
    )

    assert prepared.task_id == "FLOW-PARENT"
    assert prepared.parent_task_id == "FLOW-PARENT"
    assert payload["task_id"] == "FLOW-PARENT"
    assert payload["parent_task_id"] == "FLOW-PARENT"
    operation_events = [
        event
        for event in runtime.event_log.read_all()
        if event.type.startswith("workflow.operation.")
    ]
    assert [event.task_id for event in operation_events] == [
        "FLOW-PARENT",
        "FLOW-PARENT",
    ]
    assert all(
        event.payload["parent_task_id"] == "FLOW-PARENT"
        for event in operation_events
    )


def test_selected_call_result_replays_settled_operation_without_redispatch(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    source = runtime.project_root / "inputs" / "context.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"facts": ["one"]}), encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    base_payload = {
        "workflow_run_id": "run-1",
        "fanout_id": "F1",
        "child_id": "C1",
        "stage_id": "impl",
        "task_id": "T1",
        "task_map_generation": "g1",
        "plan_artifact_package_id": "planpkg-current",
        "plan_artifact_package_ref": "artifacts/plan-packages/current.json",
        "plan_artifact_package_digest": "package-sha",
        "run_contract_ref": "artifacts/run-contracts/current.json",
        "run_contract_digest": "contract-sha",
        "effective_config_ref": {
            "ref": "artifacts/effective-config/current.json",
            "sha256": "effective-sha",
        },
        "effective_config_digest": "effective-sha",
        "artifact_refs": [{
            "source_id": "context",
            "artifact_id": "context",
            "kind": "context",
            "ref": "inputs/context.json",
            "sha256": digest,
        }],
        "required_reads": [{
            "source_id": "context",
            "artifact_id": "context",
            "artifact_sha256": digest,
            "json_path": "$.facts",
        }],
    }
    runtime.event_log.append(ZfEvent(
        id="evt-plan-package-current",
        type="plan.artifact_package.admitted",
        correlation_id="run-1",
        payload={
            "status": "admitted",
            "workflow_run_id": "run-1",
            "plan_revision": "r1",
            "task_map_generation": "g1",
            "package_id": "planpkg-current",
            "package_ref": "artifacts/plan-packages/current.json",
            "package_digest": "package-sha",
        },
    ))
    payload = dict(base_payload)
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_writer_child",
        operation_key="C1",
        stage_id="impl",
        task_id="T1",
        dispatch_id="attempt-1",
    )
    assert prepared.should_dispatch is True
    assert prepared.output_profile_id == "implementation"
    requested = next(
        event for event in runtime.event_log.read_all()
        if event.type == "workflow.operation.requested"
    )
    request = hydrate_sidecar_ref(
        runtime.state_dir,
        requested.payload["request_ref"],
    ).payload["request"]
    assert request["output_profile_id"] == "implementation"
    assert request["output_profile_revision"] == "1"
    assert request["execution_profile"]["profile_id"] == "direct-v1"
    assert request["execution_profile"]["profile_digest"]
    assert request["execution_profile"]["shadow"] == {
        "verdict": "supported",
        "reason": "direct_profile",
        "dispatch_effect": "none",
    }
    assert payload["execution_profile_digest"] == request[
        "execution_profile"
    ]["profile_digest"]
    assert request["attempt_domain"] == "task"
    assert request["handoff_authority_contract"] == {
        "schema_version": "handoff-authority-contract.v1",
        "output_profile_id": "implementation",
        "stage_id": "impl",
        "operation_type": "fanout_writer_child",
        "attempt_domain": "task",
        "continuation_key": "",
        "requires_prior_task_ref": False,
        "requires_candidate_lineage": False,
    }
    assert request["result_identity"]["plan_artifact_package_id"] == "planpkg-current"
    assert request["result_identity"]["run_contract_digest"] == "contract-sha"
    assert request["effective_config_ref"] == base_payload[
        "effective_config_ref"
    ]
    assert request["effective_config_digest"] == "effective-sha"
    assert request["result_identity"]["effective_config_digest"] == (
        "effective-sha"
    )
    manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    read_attempt_artifact(
        runtime.state_dir,
        manifest=manifest,
        source_id="context",
        artifact_id="context",
        json_path="$.facts",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="T1",
        dispatch_id="attempt-1",
    )
    running_projection = project_workflow_operation(
        runtime.state_dir,
        prepared.operation_id,
    )
    assert running_projection["status"] == "running"
    terminal = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="T1",
        payload={
            **payload,
            "target_commit": "abc123",
            "source_commit": "abc123",
            "task_ref": "refs/zf/tasks/T1",
            "summary": "done",
        },
    )
    outcome = admit_runtime_call_result(runtime, terminal, mode="blocking")
    assert outcome.admitted is True

    restarted = _runtime(tmp_path)
    replay_payload = dict(base_payload)
    replay = prepare_call_operation(
        restarted,
        payload=replay_payload,
        operation_type="fanout_writer_child",
        operation_key="C1",
        stage_id="impl",
        task_id="T1",
        dispatch_id="attempt-1",
    )
    assert replay.ensure_status == "settled"
    assert replay.should_dispatch is False
    assert replay.admitted_call_result_ref == outcome.envelope_ref["ref"]
    projection = project_workflow_operation(runtime.state_dir, replay.operation_id)
    assert projection["status"] == "settled"
    assert projection["blockers"] == []


def test_inherited_operation_identity_is_rederived_per_stage(tmp_path: Path) -> None:
    """ZF-REVIEW-140-B3:verify child 的 payload 派生自上游 impl manifest
    child,继承的 operation_id 曾压过本段派生 → request_hash_divergence 环。
    修复后:同一 payload 流经不同 stage 必须得到不同、且本段稳定的身份。"""
    runtime = _runtime(tmp_path)
    contract = write_immutable_json_sidecar(
        runtime.state_dir,
        {"schema_version": "task-contract-snapshot.v1", "task_id": "T1"},
        root="fixtures/handoff/contract",
        kind="task_contract_snapshot",
        schema_version="task-contract-snapshot.v1",
        created_by="test",
    )
    feedback = write_immutable_json_sidecar(
        runtime.state_dir,
        {"schema_version": "rework-feedback.v1", "task_id": "T1"},
        root="fixtures/handoff/feedback",
        kind="rework_feedback",
        schema_version="rework-feedback.v1",
        created_by="test",
    )
    payload = {
        "workflow_run_id": "run-1",
        "fanout_id": "F1",
        "child_id": "C1",
        "stage_id": "impl",
        "task_id": "T1",
        "contract_revision": "contract-r1",
        "task_map_generation": "generation-r1",
        "plan_artifact_package_ref": "artifacts/plan/package-r1.json",
        "plan_artifact_package_digest": "a" * 64,
        "contract_snapshot_ref": contract["ref"],
        "contract_snapshot_digest": contract["sha256"],
        "rework_feedback_ref": feedback["ref"],
        "rework_feedback_digest": feedback["sha256"],
    }
    impl = prepare_call_operation(
        runtime, payload=payload, operation_type="fanout_writer_child",
        operation_key="dev-lane-0-T1", stage_id="prd-lanes-impl",
        task_id="T1", dispatch_id="run-F1-impl",
    )
    impl_manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    # payload 现在带着 impl 的 operation_id(manifest 持久化的污染形态)
    assert payload["operation_id"] == impl.operation_id
    verify = prepare_call_operation(
        runtime, payload=payload, operation_type="fanout_reader_child",
        operation_key="verify-lane-0-T1", stage_id="prd-lanes-verify",
        task_id="T1", dispatch_id="run-F2-verify",
    )
    verify_manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    assert verify.operation_id != impl.operation_id, "verify 不得继承 impl 身份"
    for manifest in (impl_manifest, verify_manifest):
        assert manifest["task_map_generation"] == "generation-r1"
        assert manifest["plan_artifact_package_digest"] == "a" * 64
        assert manifest["feedback_revision"] == feedback["sha256"]
        assert {source["source_id"] for source in manifest["sources"]} >= {
            "contract",
            "rework-feedback",
        }
    requests = {
        event.payload["operation_id"]: hydrate_sidecar_ref(
            runtime.state_dir,
            event.payload["request_ref"],
        ).payload["request"]
        for event in runtime.event_log.read_all()
        if event.type == "workflow.operation.requested"
    }
    for operation in (impl, verify):
        assert requests[operation.operation_id]["result_identity"][
            "feedback_revision"
        ] == feedback["sha256"]
    # 同 dispatch 重放:身份稳定(replay 语义不变)
    replay = prepare_call_operation(
        runtime, payload=dict(payload), operation_type="fanout_reader_child",
        operation_key="verify-lane-0-T1", stage_id="prd-lanes-verify",
        task_id="T1", dispatch_id="run-F2-verify",
    )
    assert replay.operation_id == verify.operation_id


def test_candidate_verify_operation_requires_current_contract_target_and_self_check(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    descriptors: dict[str, tuple[str, str]] = {}
    for source_id in ("contract", "target", "impl-self-check"):
        ref = f"artifacts/{source_id}.json"
        path = runtime.state_dir / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"source": source_id}), encoding="utf-8")
        descriptors[source_id] = (ref, hashlib.sha256(path.read_bytes()).hexdigest())
    payload = {
        "workflow_run_id": "run-candidate",
        "task_id": "T1",
        "output_profile_id": "candidate-verify",
        "contract_snapshot_ref": descriptors["contract"][0],
        "contract_snapshot_digest": descriptors["contract"][1],
        "target_snapshot_ref": descriptors["target"][0],
        "target_snapshot_digest": descriptors["target"][1],
        "impl_self_check_ref": descriptors["impl-self-check"][0],
        "impl_self_check_digest": descriptors["impl-self-check"][1],
    }

    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="candidate-verify-T1",
        stage_id="candidate-verify",
        task_id="T1",
        dispatch_id="attempt-candidate",
    )

    assert prepared.output_profile_id == "candidate-verify"
    manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    assert {item["source_id"] for item in manifest["sources"]} == {
        "contract",
        "target",
        "impl-self-check",
    }
    assert {item["source_id"] for item in payload["required_reads"]} == {
        "contract",
        "target",
        "impl-self-check",
    }


def test_runtime_admission_uses_output_profile_from_pinned_operation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _runtime(tmp_path)
    payload = {
        "workflow_run_id": "run-rescan",
        "fanout_id": "fanout-rescan",
        "child_id": "flow-discovery",
        "stage_id": "post-verify-discovery",
        "role_instance": "flow-discovery",
        "output_profile_id": "global-rescan",
        "output_profile_revision": "1",
    }
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="flow-discovery",
        stage_id="post-verify-discovery",
        task_id="",
        dispatch_id="run-rescan-discovery",
    )
    captured: dict = {}

    class _Admission:
        def report_legacy_result(self, _event, **kwargs):  # noqa: ANN001
            captured.update(kwargs["operation"])
            return CallResultAdmissionOutcome(status="admitted", mode="blocking")

    monkeypatch.setattr(
        "zf.runtime.call_result_adapters.hydrate_profiled_control_result_event",
        lambda _state_dir, event: event,
    )
    monkeypatch.setattr(
        "zf.runtime.call_result_runtime.call_result_admission_service",
        lambda _runtime: _Admission(),
    )
    event = ZfEvent(
        type="flow.discovery.child.failed",
        actor="flow-discovery",
        payload={
            "operation_id": prepared.operation_id,
            "request_hash": prepared.request_hash,
            "workflow_run_id": prepared.workflow_run_id,
            "report": {"status": "failed", "recommendation": "reject"},
        },
    )

    outcome = admit_runtime_call_result(runtime, event, mode="blocking")

    assert outcome.admitted
    assert captured["output_profile_id"] == "global-rescan"
    assert captured["output_profile_revision"] == "1"


def test_runtime_admission_uses_pinned_adapter_when_result_omits_profile(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    payload = {
        "workflow_run_id": "run-rescan-admission",
        "fanout_id": "fanout-rescan-admission",
        "child_id": "flow-discovery",
        "stage_id": "post-verify-discovery",
        "role_instance": "flow-discovery",
        "output_profile_id": "global-rescan",
        "output_profile_revision": "1",
    }
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="flow-discovery",
        stage_id="post-verify-discovery",
        task_id="",
        dispatch_id="run-rescan-admission-discovery",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="",
        dispatch_id="run-rescan-admission-discovery",
    )
    result = ZfEvent(
        type="flow.discovery.child.failed",
        actor="flow-discovery",
        payload={
            "operation_id": prepared.operation_id,
            "request_hash": prepared.request_hash,
            "workflow_run_id": prepared.workflow_run_id,
            "report": {
                "status": "failed",
                "recommendation": "reject",
                "summary": "manual evidence is unavailable",
            },
        },
        correlation_id=prepared.workflow_run_id,
    )

    outcome = admit_runtime_call_result(runtime, result, mode="blocking")

    assert outcome.status != "unsupported"
    reported = next(
        event for event in runtime.event_log.read_all()
        if event.type == "workflow.call.result.reported"
    )
    assert reported.payload["adapter_id"] == "verification-result-v1-explicit"
    assert not {
        issue["code"] for issue in reported.payload["issues"]
    }.intersection({"adapter_missing", "call_result_unsupported"})


def _prepared_plan_synth(tmp_path: Path):
    runtime = _runtime(tmp_path)
    report_path = runtime.state_dir / "fanouts" / "F-PLAN" / "children" / "planner" / "report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps({
        "status": "passed",
        "recommendation": "approve",
        "task_map": {"tasks": [{"task_id": "T1"}]},
    }), encoding="utf-8")
    manifest = {
        "fanout_id": "F-PLAN",
        "trace_id": "run-plan",
        "workflow_run_id": "run-plan",
        "stage_id": "plan",
        "trigger_event_id": "evt-plan-trigger",
        "target_ref": "main",
    }
    payload = build_plan_synth_call_payload(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        manifest=manifest,
        reports=[{
            "child_id": "planner",
            "report_path": str(report_path),
        }],
        run_id="run-F-PLAN-synth",
        role_instance="plan-critic",
    )
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_synth",
        operation_key="synth@trig:evt-plan-tri",
        stage_id="plan",
        task_id="",
        dispatch_id="run-F-PLAN-synth",
    )
    mark_call_operation_started(
        runtime,
        prepared,
        task_id="",
        dispatch_id="run-F-PLAN-synth",
    )
    return runtime, payload, prepared


def _plan_synth_event(payload: dict, *, plan_revision: str = "") -> ZfEvent:
    return ZfEvent(
        type="fanout.synth.completed",
        actor="plan-critic",
        correlation_id="run-plan",
        payload={
            **payload,
            "plan_revision": plan_revision or payload["plan_revision"],
            "status": "completed",
            "recommendation": "approve",
            "summary": "plan ready",
            "report": {
                "status": "passed",
                "recommendation": "approve",
                "summary": "plan ready",
            },
        },
    )


def test_plan_synth_requires_relocated_child_artifacts(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    task_map = (
        runtime.state_dir
        / "artifacts"
        / "fanouts"
        / "F-PLAN"
        / "planner"
        / "task_map.json"
    )
    task_map.parent.mkdir(parents=True)
    task_map.write_text(
        json.dumps({"tasks": [{"task_id": "T-CHILD"}]}),
        encoding="utf-8",
    )
    report_path = (
        runtime.state_dir
        / "fanouts"
        / "F-PLAN"
        / "children"
        / "planner"
        / "report.json"
    )
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps({"status": "passed", "task_map_ref": str(task_map)}),
        encoding="utf-8",
    )
    payload = build_plan_synth_call_payload(
        state_dir=runtime.state_dir,
        project_root=runtime.project_root,
        manifest={
            "fanout_id": "F-PLAN",
            "trace_id": "run-plan",
            "stage_id": "plan",
            "trigger_event_id": "evt-plan-trigger",
        },
        reports=[{
            "child_id": "planner",
            "report_path": str(report_path),
            "report": {
                "status": "passed",
                "task_map_ref": str(task_map),
            },
        }],
        run_id="run-F-PLAN-synth",
        role_instance="plan-critic",
    )
    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_synth",
        operation_key="synth@trig:evt-plan-tri",
        stage_id="plan",
        task_id="",
        dispatch_id="run-F-PLAN-synth",
    )

    source_ids = {row["source_id"] for row in payload["required_reads"]}
    assert "child-artifact-planner-1" in source_ids
    manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    requirement = next(
        row
        for row in payload["required_reads"]
        if row["source_id"] == "child-artifact-planner-1"
    )
    read = read_attempt_artifact(
        runtime.state_dir,
        manifest=manifest,
        source_id=requirement["source_id"],
        artifact_id=requirement["artifact_id"],
    )
    assert json.loads(read["content"]) == {
        "tasks": [{"task_id": "T-CHILD"}]
    }


def test_plan_synth_semantic_result_preserves_ready_plan_ports(
    tmp_path: Path,
) -> None:
    semantic_result = {
        "schema_version": "plan-synthesis-result.v1",
        "execution_status": "completed",
        "verdict": "passed",
        "summary": "plan ready",
        "refactor_plan_md": "# Refactor plan",
        "task_map": {"tasks": [{"task_id": "T-1"}]},
        "gates": ["pytest -q"],
        "owner_decision_items": [{
            "decision_id": "ODI-1",
            "question": "May the Plan add one compatibility fixture?",
            "options": [
                {"option_id": "approve", "label": "Add fixture"},
                {"option_id": "reject", "label": "Keep current scope"},
            ],
            "blocking": True,
            "evidence_refs": ["requirements:AC-1"],
        }],
        "plan_ports": [{
            "logical_name": "acceptance_matrix",
            "schema_version": "acceptance-matrix.v1",
            "body": {
                "schema_version": "acceptance-matrix.v1",
                "status": "ready",
                "metadata": {
                    "enrichment_contract": {"status": "fulfilled"},
                },
                "rows": [{"acceptance_id": "AC-1"}],
            },
        }],
    }
    _event, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        tmp_path / ".zf",
        profile_id="plan-synth",
        revision="1",
        event_type="fanout.synth.completed",
        semantic_result=semantic_result,
        identity={
            "output_profile_id": "plan-synth",
            "workflow_run_id": "run-plan",
            "fanout_id": "F-PLAN",
            "stage_id": "plan",
            "plan_revision": "plan-r2",
            "plan_synth_contract_ref": "artifacts/contracts/plan-r2.json",
            "plan_synth_contract_digest": "a" * 64,
            "reviewed_plan_candidate_digest": "a" * 64,
        },
        source_event_id="evt-plan-result",
        actor="plan-critic",
        task_id="",
        correlation_id="run-plan",
    )

    assert adapted.payload["plan_ports"] == semantic_result["plan_ports"]
    assert adapted.payload["refactor_plan_md"] == "# Refactor plan"
    assert adapted.payload["task_map"] == {"tasks": [{"task_id": "T-1"}]}
    assert adapted.payload["gates"] == ["pytest -q"]
    assert adapted.payload["owner_decision_items"] == (
        semantic_result["owner_decision_items"]
    )


def test_plan_synth_required_reads_repair_then_admit_and_replay(
    tmp_path: Path,
) -> None:
    runtime, payload, prepared = _prepared_plan_synth(tmp_path)
    assert prepared.output_profile_id == "plan-synth"
    assert {row["source_id"] for row in payload["required_reads"]} == {
        "plan-synth-contract",
        "child-result-planner",
    }

    missing = admit_runtime_call_result(
        runtime,
        _plan_synth_event(payload),
        mode="blocking",
        dispatch_correction=False,
    )
    assert missing.repair_requested is True
    assert missing.repair_round == 1

    manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    for requirement in payload["required_reads"]:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=manifest,
            source_id=requirement["source_id"],
            artifact_id=requirement["artifact_id"],
        )
    admitted = admit_runtime_call_result(
        runtime,
        _plan_synth_event(payload),
        mode="blocking",
        dispatch_correction=False,
    )
    assert admitted.admitted is True
    duplicate = admit_runtime_call_result(
        runtime,
        _plan_synth_event(payload),
        mode="blocking",
        dispatch_correction=False,
    )
    assert duplicate.admitted is True
    assert duplicate.admitted_event_id == admitted.admitted_event_id

    restarted = _runtime(tmp_path)
    replay_payload = build_plan_synth_call_payload(
        state_dir=restarted.state_dir,
        project_root=restarted.project_root,
        manifest={
            "fanout_id": "F-PLAN",
            "trace_id": "run-plan",
            "workflow_run_id": "run-plan",
            "stage_id": "plan",
            "trigger_event_id": "evt-plan-trigger",
            "target_ref": "main",
        },
        reports=[{
            "child_id": "planner",
            "report_path": str(
                restarted.state_dir
                / "fanouts/F-PLAN/children/planner/report.json"
            ),
        }],
        run_id="run-F-PLAN-synth",
        role_instance="plan-critic",
    )
    replay = prepare_call_operation(
        restarted,
        payload=replay_payload,
        operation_type="fanout_synth",
        operation_key="synth@trig:evt-plan-tri",
        stage_id="plan",
        task_id="",
        dispatch_id="run-F-PLAN-synth",
    )
    assert replay.ensure_status == "settled"
    assert replay.output_profile_id == "plan-synth"
    assert replay.output_profile_revision == "1"


def test_plan_synth_stale_revision_is_superseded(tmp_path: Path) -> None:
    runtime, payload, _prepared = _prepared_plan_synth(tmp_path)
    manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    for requirement in payload["required_reads"]:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=manifest,
            source_id=requirement["source_id"],
            artifact_id=requirement["artifact_id"],
        )

    outcome = admit_runtime_call_result(
        runtime,
        _plan_synth_event(payload, plan_revision="plan-rstale"),
        mode="blocking",
        dispatch_correction=False,
    )
    assert outcome.status == "superseded"
    assert {issue["code"] for issue in outcome.issues} == {"stale_plan_revision"}
    assert not [
        event for event in runtime.event_log.read_all()
        if event.type == "workflow.call.result.admitted"
    ]


def test_call_result_correction_waits_for_source_turn_stop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.config.roles = [RoleConfig(
        name="verify",
        instance_id="verify-1",
        backend="codex",
        role_kind="reader",
    )]
    source_event = ZfEvent(
        type="verify.child.failed",
        actor="verify-1",
        task_id="T1",
        payload={
            "role_instance": "verify-1",
            "result_scratch_ref": "tmp/result-submit/op-1/result.json",
        },
    )
    runtime.event_log.append(source_event)

    def append_stop(_delay: float) -> None:
        runtime.event_log.append(ZfEvent(
            type="codex.hook.stop",
            actor="verify-1",
        ))

    monkeypatch.setattr(
        "zf.runtime.call_result_correction.time.sleep",
        append_stop,
    )
    sent: list[tuple[str, Path]] = []

    def send_task(role_name, briefing_path, _prompt, _context):  # noqa: ANN001
        assert runtime.event_log.read_all()[-1].type == "codex.hook.stop"
        sent.append((role_name, briefing_path))

    runtime._dispatch_context = lambda **_kwargs: None
    runtime._send_transport_task = send_task
    outcome = CallResultAdmissionOutcome(
        status="repair_pending",
        mode="blocking",
        operation_id="op-1",
        request_hash="request-1",
        repair_round=1,
        correction_ref={"ref": "artifacts/correction.json"},
        correction_dispatch_required=True,
        issues=({
            "field": "control_result.workflow_output_ports[0]",
            "code": "workflow_output_missing",
            "message": "scope.audit_report",
        },),
    )

    assert dispatch_call_result_correction(
        runtime,
        source_event=source_event,
        outcome=outcome,
    ) is True
    assert sent[0][0] == "verify-1"
    assert sent[0][1].name == "verify-1-T1-result-correction-1.md"
    briefing = sent[0][1].read_text(encoding="utf-8")
    assert "outputs.audit_report" in briefing
    assert "zf result submit --operation op-1" in briefing
    assert "Do not merely change finding severity" in briefing


def test_rework_of_scopes_new_operation(tmp_path: Path) -> None:
    """ZF-REVIEW-140-B3:带 rework_of 的 retrigger 是新 operation(140
    裁决 10:rework 不是 replay),不得与原 operation 撞名。"""
    runtime = _runtime(tmp_path)
    base = {
        "workflow_run_id": "run-1", "fanout_id": "F1",
        "child_id": "C1", "stage_id": "impl", "task_id": "T1",
    }
    first = prepare_call_operation(
        runtime, payload=dict(base), operation_type="fanout_writer_child",
        operation_key="dev-lane-0-T1", stage_id="prd-lanes-impl",
        task_id="T1", dispatch_id="run-F1",
    )
    retrigger = prepare_call_operation(
        runtime, payload={**base, "rework_of": "evt-rej-1"},
        operation_type="fanout_writer_child",
        operation_key="dev-lane-0-T1", stage_id="prd-lanes-impl",
        task_id="T1", dispatch_id="run-F3",
    )
    assert retrigger.operation_id != first.operation_id


def test_flow_plan_does_not_pin_planner_owned_generation(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    from tests.test_plan_artifact_package import _package
    from zf.runtime.plan_artifact_package import write_plan_artifact_package

    package = _package(runtime.state_dir)
    package["workflow_run_id"] = "run-replan"
    baseline = write_plan_artifact_package(runtime.state_dir, package)
    runtime.event_log.append(ZfEvent(
        type="plan.artifact_package.admitted",
        correlation_id="run-replan",
        payload={
            "workflow_run_id": "run-replan",
            "package_id": "planpkg-old",
            "package_ref": baseline["ref"],
            "package_digest": baseline["sha256"],
            "mode": "blocking",
            "status": "admitted",
        },
    ))
    payload = {
        "workflow_run_id": "run-replan",
        "fanout_id": "fanout-plan",
        "child_id": "refactor-plan-synth",
        "stage_id": "flow-plan",
        "role_instance": "refactor-plan-synth",
        "plan_revision": "revision-0002",
        "task_map_generation": "old-generation",
        "plan_artifact_package_id": "planpkg-old",
        "plan_artifact_package_ref": baseline["ref"],
        "plan_artifact_package_digest": baseline["sha256"],
        "rework_of": "evt-integration-failed",
    }

    prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="refactor-plan-synth",
        stage_id="flow-plan",
        task_id="",
        dispatch_id="run-fanout-plan-refactor-plan-synth",
    )

    requested = next(
        event for event in runtime.event_log.read_all()
        if event.type == "workflow.operation.requested"
    )
    request = hydrate_sidecar_ref(
        runtime.state_dir,
        requested.payload["request_ref"],
    ).payload["request"]
    identity = request["result_identity"]
    assert "plan_revision" not in identity
    assert "task_map_generation" not in identity
    assert "plan_artifact_package_id" not in identity
    assert "plan_artifact_package_ref" not in identity
    assert "plan_artifact_package_digest" not in identity
    assert identity["workflow_run_id"] == "run-replan"
    assert identity["fanout_id"] == "fanout-plan"


def test_artifact_delivery_operation_pins_generic_goal_identity(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata_by_kind = {
        "issue": {
            "result_protocol": {"mode": "blocking"},
        },
        "workflow": {
            "result_protocol": {
                "mode": "blocking",
                "semantic_submit_profiles": {
                    "artifact-delivery": "blocking",
                },
            },
        },
    }
    input_result_ref = (
        "artifacts/call-results/envelopes/" + "e" * 64 + ".json"
    )
    payload = {
        "workflow_run_id": "run-generic",
        "fanout_id": "fanout-verify",
        "child_id": "workflow-verifier",
        "stage_id": "verify",
        "role_instance": "workflow-verifier",
        "flow_kind": "workflow",
        "goal_id": "goal-generic",
        "workflow_generation": "a" * 64,
        "request_revision": 2,
        "generic_workflow_contract_digest": "b" * 64,
        "workflow_intent": "research",
        "workflow_template": "evidence-synthesis-v1",
        "completion_profile": "artifact_delivery",
        "required_delivery_artifacts": [{
            "name": "report",
            "kind": "report/markdown",
            "source_ref": "synthesize.report",
        }],
        "goal_claim_set_ref": (
            "artifacts/goal-closure/claim-sets/current.json"
        ),
        "goal_claim_set_digest": "c" * 64,
        "run_contract_ref": "artifacts/run-contracts/current.json",
        "run_contract_digest": "d" * 64,
        "input_result_refs": [input_result_ref],
        "output_profile_id": "artifact-delivery",
        "output_profile_revision": "1",
    }

    prepared = prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="workflow-verifier",
        stage_id="verify",
        task_id="",
        dispatch_id="run-fanout-verify-workflow-verifier",
    )

    requested = next(
        event
        for event in runtime.event_log.read_all()
        if event.type == "workflow.operation.requested"
    )
    request = hydrate_sidecar_ref(
        runtime.state_dir,
        requested.payload["request_ref"],
    ).payload["request"]
    identity = request["result_identity"]
    assert identity["goal_id"] == "goal-generic"
    assert identity["workflow_generation"] == "a" * 64
    assert identity["workflow_intent"] == "research"
    assert identity["goal_claim_set_ref"].endswith("current.json")
    assert identity["goal_claim_set_digest"] == "c" * 64
    assert identity["input_result_refs"] == [input_result_ref]
    assert identity["required_delivery_artifacts"][0]["source_ref"] == (
        "synthesize.report"
    )
    assert request["semantic_result_submit_mode"] == "blocking"
    assert prepared.output_profile_id == "artifact-delivery"


def test_artifact_delivery_operation_routes_producer_output_as_controlled_input(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata_by_kind = {
        "workflow": {
            "result_protocol": {
                "mode": "blocking",
                "semantic_submit_profiles": {
                    "artifact-delivery": "blocking",
                },
            },
        },
    }
    source_event, adapted = ControlResultAdapterRegistry().adapt_semantic_result(
        runtime.state_dir,
        profile_id="workflow-read",
        revision="1",
        event_type="workflow.child.completed",
        semantic_result={
            "status": "passed",
            "summary": "# Report\n\nVerified source synthesis.",
            "recommendation": "approve",
            "findings": [],
        },
        identity={
            "workflow_run_id": "run-generic-output",
            "stage_id": "synthesize",
            "generic_workflow_operation": "agent.synthesize",
            "result_semantics": "artifact_production",
            "workflow_output_ports": [{
                "name": "report",
                "kind": "report/markdown",
            }],
        },
        source_event_id="evt-synthesize-output",
        actor="workflow-synthesizer",
        task_id="",
        correlation_id="run-generic-output",
    )
    artifact = adapted.payload["output_artifacts"][0]
    envelope = normalize_call_result_envelope(
        source_payload={
            **source_event.payload,
            "attempt_id": "attempt-synthesize",
        },
        control_result={
            "schema_version": adapted.schema_version,
            **adapted.descriptor,
        },
        workflow_run_id="run-generic-output",
        operation_id="operation-synthesize",
        request_hash="request-synthesize",
        source_event_id="evt-synthesize-output",
        source_event_type=source_event.type,
        actor="workflow-synthesizer",
        correlation_id="run-generic-output",
    )
    envelope_ref = write_immutable_json_sidecar(
        runtime.state_dir,
        envelope,
        root="call-results/envelopes",
        kind="call_result_envelope",
        schema_version="call-result-envelope.v1",
        created_by="test",
    )
    payload = {
        "workflow_run_id": "run-generic-output",
        "fanout_id": "fanout-verify",
        "child_id": "workflow-verifier",
        "stage_id": "verify",
        "role_instance": "workflow-verifier",
        "flow_kind": "workflow",
        "goal_id": "goal-generic",
        "workflow_generation": "a" * 64,
        "request_revision": 1,
        "generic_workflow_contract_digest": "b" * 64,
        "workflow_intent": "research",
        "workflow_template": "evidence-synthesis-v1",
        "completion_profile": "artifact_delivery",
        "required_delivery_artifacts": [{
            "name": "report",
            "kind": "report/markdown",
            "source_ref": "synthesize.report",
        }],
        "goal_claim_set_ref": "artifacts/goal-closure/current.json",
        "goal_claim_set_digest": "c" * 64,
        "run_contract_ref": "artifacts/run-contracts/current.json",
        "run_contract_digest": "d" * 64,
        "input_result_refs": [envelope_ref["ref"]],
        "output_profile_id": "artifact-delivery",
        "output_profile_revision": "1",
    }

    prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="workflow-verifier",
        stage_id="verify",
        task_id="",
        dispatch_id="attempt-verify",
    )

    manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    assert artifact["ref"] in {
        source["ref"] for source in manifest["sources"]
    }
    assert any(
        read["artifact_sha256"] == artifact["sha256"]
        for read in payload["required_reads"]
    )


def test_flow_plan_pins_requirement_and_rework_context(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    requirement = (
        runtime.state_dir
        / "workflow-requests"
        / "run-replan"
        / "requirement.json"
    )
    requirement.parent.mkdir(parents=True)
    requirement.write_text(
        '{"acceptance":["Keep the canonical claim text."]}',
        encoding="utf-8",
    )
    payload = {
        "workflow_run_id": "run-replan",
        "fanout_id": "fanout-plan",
        "child_id": "refactor-plan-synth",
        "stage_id": "flow-plan",
        "role_instance": "refactor-plan-synth",
        "trigger_payload": {
            "requirement_spec_ref": str(requirement),
            "rework_of": "evt-package-rejected",
            "rework_attempt": 2,
            "rework_feedback": [
                "Preserve the canonical goal claim text exactly.",
            ],
        },
    }

    prepare_call_operation(
        runtime,
        payload=payload,
        operation_type="fanout_reader_child",
        operation_key="refactor-plan-synth",
        stage_id="flow-plan",
        task_id="",
        dispatch_id="run-fanout-plan-refactor-plan-synth",
    )

    manifest = hydrate_sidecar_ref(
        runtime.state_dir,
        payload["attempt_source_manifest"],
    ).payload
    source_ids = {
        str(source.get("source_id") or "")
        for source in manifest["sources"]
    }
    assert {"requirement", "plan-rework-context"} <= source_ids
    required_ids = {
        str(item.get("source_id") or "")
        for item in payload["required_reads"]
    }
    assert {"requirement", "plan-rework-context"} <= required_ids


def test_generation_scoped_operation_key_per_trigger() -> None:
    """ZF-GEN-SCOPE-01:同触发重放同 id(replay 保持),新触发新代。

    07-17 四次实弹同墙:scan 重发/discovery 重发/final 重发/replan
    task_map 重派 — 同 child 键撞已注册 op → divergence 拒绝。
    键掺 trigger_event_id 后,retrigger 天然新代,replay 语义不变。
    """
    key_a1 = "dev-lane-0-T1@trig:evt-aaaa0000"
    key_a2 = "dev-lane-0-T1@trig:evt-aaaa0000"
    key_b = "dev-lane-0-T1@trig:evt-bbbb1111"
    from zf.runtime.workflow_operation import stable_operation_id

    id_a1 = stable_operation_id(
        workflow_run_id="run-1", parent_stage_id="impl",
        operation_key=key_a1, operation_type="fanout_writer_child",
    )
    id_a2 = stable_operation_id(
        workflow_run_id="run-1", parent_stage_id="impl",
        operation_key=key_a2, operation_type="fanout_writer_child",
    )
    id_b = stable_operation_id(
        workflow_run_id="run-1", parent_stage_id="impl",
        operation_key=key_b, operation_type="fanout_writer_child",
    )
    assert id_a1 == id_a2  # 同触发重放 → replay
    assert id_a1 != id_b   # 新触发 → 新代
