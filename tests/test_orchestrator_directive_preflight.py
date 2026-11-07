"""OA protocol errors must be repairable before the operation settles."""
from pathlib import Path

import pytest

from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.result_submit import ResultSubmitError, SemanticResultSubmitService
from zf.runtime.workflow_operation import load_workflow_operation

from tests.test_orchestrator_agent_semantic_failure import _runtime, _semantic_request, _submit_rework


@pytest.mark.parametrize("field", ["basis_refs", "reuse_refs", "invalidate_refs"])
@pytest.mark.parametrize("wrong_digest", [False, True])
def test_invalid_directive_can_be_repaired_in_same_operation(
    tmp_path: Path, monkeypatch, field: str, wrong_digest: bool,
) -> None:
    runtime = _runtime(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _semantic_request(runtime))
    captured = {}
    original = SemanticResultSubmitService.submit

    def with_invalid_ref(service, **kwargs):
        directive = kwargs["semantic_result"]["delta"]["directives"][0]
        source = prepared.context.source_manifest["sources"][0]
        directive[field] = [{
            "ref": source["ref"] if wrong_digest else "artifacts/unbound-child.json",
            "sha256": "f" * 64,
        }]
        captured.update(kwargs)
        return original(service, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(SemanticResultSubmitService, "submit", with_invalid_ref)
        with pytest.raises(ResultSubmitError, match="directive_ref_outside_operation") as rejected:
            _submit_rework(runtime, prepared)
    assert rejected.value.details["status"] == "repair_required"
    assert rejected.value.details["semantic_rework_cost"] == 0
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "running"
    assert not any(e.type in {"workflow.operation.settled", "orchestrator.semantic.decision.submitted"}
                   for e in runtime.event_log.read_all())

    service = SemanticResultSubmitService(state_dir=runtime.state_dir, event_log=runtime.event_log,
                                          event_writer=runtime.event_writer)
    before = len(runtime.event_log.read_all())
    validation = service.validate_plan_candidate(**captured)
    assert validation["status"] == "failed"
    assert validation["errors"][0]["code"] == "directive_ref_outside_operation"
    assert len(runtime.event_log.read_all()) == before
    source = prepared.context.source_manifest["sources"][0]
    captured["semantic_result"]["delta"]["directives"][0][field] = [
        {"ref": source["ref"], "sha256": source["sha256"]}
    ]
    assert service.validate_plan_candidate(**captured)["status"] == "passed"
    admitted = service.submit(**captured)
    assert admitted.admitted_event_id
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "settled"
    with pytest.raises(ResultSubmitError, match="already consumed"):
        service.submit(**captured)
