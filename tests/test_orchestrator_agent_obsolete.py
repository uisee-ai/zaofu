"""A superseded result cannot leave OA permanently busy after restart."""
from dataclasses import replace

import pytest

from tests.test_orchestrator_agent_execution_review import _case
from tests.test_orchestrator_agent_plan_binding import _new_package, _review
from tests.test_orchestrator_agent_semantic_failure import _submit_rework
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_obsolete import reconcile_obsolete_checkpoint_operations
from zf.runtime.orchestrator_agent_operations import activate_orchestrator_agent_operation, workflow_operation_service
from zf.runtime.orchestrator_agent_semantic_failure import request_semantic_failure_checkpoint
from zf.runtime.orchestrator_agent_transport import dispatch_pending_orchestrator_checkpoint, orchestrator_checkpoint_busy
from zf.runtime.result_submit import ResultSubmitError
from zf.runtime.workflow_operation import load_workflow_operation


def _obsolete(tmp_path):
    runtime, _ = _case(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _review(runtime))
    _new_package(runtime)
    runtime.event_writer.append(ZfEvent(type="task.pipeline.generation.admitted", payload={
        "schema_version": "task-pipeline-generation.v1", "workflow_run_id": "run-semantic-1",
        "task_ids": ["TASK-1"], "task_map_generation": "g2",
    }))
    return runtime, prepared


def _status(runtime, prepared):
    return load_workflow_operation(runtime.event_log, prepared.operation_id)["status"]


def test_dispatch_retires_obsolete_queued_request_without_paid_turn(tmp_path):
    runtime, prepared = _obsolete(tmp_path)
    sent = []
    runtime._notify_orchestrator_agent = sent.append
    dispatch_pending_orchestrator_checkpoint(runtime)
    dispatch_pending_orchestrator_checkpoint(runtime)
    assert _status(runtime, prepared) == "superseded"
    assert not sent
    assert sum(e.type == "workflow.operation.superseded" for e in runtime.event_log.read_all()) == 1


def test_dispatch_replays_legacy_stale_result_and_releases_oa(tmp_path, monkeypatch):
    runtime, prepared = _obsolete(tmp_path)
    # Simulate the historical admission bug: result rejected, operation left running.
    with monkeypatch.context() as patch:
        patch.setattr("zf.runtime.orchestrator_agent_obsolete.reconcile_obsolete_checkpoint_operations", lambda *a, **k: None)
        with pytest.raises(ResultSubmitError, match="stale_plan_artifact_package"):
            _submit_rework(runtime, prepared)
    assert orchestrator_checkpoint_busy(runtime)
    runtime._notify_orchestrator_agent = lambda e: pytest.fail("obsolete request dispatched")
    dispatch_pending_orchestrator_checkpoint(runtime)
    dispatch_pending_orchestrator_checkpoint(runtime)
    assert _status(runtime, prepared) == "superseded"
    assert not orchestrator_checkpoint_busy(runtime)
    invalid = next(e for e in runtime.event_log.read_all() if e.type == "workflow.call.result.invalid")
    terminal = [e for e in runtime.event_log.read_all() if e.type == "workflow.operation.superseded"]
    assert len(terminal) == 1 and terminal[0].causation_id == invalid.id


def test_running_operation_without_result_keeps_exclusive_ownership(tmp_path):
    runtime, prepared = _obsolete(tmp_path)
    activate_orchestrator_agent_operation(runtime, prepared, dispatch_id="dispatch", causation_id="start")
    reconcile_obsolete_checkpoint_operations(workflow_operation_service(runtime))
    assert _status(runtime, prepared) == "running"
    assert orchestrator_checkpoint_busy(runtime)


def test_retained_task_package_is_current_despite_new_run_package(tmp_path):
    runtime, _ = _case(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _review(runtime))
    _new_package(runtime)
    reconcile_obsolete_checkpoint_operations(workflow_operation_service(runtime))
    assert _status(runtime, prepared) == "requested"


@pytest.mark.parametrize("mutation", ["run", "hash", "attempt", "external", "body", "request"])
def test_unbound_or_corrupt_evidence_cannot_release_active_operation(tmp_path, monkeypatch, mutation):
    runtime, prepared = _obsolete(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr("zf.runtime.orchestrator_agent_obsolete.reconcile_obsolete_checkpoint_operations", lambda *a, **k: None)
        with pytest.raises(ResultSubmitError):
            _submit_rework(runtime, prepared)
    events = runtime.event_log.read_all()
    invalid = next(e for e in events if e.type == "workflow.call.result.invalid")
    body = dict(invalid.payload)
    if mutation in {"run", "hash"}:
        body[{"run": "workflow_run_id", "hash": "request_hash"}[mutation]] = "other"
    elif mutation in {"attempt", "body"}:
        from zf.runtime.sidecar_refs import hydrate_sidecar_ref
        from zf.runtime.call_result_envelope import write_immutable_json_sidecar
        envelope = hydrate_sidecar_ref(runtime.state_dir, body["envelope_ref"]).payload
        envelope["identity"]["attempt_id"] = "other-attempt"
        body["envelope_ref"] = write_immutable_json_sidecar(runtime.state_dir, envelope,
            root="fixtures/envelopes", kind="call_result_envelope", schema_version="call-result-envelope.v1", created_by="test")
        if mutation == "body":
            body["envelope_ref"]["sha256"] = "0" * 64
    replacement = replace(invalid, payload=body, origin="external" if mutation == "external" else invalid.origin)
    monkeypatch.setattr(runtime.event_log, "read_all", lambda: [replacement if e.id == invalid.id else e for e in events])
    if mutation == "request":
        row = load_workflow_operation(runtime.event_log, prepared.operation_id)
        (runtime.state_dir / row["request_ref"]["ref"]).write_text("{}")
    reconcile_obsolete_checkpoint_operations(workflow_operation_service(runtime))
    assert _status(runtime, prepared) == "running"
