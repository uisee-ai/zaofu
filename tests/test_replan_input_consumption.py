"""Bounded replan inputs and truthful reader failure admission."""

import json
from pathlib import Path

import pytest

from tests.test_result_submit import _runtime
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.artifact_read_ledger import (
    materialize_attempt_source_ref, read_attempt_artifact, active_read_ledger_path,
    canonical_required_reads,
)
from zf.runtime.call_result_runtime import prepare_call_operation, mark_call_operation_started
from zf.runtime.plan_synth_handoff import build_plan_handoff_input_refs
from zf.runtime.result_submit import (
    SemanticResultSubmitService, ResultSubmitError, provision_role_submit_credential,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def test_scoped_replan_retains_all_contracts_without_requiring_unaffected_bodies(tmp_path):
    state = tmp_path / ".state"
    state.mkdir()
    store = TaskStore(state / "kanban.json")
    for index in range(93):
        store.add(Task(
            id=f"T{index:03}", title=f"Slice {index}", status="backlog",
            blocked_by=[f"T{index - 1:03}"] if index else [],
            contract=TaskContract(
                feature_id="ROOT", contract_revision=f"revision-{index}",
                acceptance_criteria=[f"AC-{index}: " + "evidence " * 1000],
            ),
        ))
    refs = build_plan_handoff_input_refs(
        state_dir=state, project_root=tmp_path,
        payload={"pdd_id": "ROOT", "rework_of": "failure-1",
                 "failed_task_ids": ["T000"], "task_ids": ["T002"]},
    )
    sources = {ref["source_id"]: ref for ref in refs}
    context = hydrate_sidecar_ref(state, sources["plan-rework-context"]).payload
    assert len(json.dumps(context, ensure_ascii=False)) < 100_000
    snapshot = context["canonical_task_snapshot"]
    full = hydrate_sidecar_ref(state, snapshot["full_snapshot_ref"]).payload
    assert len(full["tasks"]) == len(snapshot["tasks"]) == 93
    assert {t["task_id"] for t in snapshot["scoped_tasks"]} == {"T000", "T002"}
    assert full["tasks"][92]["contract"]["acceptance_criteria"][0].startswith("AC-92:")
    assert snapshot["tasks"][92]["blocked_by"] == ["T091"]
    assert snapshot["tasks"][92]["contract"]["contract_revision"] == "revision-92"
    assert sources["canonical-task-snapshot"]["sha256"] == snapshot["full_snapshot_ref"]["sha256"]
    required = canonical_required_reads(
        {"sources": refs, "handoff_authority_contract": {"attempt_domain": "plan"}},
        output_profile_id="workflow-read",
    )
    assert {row["source_id"] for row in required} == {"plan-rework-context"}
    unscoped = build_plan_handoff_input_refs(
        state_dir=state, project_root=tmp_path,
        payload={"pdd_id": "ROOT", "rework_of": "unscoped-failure"},
    )
    unscoped_body = hydrate_sidecar_ref(state, unscoped[0]).payload
    assert unscoped_body["canonical_task_snapshot"]["tasks"] == full["tasks"]


def _reader(tmp_path: Path, *, partial_read: bool = False):
    runtime = _runtime(tmp_path)
    token = provision_role_submit_credential(runtime.state_dir, "planner").read_text().strip()
    source = runtime.project_root / "required.json"
    source.write_text('{"input":"must be consumed for a conclusion"}')
    ref = materialize_attempt_source_ref(
        state_dir=runtime.state_dir, project_root=runtime.project_root,
        ref=str(source), source_id="plan-rework-context",
    )
    prepared = prepare_call_operation(
        runtime, payload={
            "workflow_run_id": "run-1", "role_instance": "planner",
            "run_id": "reader-1", "stage_id": "prd-plan", "child_id": "planner",
            "fanout_id": "fanout-1", "attempt_domain": "plan", "input_refs": [ref],
            "canonical_success_event": "prd.plan.child.completed",
            "canonical_failure_event": "prd.plan.child.failed",
        }, operation_type="fanout_reader_child", operation_key="planner",
        stage_id="prd-plan", task_id="", dispatch_id="reader-1",
    )
    mark_call_operation_started(runtime, prepared, task_id="", dispatch_id="reader-1")
    if partial_read:
        read_attempt_artifact(
            runtime.state_dir,
            manifest={"attempt_id": "reader-1", "sources": [ref]},
            source_id=ref["source_id"], artifact_id=ref["artifact_id"],
            page_start=0, page_size=5,
        )
    service = SemanticResultSubmitService(
        state_dir=runtime.state_dir, event_log=runtime.event_log, event_writer=runtime.event_writer,
    )
    return runtime, prepared, service, token


@pytest.mark.parametrize("partial_read", [False, True])
def test_reader_execution_failure_admits_incomplete_reads_without_pass(tmp_path, partial_read):
    runtime, prepared, service, token = _reader(tmp_path, partial_read=partial_read)
    submitted = service.submit(
        operation_id=prepared.operation_id, role_instance="planner", credential=token,
        semantic_result={"execution_status": "failed", "status": "failed",
                         "recommendation": "needs_rework", "summary": "Input cannot be fully consumed.",
                         "findings": [{"severity": "high", "message": "Read budget exhausted."}]},
    )
    assert submitted.canonical_event_type == "prd.plan.child.failed"
    events = runtime.event_log.read_all()
    admitted = next(e for e in events if e.type == "workflow.call.result.admitted")
    envelope = hydrate_sidecar_ref(runtime.state_dir, admitted.payload["envelope_ref"]).payload
    assert envelope["execution"]["status"] == "failed"
    assert envelope["input_consumption"]["status"] == "incomplete"
    assert envelope["input_consumption"]["read_ledger_ref"]
    control = hydrate_sidecar_ref(runtime.state_dir, admitted.payload["control_result_ref"]).payload
    assert control["verdict"] == "abstained"
    assert not any(e.type == "prd.plan.child.completed" for e in events)


def test_reader_failure_does_not_admit_corrupt_read_ledger(tmp_path):
    runtime, prepared, service, token = _reader(tmp_path, partial_read=True)
    active_read_ledger_path(runtime.state_dir, "reader-1").write_text("corrupt ledger\n")
    with pytest.raises(ResultSubmitError, match="result was not admitted"):
        service.submit(
            operation_id=prepared.operation_id, role_instance="planner", credential=token,
            semantic_result={"execution_status": "failed", "status": "failed",
                             "summary": "Input unavailable.", "findings": []},
        )
    assert not any(e.type == "workflow.call.result.admitted" for e in runtime.event_log.read_all())


@pytest.mark.parametrize("verdict", ["approve", "reject"])
def test_completed_reader_still_requires_all_reads(tmp_path, verdict):
    runtime, prepared, service, token = _reader(tmp_path)
    with pytest.raises(ResultSubmitError, match="required_read_missing"):
        service.submit(
            operation_id=prepared.operation_id, role_instance="planner", credential=token,
            semantic_result={"execution_status": "completed", "recommendation": verdict,
                             "status": "passed" if verdict == "approve" else "failed",
                             "summary": "A semantic judgment needs all required inputs.", "findings": []},
        )
    assert not any(e.type == "workflow.call.result.admitted" for e in runtime.event_log.read_all())
