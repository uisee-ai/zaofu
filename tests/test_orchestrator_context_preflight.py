"""Frozen context references must fail before an OA result consumes its lease."""
from pathlib import Path

import pytest

from zf.runtime.result_submit import ResultSubmitError, SemanticResultSubmitService
from zf.runtime.workflow_operation import load_workflow_operation

from tests.test_orchestrator_agent_run_plan import (
    _checkpoint, _decision, _revised_candidate, _runtime, _submit,
)


@pytest.mark.parametrize("mutation", ["unknown_ref", "wrong_digest", "prior_plan"])
def test_context_reference_can_be_repaired_before_settlement(
    tmp_path: Path, monkeypatch, mutation: str,
) -> None:
    runtime, trigger, loaded, package, package_ref = _runtime(tmp_path)
    _, old = _checkpoint(runtime, trigger, loaded)
    old_source = next(s for s in old.context.source_manifest["sources"]
                      if s["source_id"] == "plan-port-task_map")
    trigger, loaded, _, _ = _revised_candidate(
        runtime, prior_package=package, prior_ref=package_ref,
        revision="2", generation="generation-2",
    )
    _, prepared = _checkpoint(runtime, trigger, loaded)
    decision = _decision(prepared)
    current = next(s for s in prepared.context.source_manifest["sources"]
                   if s["source_id"] == "plan-port-task_map")
    descriptor = {"ref": current["ref"], "sha256": current["sha256"]}
    if mutation == "unknown_ref":
        descriptor["ref"] = "artifacts/unbound-child.json"
    elif mutation == "wrong_digest":
        descriptor["sha256"] = "f" * 64
    else:
        descriptor = {"ref": old_source["ref"], "sha256": old_source["sha256"]}
    decision["run_plan"]["context_routes"][0]["required_sources"] = [descriptor]
    captured = {}
    original = SemanticResultSubmitService.submit

    def capture(service, **kwargs):
        captured.update(kwargs)
        return original(service, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(SemanticResultSubmitService, "submit", capture)
        with pytest.raises(ResultSubmitError, match="context route source is outside") as rejected:
            _submit(runtime, prepared, decision)
    assert rejected.value.details["status"] == "repair_required"
    assert rejected.value.details["semantic_rework_cost"] == 0
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "running"
    assert not any(e.type in {"workflow.operation.settled", "orchestrator.semantic.decision.submitted"}
                   for e in runtime.event_log.read_all())

    service = SemanticResultSubmitService(state_dir=runtime.state_dir, event_log=runtime.event_log,
                                          event_writer=runtime.event_writer)
    before = runtime.event_log.read_all()
    validation = service.validate_plan_candidate(**captured)
    assert validation["status"] == "failed"
    error = validation["errors"][0]
    assert error["code"] == "context_ref_outside_operation"
    assert error["work_unit_id"] == "discovery"
    assert "same scratch" in error["required_change"]
    assert runtime.event_log.read_all() == before
    decision["run_plan"]["context_routes"][0]["required_sources"] = [
        {"ref": current["ref"], "sha256": current["sha256"]}
    ]
    assert service.validate_plan_candidate(**captured)["status"] == "passed"
    admitted = service.submit(**captured)
    assert admitted.admitted_event_id
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "settled"
    with pytest.raises(ResultSubmitError, match="already consumed"):
        service.submit(**captured)
