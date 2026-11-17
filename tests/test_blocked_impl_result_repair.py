"""An infrastructure blocker must not admit unfilled successful-result slots."""
from copy import deepcopy

import pytest

from tests.test_typed_result_admission import _descriptors, _implementation_result
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.call_result_admission import CallResultAdmissionService
from zf.runtime.typed_result_admission import typed_result_admission_issues


def _blocked(tmp_path):
    contract, descriptor, _ = _descriptors(tmp_path)
    result = _implementation_result(contract, descriptor)
    result.update(execution_status="failed", verdict="blocked",
                  changed_files=["src/name.ts", "tests/name.mjs"])
    return result


@pytest.mark.parametrize("location", ["target", "evidence", "self_target", "command", "acceptance"])
def test_blocked_impl_rejects_unfilled_result_slots(tmp_path, location):
    result = _blocked(tmp_path)
    if location == "target":
        result["target_commit"] = "<HEAD commit>"
    elif location == "evidence":
        result["evidence_refs"] = ["<implementation summary artifact or event ref>"]
    elif location == "self_target":
        result["self_check"]["target_commit"] = "<HEAD commit>"
    else:
        key = "command_receipts" if location == "command" else "acceptance_results"
        result["self_check"][key][0]["evidence_refs"] = ["<durable output or event ref>"]
    issues = typed_result_admission_issues(tmp_path, schema_version="implementation-result.v1",
                                         result=result, semantic_submit=True)
    assert issues
    assert issues[0]["code"] == "implementation_result_placeholder"
    assert issues[0]["recovery_owner"] == "implementation_owner"
    assert issues[0]["recovery_action"] == "result_repair"


def test_infrastructure_failure_can_report_unknown_target_and_no_self_checks(tmp_path):
    result = _blocked(tmp_path)
    result.update(target_commit="", changed_files=[], self_check={},
                  failure_class="reader_execution_failure", evidence_refs=["event:read-failed"])
    assert typed_result_admission_issues(tmp_path, schema_version="implementation-result.v1",
                                        result=result, semantic_submit=True) == []


def test_secure_blocked_submission_requests_correction_before_settling(tmp_path):
    valid = _blocked(tmp_path)
    invalid = deepcopy(valid)
    invalid.update(target_commit="<HEAD commit>", changed_files=[])
    log = EventLog(tmp_path / "events.jsonl")
    service = CallResultAdmissionService(state_dir=tmp_path, event_log=log, event_writer=EventWriter(log))
    operation = {"workflow_run_id": "run-1", "operation_id": "impl-1", "request_hash": "hash-1"}

    def submit(result):
        return service.report_legacy_result(ZfEvent(
            type="dev.blocked", actor="dev-1", task_id="TASK-1",
            payload={**operation, "attempt_id": "attempt-1", "implementation_result": result},
        ), mode="blocking", operation=operation, require_semantic_submit=True, semantic_submit=True)

    rejected = submit(invalid)
    assert not rejected.admitted
    assert rejected.repair_requested
    assert not any(e.type == "workflow.call.result.admitted" for e in log.read_all())
    repairs = [e for e in log.read_all() if e.type == "workflow.call.result.repair.requested"]
    assert len(repairs) == 1
    assert repairs[0].payload["semantic_attempt_incremented"] is False
    accepted = submit(valid)
    assert accepted.admitted
    assert not accepted.issues


def test_pipeline_submit_keeps_attempt_available_for_blocked_result_correction(tmp_path):
    from tests.e2e.test_replan_checkpoint_closure_mock_e2e import (
        _runtime, _map, _admit, _dispatches, _submit, _git,
    )
    from pathlib import Path
    from zf.runtime.result_submit import ResultSubmitError

    runtime = _runtime(tmp_path)
    _admit(runtime, _map(), "g1")
    dispatch = _dispatches(runtime)[0]
    head = _git(Path(dispatch.payload["workdir"]), "rev-parse", "HEAD")
    result = {"schema_version": "implementation-result.v1", "execution_status": "failed",
              "verdict": "blocked", "target_commit": "<HEAD commit>", "changed_files": [],
              "self_check": {}, "evidence_refs": ["mock://index-read-only"],
              "summary": "Git index is read-only; no successful publication or product verdict."}
    with pytest.raises(ResultSubmitError, match="implementation_result_placeholder"):
        _submit(runtime, dispatch, result)
    assert not any(e.type == "dev.blocked" for e in runtime.event_log.read_all())
    repairs = [e for e in runtime.event_log.read_all()
               if e.type == "workflow.call.result.repair.requested"]
    assert len(repairs) == 1 and not repairs[0].payload["semantic_attempt_incremented"]
    corrected = _submit(runtime, dispatch, {**result, "target_commit": head})
    assert corrected.type == "dev.blocked"
    assert corrected.payload["operation_id"] == dispatch.payload["operation_id"]
