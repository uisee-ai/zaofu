"""The exact frozen Planner execution abstention has a diagnostic owner before any product Task exists."""

from dataclasses import replace

import pytest

from tests.test_orchestrator_escalation_wire import legacy_config, state_dir, transport
from tests.test_workflow_registered_controller import assembly
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.reader_rebuild_admission import _settled_execution_failure


def test_missing_failed_attempt_keeps_the_original_owner_gate_even_with_reflection(
    state_dir,
    legacy_config,
    transport,
    monkeypatch,
):
    runtime = Orchestrator(state_dir, legacy_config, transport)
    legacy_config.runtime.run_manager.reflect.enabled = True
    failure = runtime.event_writer.append(
        ZfEvent(
            type="prd.plan.failed",
            origin="kernel",
            correlation_id="run-A",
            payload={
                "pdd_id": "anchor",
                "workflow_run_id": "run-A",
                "failure_class": "reader_execution_failure",
                "failure_kind": "infra",
                "stage_id": "prd-plan",
            },
        )
    )
    monkeypatch.setattr(
        "zf.runtime.stage_failure_replan.plan_reader_stage_replan",
        lambda *a, **kw: (None, "infrastructure_failure"),
    )
    decision = runtime._on_reader_stage_failure_replan(failure)
    assert decision.action == "escalate"
    assert runtime.event_log.query(type="human.escalate")


def test_owner_fallback_correlates_exact_failed_run_and_task(
    state_dir, legacy_config, transport, monkeypatch
):
    runtime = Orchestrator(state_dir, legacy_config, transport)
    failure = runtime.event_writer.append(
        ZfEvent(
            type="prd.plan.failed",
            origin="kernel",
            correlation_id="run-A",
            payload={
                "pdd_id": "anchor",
                "workflow_run_id": "run-A",
                "stage_id": "prd-plan",
            },
        )
    )
    monkeypatch.setattr(
        "zf.runtime.stage_failure_replan.plan_reader_stage_replan",
        lambda *a, **kw: (None, "infrastructure_failure"),
    )
    assert runtime._on_reader_stage_failure_replan(failure).action == "escalate"
    (escalation,) = runtime.event_log.query(type="human.escalate")
    assert (escalation.causation_id, escalation.correlation_id, escalation.task_id) == (
        failure.id,
        "run-A",
        "anchor",
    )
    assert (
        escalation.payload["workflow_run_id"] == "run-A"
        and escalation.payload["source_event_id"] == failure.id
    )


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "owner",
        "scan",
        "different_role",
        "verdict",
        "wrong_attempt",
        "foreign_run",
        "foreign_manifest",
    ],
)
def test_repair_consumer_accepts_only_exact_abstaining_plan_producer(tmp_path, fault):
    binding = {
        "operation_id": "op-7",
        "request_hash": "a" * 64,
        "workflow_run_id": "run-A",
        "task_id": "anchor",
        "role_instance": "plan-author",
        "attempt_domain": "plan",
        "stage_id": "prd-plan",
        "result_semantics": "artifact_production",
    }
    body = {
        **binding,
        "attempt_id": "attempt-7",
        "schema_version": "workflow-read-result.v1",
        "execution_status": "failed",
        "failure_class": "reader_execution_failure",
        "verdict": "abstained",
        "owner_decision_items": [],
    }
    if fault == "scan":
        body["attempt_domain"] = "scan"
    if fault == "owner":
        body["owner_decision_items"] = [
            {"question": "May I change the contract?", "blocking": True}
        ]
    if fault == "different_role":
        body["role_instance"] = "sibling"
    if fault == "wrong_attempt":
        body["attempt_id"] = "attempt-8"
    if fault == "verdict":
        body["verdict"] = "rejected"
    if fault == "foreign_run":
        body["workflow_run_id"] = "run-B"
    ref = write_immutable_json_sidecar(
        tmp_path,
        body,
        root="test-results",
        kind="call_control_result",
        schema_version="workflow-read-result.v1",
        created_by="kernel",
    )
    request = write_immutable_json_sidecar(
        tmp_path,
        {
            "request": {
                **binding,
                "output_profile_id": "workflow-read",
                "result_identity": binding,
            }
        },
        root="test-requests",
        kind="call_request",
        schema_version="call-request.v1",
        created_by="kernel",
    )
    op = {
        **binding,
        "status": "settled",
        "operation_type": "fanout_reader_child",
        "semantic_verdict": "abstained",
        "active_attempt_id": "attempt-7",
        "admitted_control_result_ref": ref,
        "request_ref": request,
    }
    failed = ZfEvent(
        type="fanout.child.failed",
        origin="kernel",
        payload={
            **binding,
            "semantic_verdict": "abstained",
            "control_result_schema": "workflow-read-result.v1",
            "control_result_ref": ref,
            "failure_class": "reader_execution_failure",
        },
    )
    failed.payload.pop(
        "attempt_domain"
    )  # The derived fanout event carries role and stage; the WRC owns domain.
    admitted = ZfEvent(
        type="workflow.call.result.admitted",
        origin="kernel",
        payload={**binding, "semantic_verdict": "abstained", "control_result_ref": ref},
    )
    if fault == "foreign_manifest":
        admitted = replace(
            admitted, payload={**admitted.payload, "control_result_ref": {}}
        )
    assert _settled_execution_failure(tmp_path, [admitted, failed], op, failed) == (
        fault is None
    )


def test_real_plan_reader_reflection_binds_its_method_to_the_successor(
    tmp_path, assembly
):
    import json
    from tests.test_plan_team_inputs import _team, submit_plan_expert
    from zf.core.task.schema import Task
    from tests.test_run_manager_reflection import _wait
    from zf.autoresearch.loop_reflect import parse_reflection_response
    from zf.runtime.artifact_input_index import load_attempt_source_manifest
    from zf.runtime.diagnosis_reader_rebuild import diagnosis_reader_rebuild
    from zf.runtime.result_submit import (
        SemanticResultSubmitService,
        provision_role_submit_credential,
    )
    from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref

    root, state, log, drive, runtime = _team(
        assembly, tmp_path, {"product-scan": "prd.md#auth"}
    )
    config = runtime.config
    config.runtime.run_manager.reflect.enabled = True
    root.joinpath("prd.md").write_text("# Auth\nA single command.\n")
    for stage in config.workflow.stages:
        for child in stage.children:
            child.payload = {**(child.payload or {}), "task_id": "ANCHOR"}
    run = "run-with-bounded-plan"
    runtime.task_store.add(
        Task(id="ANCHOR", title="Single command", status="in_progress")
    )
    runtime.event_writer.emit("run.started", payload={"workflow_run_id": run})
    entry = runtime.event_writer.emit(
        "prd.requested",
        task_id="ANCHOR",
        correlation_id=run,
        payload={
            "workflow_run_id": run,
            "flow_kind": "prd",
            "task_id": "ANCHOR",
            "pdd_id": "ANCHOR",
            "prd_ref": "prd.md",
        },
    )
    runtime.run_once(events=[entry])
    for dispatch in log.query(type="fanout.child.dispatched"):
        role = dispatch.payload["role_instance"]
        submit_plan_expert(
            runtime,
            dispatch,
            {
                "child_id": role,
                "status": "passed",
                "summary": "Scoped evidence",
                "findings": [],
                "recommendation": "approve",
            },
        )
    (completed,) = log.query(type="prd.scan.completed")
    runtime.run_once(events=[completed])
    issued = log.query(type="fanout.child.dispatched")[-1]
    assert issued.payload["role_instance"] == "planner"
    original = issued.payload["operation_id"]
    token = provision_role_submit_credential(state, "planner").read_text().strip()
    service = SemanticResultSubmitService(
        state_dir=state, event_log=log, event_writer=runtime.event_writer
    )
    ret = service.submit(
        operation_id=original,
        role_instance="planner",
        credential=token,
        semantic_result={
            "status": "failed",
            "execution_status": "failed",
            "verdict": "abstained",
            "failure_class": "reader_execution_failure",
            "summary": "The producer could not represent a single Task.",
            "findings": [
                {
                    "code": "single_task_conflict",
                    "severity": "blocking",
                    "message": "I cannot express this requirement.",
                    "evidence_refs": ["prd.md"],
                }
            ],
            "evidence_refs": ["prd.md"],
            "recommendation": "abstain",
        },
    )
    notice = next(e for e in log.read_all() if e.id == ret.canonical_event_id)
    runtime.run_once(events=[notice])
    (agg,) = log.query(type="prd.plan.failed")
    runtime.run_once(events=[agg])
    assert not log.query(type="human.escalate")
    method = "Validate the proposed single Task against the applicable contract."
    seen_prompts = []

    def diagnose(prompt, **_):
        seen_prompts.append(prompt)
        option = json.loads(prompt.splitlines()[-1])
        assert option["dispatch_event_id"] == issued.id
        return parse_reflection_response(
            json.dumps(
                {
                    "verdict": "best_so_far",
                    "risk": "low",
                    "rec_for_next_iter": method,
                    "selected_reader_recovery": option["option_id"],
                }
            )
        )

    assert next(
        a
        for a in build_run_manager_projection(
            state, events=log.read_all(), config=config
        )["pending_actions"]
        if agg.id in a.get("source_event_ids", [])
    )
    # Dispatch through the regular Run Manager diagnosis path, without a resident Agent.
    for _ in range(2):
        run_manager_tick(
            state_dir=state,
            writer=runtime.event_writer,
            event_log=log,
            config=config,
            project_root=runtime.project_root,
            spawn_repairs=False,
            reflect_fn=diagnose,
        )
        if log.query(type="run.manager.reflect.requested"):
            break
    _wait(lambda: bool(log.query(type="run.manager.reflect.completed")))
    run_manager_tick(
        state_dir=state,
        writer=runtime.event_writer,
        event_log=log,
        config=config,
        project_root=runtime.project_root,
        spawn_repairs=False,
        reflect_fn=diagnose,
    )
    assert any("single_task_conflict" in prompt and "I cannot express this requirement." in prompt
               for prompt in seen_prompts)
    reflection = log.query(type="run.manager.reflect.requested")[0]
    context = hydrate_sidecar_ref(state, reflection.payload["context_ref"]).payload
    read_set = hydrate_sidecar_ref(state, reflection.payload["read_set_ref"]).payload
    evidence = context["worker_failure_evidence"]
    assert evidence["source_status"] == "admitted_worker_report"
    assert evidence["preflight"]["binding"] == "passed"
    assert evidence["preflight"]["product_validation"] == "not_established"
    assert evidence["failure_class"] == "reader_execution_failure"
    assert evidence["summary"] == "The producer could not represent a single Task."
    assert evidence["findings"][0]["code"] == "single_task_conflict"
    assert evidence["original_result"] == read_set["source_refs"]["worker_failure_result"]
    assert evidence["admission_event_id"] in read_set["source_event_ids"]
    assert ret.canonical_event_id != evidence["admission_event_id"]
    from zf.runtime.reader_failure_evidence import worker_failure_evidence

    option = reflection.payload["reader_recovery_option"]
    events = log.read_all()
    for key, other in (("attempt_id", "unrelated"), ("workflow_run_id", "another-run"),
                       ("control_result_ref", {}), ("semantic_verdict", "passed")):
        stale = [replace(e, payload={**e.payload, key: other}) if e.id == evidence["admission_event_id"] else e
                 for e in events]
        assert not worker_failure_evidence(state, stale, option)
    assert not worker_failure_evidence(state, events, {**option, "human_gate_status": "blocked"})
    (intent,) = log.query(type="repair.action.requested")
    runtime.run_once(events=[intent])
    runtime.run_once(events=[intent])
    next_call = log.query(type="fanout.child.dispatched")[-1].payload
    assert next_call["payload"]["retry_of_operation_id"] == original
    assert next_call["role_instance"] == "planner"
    manifest = load_attempt_source_manifest(state, next_call["payload"]["attempt_id"])
    (note,) = [
        s for s in manifest["sources"] if s["source_id"].startswith("plan-recovery-")
    ]
    assert hydrate_sidecar_ref(state, note).payload["recommendation"] == method
    assert any(
        r["source_id"] == note["source_id"]
        for r in next_call["payload"]["required_reads"]
    )
    assert len(log.query(type="repair.action.applied")) == 1
    # A second unresolved diagnosis cannot exceed the Run's Plan revision allowance.
    config.workflow.orchestration.max_plan_revisions = 1
    repeated = service.submit(
        operation_id=next_call["operation_id"],
        role_instance="planner",
        credential=token,
        semantic_result={
            "status": "failed",
            "execution_status": "failed",
            "verdict": "abstained",
            "failure_class": "reader_execution_failure",
            "summary": "Still unable to deliver.",
            "evidence_refs": ["prd.md"],
            "recommendation": "abstain",
        },
    )
    repeated_notice = next(
        e for e in log.read_all() if e.id == repeated.canonical_event_id
    )
    runtime.run_once(events=[repeated_notice])
    latest = log.query(type="fanout.child.failed")[-1]
    card = diagnosis_reader_rebuild(
        state,
        log.read_all(),
        {
            "request_kind": "recovery_diagnosis",
            "workflow_run_id": run,
            "source_event_ids": [latest.id],
        },
        config=config,
    )
    assert (
        card["binding_preflight"] == "blocked"
        and "Plan revision limit" in card["reason"]
    )
    assert "recommendation" not in card


def test_worker_diagnosis_excerpt_redacts_before_truncating():
    from zf.runtime.reader_failure_evidence import _excerpt

    assert _excerpt("OPENAI_API_KEY=sk-" + "z" * 50, 36)== "OPENAI_API_KEY=[REDACTED_SECRET]"
    assert len(_excerpt("a" * 2000, 80)) == 80
    assert _excerpt("a" * 2000, 80).endswith("…")
