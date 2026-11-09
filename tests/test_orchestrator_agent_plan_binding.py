"""OA request and decision admission must agree on retained Task authority."""

import pytest

from tests.test_orchestrator_agent_execution_review import _case, _pending
from tests.test_orchestrator_agent_semantic_failure import _submit_rework
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_agent_admission import admit_orchestrator_agent_decision
from zf.runtime.orchestrator_agent_plan_binding import current_checkpoint_plan_package
from zf.runtime.orchestrator_agent_semantic_failure import (
    SemanticFailureCheckpointError,
    request_semantic_failure_checkpoint,
)
from zf.runtime.run_manager import _execute_orchestrator_triage_request


def _review(runtime):
    action = _pending(runtime)[0]
    _execute_orchestrator_triage_request(
        writer=runtime.event_writer, action=action, causation_id=action["source_event_id"],
        state_dir=runtime.state_dir, config=runtime.config,
    )
    return next(e for e in runtime.event_log.read_all()
                if e.type == "orchestrator.semantic.failure.requested")


def _new_package(runtime):
    original = next(e.payload for e in runtime.event_log.read_all()
                    if e.type == "plan.artifact_package.admitted")
    runtime.event_writer.append(ZfEvent(type="plan.artifact_package.admitted", payload={
        **original, "plan_revision": "r2", "task_map_generation": "g2",
        "package_digest": "new-digest", "package_ref": "new-package.json",
        "package_id": "new-package",
    }))


def test_retained_task_review_and_result_use_its_admitted_package(tmp_path):
    runtime, _ = _case(tmp_path)
    request = _review(runtime)
    _new_package(runtime)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    assert prepared.context.input_body["identity"]["task_map_generation"] == "g1"
    admission = admit_orchestrator_agent_decision(runtime, _submit_rework(runtime, prepared))
    assert admission.admitted, admission.reason


def test_settled_task_without_active_dispatch_uses_operation_attempt(tmp_path):
    from zf.runtime.orchestrator_agent_decision_apply import apply_orchestrator_agent_decision

    runtime, _ = _case(tmp_path)
    runtime.task_store.update("TASK-1", active_dispatch_id=None)
    request = _review(runtime)
    prepared = request_semantic_failure_checkpoint(runtime, request)
    assert prepared.context.input_body["checkpoint_context"]["target_attempt_id"] == "lease-task-1"
    outcome = apply_orchestrator_agent_decision(runtime, _submit_rework(runtime, prepared))
    assert outcome["applied"], outcome


def test_task_readmission_invalidates_pending_decision(tmp_path):
    runtime, _ = _case(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _review(runtime))
    decision = _submit_rework(runtime, prepared)
    _new_package(runtime)
    runtime.event_writer.append(ZfEvent(type="task.pipeline.generation.admitted", payload={
        "schema_version": "task-pipeline-generation.v1", "workflow_run_id": "run-semantic-1",
        "task_ids": ["TASK-1"], "task_map_generation": "g2",
    }))
    admission = admit_orchestrator_agent_decision(runtime, decision)
    assert not admission.admitted
    assert admission.reason == "plan_package_stale"


def test_task_readmission_rejects_result_submission(tmp_path):
    from zf.runtime.result_submit import ResultSubmitError

    runtime, _ = _case(tmp_path)
    prepared = request_semantic_failure_checkpoint(runtime, _review(runtime))
    _new_package(runtime)
    runtime.event_writer.append(ZfEvent(type="task.pipeline.generation.admitted", payload={
        "schema_version": "task-pipeline-generation.v1", "workflow_run_id": "run-semantic-1",
        "task_ids": ["TASK-1"], "task_map_generation": "g2",
    }))
    with pytest.raises(ResultSubmitError, match="stale_plan_artifact_package"):
        _submit_rework(runtime, prepared)
    from zf.runtime.workflow_operation import load_workflow_operation
    assert load_workflow_operation(runtime.event_log, prepared.operation_id)["status"] == "superseded"


@pytest.mark.parametrize("mutation", [
    {"task_map_generation": "missing"},
    {"workflow_run_id": "other-run"},
    {"plan_artifact_package_digest": "wrong-digest"},
])
def test_task_binding_mismatch_never_falls_back_to_global_package(tmp_path, mutation):
    runtime, _ = _case(tmp_path)
    runtime.event_writer.append(ZfEvent(type="task.pipeline.generation.admitted", payload={
        "schema_version": "task-pipeline-generation.v1", "workflow_run_id": "run-semantic-1",
        "task_ids": ["TASK-1"], "task_map_generation": "g1", **mutation,
    }))
    assert current_checkpoint_plan_package(runtime.event_log.read_all(),
        workflow_run_id="run-semantic-1", task_id="TASK-1") == {}


def test_caller_cannot_select_a_superseded_task_generation(tmp_path):
    runtime, _ = _case(tmp_path)
    request = _review(runtime)
    request.payload["task_map_generation"] = "g2"
    _new_package(runtime)
    with pytest.raises(SemanticFailureCheckpointError, match="target_generation_stale"):
        request_semantic_failure_checkpoint(runtime, request)


def test_run_scope_still_uses_latest_package(tmp_path):
    runtime, _ = _case(tmp_path)
    _new_package(runtime)
    package = current_checkpoint_plan_package(runtime.event_log.read_all(),
        workflow_run_id="run-semantic-1")
    assert package["task_map_generation"] == "g2"
