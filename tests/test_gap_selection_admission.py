"""Malformed reader gap selection returns to its producer before settlement."""
from copy import deepcopy

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_read_result import normalize_workflow_read_result
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from tests.test_call_result_admission import _runtime


SELECTION = {
    "schema_version": "task-plan-input-selection.v1",
    "implementation": ["task_execution_input"],
    "task-verify": ["task_execution_input"],
}


def reader_result(selection):
    evidence = {} if selection is None else {"plan_input_selection": selection}
    return ZfEvent(type="flow.discovery.child.failed", actor="flow-discovery", payload={
        "workflow_run_id": "run-1", "run_id": "attempt-1", "role_instance": "flow-discovery",
        "report": {"summary": "Independent UI gap; full acceptance remains blocked.",
            "status": "failed", "recommendation": "needs_rework", "findings": [],
            "gap_tasks": [{"task_id": "UI-GAP", "evidence_contract": evidence}]},
    })


def test_invalid_gap_selection_uses_same_operation_protocol_correction(tmp_path):
    admission, operations = _runtime(tmp_path)
    ensured = operations.ensure_operation(workflow_run_id="run-1", operation_id="reader-op",
        operation_type="agent", request={"prompt": "bounded gap recovery"})
    operation = {"workflow_run_id": "run-1", "operation_id": "reader-op",
                 "request_hash": ensured.request_hash, "output_profile_id": "workflow-read",
                 "output_profile_revision": "1"}
    wrong = deepcopy(SELECTION)
    wrong["task_verify"] = wrong.pop("task-verify")
    source = reader_result(wrong)
    outcome = admission.report_legacy_result(source, mode="blocking", operation=operation)
    assert outcome.repair_requested
    assert outcome.correction_dispatch_required
    assert any(issue["field"] == "control_result.gap_tasks[0].evidence_contract.plan_input_selection"
               and issue["code"] == "schema_invalid" for issue in outcome.issues)
    before = operations.event_log.read_all()
    assert not any(e.type in {"workflow.operation.settled", "workflow.call.result.admitted"}
                   or e.type.startswith("task.attempt.") for e in before)
    repair = next(e for e in before if e.type == "workflow.call.result.repair.requested")
    assert repair.payload["operation_id"] == "reader-op"
    assert repair.payload["semantic_attempt_incremented"] is False
    replay = admission.report_legacy_result(source, mode="blocking", operation=operation)
    assert not replay.correction_dispatch_required
    corrected = admission.report_legacy_result(reader_result(deepcopy(SELECTION)),
                                               mode="blocking", operation=operation)
    assert corrected.status == "admitted", corrected.issues
    body = hydrate_sidecar_ref(tmp_path, corrected.control_result_ref).payload
    assert body["verdict"] == "rejected"
    assert body["gap_tasks"][0]["evidence_contract"]["plan_input_selection"] == SELECTION


@pytest.mark.parametrize("selection", [None, SELECTION])
def test_optional_or_valid_gap_selection_preserves_semantic_result(selection):
    result, issues = normalize_workflow_read_result(reader_result(selection))
    assert issues == []
    assert result["verdict"] == "rejected"


@pytest.mark.parametrize("ports", [[], ["task_execution_input", "task_execution_input"],
                                  ["other"], [" task_execution_input"], [None], [{}]])
@pytest.mark.parametrize("stage", ["implementation", "task-verify"])
def test_gap_selection_invalid_stage_shape_is_reported(ports, stage):
    selection = deepcopy(SELECTION)
    selection[stage] = ports
    _, issues = normalize_workflow_read_result(reader_result(selection))
    assert issues and all(i["code"] == "schema_invalid" for i in issues)


def test_live_reader_submit_corrects_selection_without_new_semantic_operation(tmp_path):
    from zf.core.config.schema import RoleConfig
    from zf.runtime.call_result_repair_reconciliation import reconcile_call_result_repairs
    from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
    from zf.runtime.result_submit import ResultSubmitError
    from tests.test_call_result_repair_reconciliation import _capture
    from tests.test_result_submit import _running_workflow_read_operation

    runtime, prepared, service, token = _running_workflow_read_operation(tmp_path)
    runtime.config.roles = [RoleConfig(name="scan-verification", backend="codex")]
    wrong = deepcopy(SELECTION)
    wrong["task_verify"] = wrong.pop("task-verify")
    raw = reader_result(wrong).payload["report"]
    kwargs = dict(operation_id=prepared.operation_id, role_instance="scan-verification", credential=token)
    with pytest.raises(ResultSubmitError) as rejected:
        service.submit(**kwargs, semantic_result=raw)
    assert "task-verify" in str(rejected.value.details)
    assert not any(e.type in {"workflow.child.failed", "workflow.child.completed", "workflow.operation.settled"}
                   for e in runtime.event_log.read_all())
    sent = _capture(runtime)
    assert reconcile_call_result_repairs(runtime) == 0
    runtime.event_log.append(ZfEvent(type="codex.hook.stop", actor="scan-verification"))
    assert reconcile_call_result_repairs(runtime) == 1
    assert len(sent) == 1 and prepared.operation_id in sent[0] and "task-verify" in sent[0]
    raw["gap_tasks"][0]["evidence_contract"]["plan_input_selection"] = deepcopy(SELECTION)
    submitted = service.submit(**kwargs, semantic_result=raw)
    canonical = next(e for e in runtime.event_log.read_all() if e.id == submitted.canonical_event_id)
    report = hydrate_profiled_control_result_event(runtime.state_dir, canonical).payload["report"]
    assert report["verdict"] == "rejected"
    assert report["gap_tasks"][0]["evidence_contract"]["plan_input_selection"] == SELECTION
    assert sum(e.type == "workflow.operation.requested" for e in runtime.event_log.read_all()) == 1


def test_old_admitted_reader_result_is_not_reopened_after_upgrade(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from zf.runtime import task_map_required_ports
    from zf.runtime.call_result_correction import dispatch_call_result_correction
    from zf.runtime.call_result_repair_reconciliation import reconcile_call_result_repairs
    from zf.runtime.workflow_operation import load_workflow_operation

    admission, operations = _runtime(tmp_path)
    ensured = operations.ensure_operation(workflow_run_id="run-1", operation_id="reader-op",
        operation_type="agent", request={"prompt": "bounded gap recovery"})
    operation = {"workflow_run_id": "run-1", "operation_id": "reader-op",
                 "request_hash": ensured.request_hash, "output_profile_id": "workflow-read"}
    wrong = deepcopy(SELECTION)
    wrong["task_verify"] = wrong.pop("task-verify")
    event = reader_result(wrong)
    # Model the previous release, which did not inspect this nested declaration.
    with monkeypatch.context() as old:
        old.setattr(task_map_required_ports, "plan_input_selection_shape_errors", lambda *a, **kw: [])
        original = admission.report_legacy_result(event, mode="blocking", operation=operation)
    assert original.admitted
    original_body = (tmp_path / original.control_result_ref["ref"]).read_bytes()
    restarted, _ = _runtime(tmp_path)
    replay = restarted.report_legacy_result(event, mode="blocking", operation=operation)
    assert replay.repair_requested
    runtime = SimpleNamespace(state_dir=tmp_path, event_log=operations.event_log)
    assert not dispatch_call_result_correction(runtime, source_event=event, outcome=replay)
    assert reconcile_call_result_repairs(runtime) == 0
    assert load_workflow_operation(operations.event_log, "reader-op")["status"] == "settled"
    assert (tmp_path / original.control_result_ref["ref"]).read_bytes() == original_body
    assert sum(e.type == "workflow.call.result.admitted" for e in operations.event_log.read_all()) == 1
