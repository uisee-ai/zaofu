"""One bounded evidence Reader owns a supplemental operation after a mixed OA decision."""

from types import SimpleNamespace

import pytest

from tests.test_candidate_host_consumption import evidence, prepare
from zf.core.events import ZfEvent
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.call_result_runtime import mark_call_operation_started
from zf.runtime.candidate_reader_reassessment import (
    REQUESTED,
    dispatch_pending_reassessments,
    reader_reassessment_state,
    request_reader_reassessment,
)
from zf.runtime.result_submit import (
    ResultSubmitError,
    SemanticResultSubmitService,
    provision_role_submit_credential,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_host_manifest import binding as host_binding
from zf.runtime.workflow_operation import reduce_workflow_operations


def setup_scope(evidence, monkeypatch, *, frozen=True):
    runtime, seeded, _, _, _, receipt = evidence
    runtime.config.workflow.orchestration = SimpleNamespace(
        autonomous_actions=["reassess_reader_evidence"] if frozen else []
    )
    role = "reader-1"
    prepared, original_payload = prepare(runtime, seeded, role)
    original = reduce_workflow_operations(runtime.event_log.read_all())[
        prepared.operation_id
    ]
    original_request = hydrate_sidecar_ref(
        runtime.state_dir, original["request_ref"]
    ).payload["request"]
    run = seeded["run_id"]
    runtime.event_writer.emit(
        "run.goal.started", actor="zf-runtime", payload={"workflow_run_id": run}
    )
    finding = {
        "finding_id": "missing-evidence",
        "operation_id": prepared.operation_id,
        "control_result_ref": None,
        "child_event_id": "failed-reader",
        "pointer": "/outcome",
        "evidence": {"execution_status": "unknown"},
    }
    owner = {**finding, "finding_id": "protected-owner", "operation_id": "reader-2"}
    bound = {
        "candidate_event_id": "evt-candidate-ready",
        "source_commit": seeded["candidate_head"],
        "task_map_generation": seeded["generation"],
        "findings": [finding, owner],
    }
    # Aggregate lineage itself is exercised by test_candidate_verification_recovery.
    monkeypatch.setattr(
        "zf.runtime.candidate_failure_checkpoint.aggregate_candidate_binding",
        lambda *a, **kw: bound,
    )
    marker = runtime.event_writer.append(
        ZfEvent(
            type="orchestrator.replan_requested",
            origin="kernel",
            payload={
                **{
                    k: bound[k]
                    for k in (
                        "candidate_event_id",
                        "source_commit",
                        "task_map_generation",
                    )
                },
                "workflow_run_id": run,
                "pdd_id": "FLOW-ANCHOR",
                "candidate_failure_id": "failure",
                "classification": "oa_semantic_return_to_plan",
                "orchestrator_operation_id": "oa",
                "orchestrator_decision_ref": {"ref": "oa"},
                "rework_summary": {
                    "candidate_obligations": [
                        {
                            **finding,
                            "disposition": {"classification": "execution_unknown"},
                        },
                        {
                            **owner,
                            "disposition": {"classification": "contract_question"},
                        },
                    ]
                },
            },
        )
    )
    runtime.writer = runtime.event_writer
    return runtime, marker, original, original_request, original_payload, receipt


def test_no_retroactive_authorization_and_revocation(evidence, monkeypatch):
    runtime, marker, op, request, _, _ = setup_scope(
        evidence, monkeypatch, frozen=False
    )
    assert "candidate_reader_reassessment" not in request
    runtime.config.workflow.orchestration.autonomous_actions = [
        "reassess_reader_evidence"
    ]
    replay, _ = prepare(runtime, evidence[1], op["role_instance"])
    assert (
        replay.operation_id == op["operation_id"]
        and replay.request_hash == op["request_hash"]
    )
    with pytest.raises(ValueError, match="authorization"):
        request_reader_reassessment(
            runtime,
            marker=marker,
            operation_id=op["operation_id"],
            finding_ids=["missing-evidence"],
        )


def test_single_supplemental_reader_admits_exact_results_without_host_execution(
    evidence, monkeypatch
):
    runtime, marker, old, req, _, receipt = setup_scope(evidence, monkeypatch)
    assert req["candidate_reader_reassessment"] is True
    token = (
        provision_role_submit_credential(runtime.state_dir, role_instance="reader-1")
        .read_text()
        .strip()
    )
    runtime.all_roles = lambda: [
        SimpleNamespace(
            instance_id="reader-1", name="verify", role_kind="reader", skills=[]
        )
    ]
    runtime._ensure_fanout_role_dispatchable = lambda **kw: True
    runtime._record_skill_provenance = lambda **kw: []
    dispatched = []

    def deliver(**kw):
        prepared = kw["prepared_dispatch"]["prepared_call"]
        mark_call_operation_started(
            runtime,
            prepared,
            task_id="FLOW-ANCHOR",
            dispatch_id=prepared.attempt_id,
            causation_id=kw["causation_id"],
        )
        dispatched.append((prepared, kw["child"].payload, kw["role"].instance_id))

    runtime._dispatch_reader_fanout_child = deliver
    scope = {
        "marker": marker,
        "operation_id": old["operation_id"],
        "finding_ids": ["missing-evidence"],
    }
    runtime.config.workflow.orchestration.autonomous_actions = []
    with pytest.raises(ValueError, match="authorization"):
        request_reader_reassessment(runtime, **scope)
    runtime.config.workflow.orchestration.autonomous_actions = [
        "reassess_reader_evidence"
    ]
    with pytest.raises(ValueError, match="finding"):
        request_reader_reassessment(
            runtime, **{**scope, "finding_ids": ["protected-owner"]}
        )
    requested = request_reader_reassessment(runtime, **scope)
    assert requested.id == request_reader_reassessment(runtime, **scope).id
    dispatch_pending_reassessments(runtime)
    dispatch_pending_reassessments(runtime)
    assert [role for _, _, role in dispatched] == ["reader-1"]
    prepared, payload, _ = dispatched[0]
    assert (
        prepared.output_profile_id == "workflow-read"
        and prepared.operation_id != old["operation_id"]
    )
    assert (
        reader_reassessment_state(
            runtime.state_dir,
            runtime.event_log.read_all(),
            request_event_id=requested.id,
        )["status"]
        == "running"
    )
    with pytest.raises(ValueError, match="host assistance"):
        host_binding(runtime, prepared.operation_id)
    rows = [
        {
            "finding_id": "missing-evidence",
            "outcome": "supported",
            "receipt_ids": [receipt.id],
            "explanation": "Frozen receipt records the scoped result.",
        }
    ]
    report = {
        "status": "passed",
        "recommendation": "approve",
        "summary": "Accounted for the bounded finding",
        "findings": [],
        "finding_results": rows,
    }
    svc = SemanticResultSubmitService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    for row in payload["required_reads"]:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=hydrate_sidecar_ref(
                runtime.state_dir, payload["attempt_source_manifest"]
            ).payload,
            role="reader-1",
            actor="reader-1",
            source_id=row["source_id"],
            artifact_id=row["artifact_id"],
            json_path=row["json_path"],
        )
    kwargs = dict(
        operation_id=prepared.operation_id, role_instance="reader-1", credential=token
    )
    with pytest.raises(ResultSubmitError, match="receipt"):
        svc.submit(
            **kwargs,
            semantic_result={
                **report,
                "finding_results": [{**rows[0], "receipt_ids": ["other-run"]}],
            },
        )
    with pytest.raises(ResultSubmitError, match="candidate-host-original-receipt"):
        svc.submit(**kwargs, semantic_result=report)
    manifest = hydrate_sidecar_ref(
        runtime.state_dir, payload["attempt_source_manifest"]
    ).payload
    source = next(
        s
        for s in manifest["sources"]
        if s["source_id"] == "candidate-host-original-receipt-" + receipt.id
    )
    read_attempt_artifact(
        runtime.state_dir,
        manifest=manifest,
        actor="reader-1",
        role="reader-1",
        source_id=source["source_id"],
        artifact_id=source["artifact_id"],
        json_path="$",
    )
    assert (
        svc.validate_plan_candidate(**kwargs, semantic_result=report)["status"]
        == "passed"
    )
    svc.submit(**kwargs, semantic_result=report)
    events = runtime.event_log.read_all()
    state = reader_reassessment_state(
        runtime.state_dir, events, request_event_id=requested.id
    )
    assert state["status"] == "completed" and state["finding_results"] == rows
    assert reduce_workflow_operations(events)[old["operation_id"]] == old
    assert sum(e.type == REQUESTED for e in events) == 1
    assert sum(e.type == "runtime.action.attempt.started" for e in events) == 1
    (runtime.state_dir / state["control_result_ref"]["ref"]).write_text("{}")
    assert (
        reader_reassessment_state(
            runtime.state_dir, events, request_event_id=requested.id
        )["status"]
        == "needs_owner"
    )


def test_concurrency_reader_can_cite_only_frozen_lifecycle_window_without_host_receipt(
    tmp_path, monkeypatch
):
    from tests.test_candidate_verification_authority import (
        _runtime,
        _seed_frozen_candidate,
    )
    from zf.runtime.candidate_reader_reassessment import validate_submission

    runtime = _runtime(tmp_path)
    runtime.config.workflow.flow_metadata["milestone_handoff"] = True
    run, gen, task = "run-candidate-authority", "generation-1", "TASK-1"
    identity = {
        "workflow_run_id": run,
        "task_id": task,
        "operation_id": "source-op",
        "request_hash": "a" * 64,
    }
    for kind, detail in [
        ("workflow.operation.started", {"active_attempt_id": "a", "dispatch_id": "d"}),
        (
            "task.pipeline.stage.dispatched",
            {
                "attempt_id": "a",
                "dispatch_id": "d",
                "stage_id": "impl",
                "task_pipeline_stage": "impl",
                "task_pipeline_entry_mode": "standard",
                "task_map_generation": gen,
                "plan_artifact_package_digest": "b" * 64,
            },
        ),
        ("workflow.operation.settled", {}),
    ]:
        runtime.event_log.append(
            ZfEvent(
                type=kind, origin="kernel", task_id=task, payload={**identity, **detail}
            )
        )
    seeded = _seed_frozen_candidate(runtime)
    runtime, marker, old, _, _, _ = setup_scope(
        (runtime, seeded, None, None, None, None), monkeypatch
    )
    event = request_reader_reassessment(
        runtime,
        marker=marker,
        operation_id=old["operation_id"],
        finding_ids=["missing-evidence"],
    )
    scope = hydrate_sidecar_ref(
        runtime.state_dir, event.payload["evidence_scope_ref"]
    ).payload
    assert not scope["receipt_refs"]
    role = SimpleNamespace(
        instance_id="reader-1", name="verify", role_kind="reader", skills=[]
    )
    runtime.all_roles = lambda: [role]
    runtime._ensure_fanout_role_dispatchable = lambda **kw: True
    runtime._record_skill_provenance = lambda **kw: []
    sends = []
    runtime._dispatch_reader_fanout_child = lambda **kw: sends.append(kw)
    dispatch_pending_reassessments(runtime)
    call = sends[0]["prepared_dispatch"]["prepared_call"]
    signed = hydrate_sidecar_ref(
        runtime.state_dir,
        reduce_workflow_operations(runtime.event_log.read_all())[call.operation_id][
            "request_ref"
        ],
    ).payload["request"]
    assert signed["semantic_result_submit_mode"] == "blocking"
    payload = sends[0]["child"].payload
    manifest = hydrate_sidecar_ref(
        runtime.state_dir, payload["attempt_source_manifest"]
    ).payload
    assert manifest["read_purpose"] == "candidate-verify"
    for row in payload["required_reads"]:
        read_attempt_artifact(
            runtime.state_dir,
            manifest=manifest,
            role=role.instance_id,
            actor=role.instance_id,
            source_id=row["source_id"],
            artifact_id=row["artifact_id"],
            json_path=row["json_path"],
        )
    (window,) = scope["lifecycle"]["windows"]
    rows = [
        {
            "finding_id": "missing-evidence",
            "outcome": "unknown",
            "explanation": "Only one stage has measured entries.",
            "lifecycle_window_ids": [window],
        }
    ]
    with pytest.raises(ValueError, match="milestone-task-lifecycles"):
        validate_submission(
            runtime.state_dir,
            signed,
            {"finding_results": rows},
            runtime.event_log.read_all(),
        )
    source = scope["lifecycle"]["source"]
    read_attempt_artifact(
        runtime.state_dir,
        manifest=manifest,
        role=role.instance_id,
        actor=role.instance_id,
        source_id=source["source_id"],
        artifact_id=source["artifact_id"],
        json_path="$.summary",
    )
    validate_submission(
        runtime.state_dir,
        signed,
        {"finding_results": rows},
        runtime.event_log.read_all(),
    )
    with pytest.raises(ValueError, match="lifecycle_window_ids"):
        validate_submission(
            runtime.state_dir,
            signed,
            {"finding_results": [{**rows[0], "lifecycle_window_ids": ["other-run"]}]},
            runtime.event_log.read_all(),
        )
    assert not any(
        e.type == "runtime.action.attempt.started" for e in runtime.event_log.read_all()
    )


def test_live_kernel_dispatches_one_reader_through_normal_briefing_and_transport(
    evidence, monkeypatch
):
    from tests.test_refactor_fanout_briefing import _RecordingTransport
    from zf.core.config.schema import (
        ProjectConfig,
        RoleConfig,
        WorkflowConfig,
        ZfConfig,
    )
    from zf.runtime.orchestrator import Orchestrator

    runtime, marker, old, _, _, _ = setup_scope(evidence, monkeypatch)
    selected = RoleConfig(name="reader-1", backend="mock", role_kind="reader")
    passed = RoleConfig(name="passed-sibling", backend="mock", role_kind="reader")
    config = ZfConfig(
        project=ProjectConfig(name="reassessment"),
        roles=[selected, passed],
        workflow=WorkflowConfig(),
    )
    config.workflow.orchestration = runtime.config.workflow.orchestration
    config.workflow.flow_metadata = runtime.config.workflow.flow_metadata
    config.workflow.orchestration.flow_policies = {}
    transport = _RecordingTransport()
    # All real paths below use this tmpfs fixture. Only the transport's provider is a stub.
    orch = Orchestrator(runtime.state_dir, config, transport)
    event = request_reader_reassessment(
        runtime,
        marker=marker,
        operation_id=old["operation_id"],
        finding_ids=["missing-evidence"],
    )
    orch.run_once(events=[event])
    dispatch_pending_reassessments(orch)
    assert [turn[0] for turn in transport.sent] == ["reader-1"], [
        (e.type, e.payload.get("reason")) for e in orch.event_log.read_all()[-12:]
    ]
    briefing = transport.sent[0][1].read_text()
    assert (
        "finding_results" in briefing
        and "result submit" in briefing
        and "reassessment.json" in briefing
    )
    assert (
        reader_reassessment_state(
            orch.state_dir, orch.event_log.read_all(), request_event_id=event.id
        )["status"]
        == "running"
    )
