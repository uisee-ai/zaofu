"""An unsent retry retains the recovery context frozen with its operation."""
from copy import deepcopy

import pytest

from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.call_result_runtime import prepare_call_operation
from zf.runtime.task_contract_snapshot import (
    build_task_contract_snapshot, write_task_contract_snapshot, current_task_contract_identity,
)
from zf.runtime.workflow_operation import WorkflowOperationError
from tests.test_call_result_runtime import _runtime


def setup_retry(tmp_path):
    runtime = _runtime(tmp_path)
    task = Task(id="T1", title="Retry", status="in_progress", contract=TaskContract(
        acceptance_criteria=["AC1"], verification="node --test", scope=["app/"],
        evidence_contract={"task_map_generation": "G1"}))
    TaskStore(runtime.state_dir / "kanban.json").add(task)
    ref = write_task_contract_snapshot(runtime.state_dir, build_task_contract_snapshot(
        task, workflow_run_id="run", task_map_generation_id="G1", base_commit="base", task_ref="task/T1"))
    payload = {**current_task_contract_identity(task), "workflow_run_id": "run", "task_id": "T1",
        "stage_id": "impl", "task_pipeline_stage": "impl", "operation_generation": 2,
        "operation_id": "op-retry", "output_profile_id": "implementation", "base_commit": "base",
        "task_ref": "task/T1", "task_map_generation": "G1", "role_instance": "dev",
        "contract_snapshot_ref": ref["ref"], "contract_snapshot_digest": ref["sha256"],
        "rework_feedback": [{"message": "Commit was published; verify that exact commit.",
                             "source_event_id": "publication"}]}
    def prepare(body, attempt):
        return prepare_call_operation(runtime, payload=body, operation_type="task-stage",
            operation_key=f"retry:{body['operation_generation']}", stage_id="impl", task_id="T1", dispatch_id=attempt,
            scheduler_attempt_id=attempt, causation_id="generation")
    first = prepare(deepcopy(payload), "a1")
    payload.pop("rework_feedback")
    return runtime, payload, first, prepare


def test_unsent_task_retry_keeps_frozen_required_repair_context(tmp_path):
    runtime, payload, first, prepare = setup_retry(tmp_path)
    replay = prepare(payload, "a2")
    assert replay.request_hash == first.request_hash
    assert replay.should_dispatch
    assert not any(e.type == "workflow.operation.blocked" for e in runtime.event_log.read_all())


def test_new_operation_does_not_inherit_old_repair_context(tmp_path):
    runtime, payload, first, prepare = setup_retry(tmp_path)
    payload["operation_id"] = "op-other"
    payload["operation_generation"] = 3
    replay = prepare(payload, "a2")
    assert replay.operation_id != first.operation_id
    import json
    manifest = json.loads((runtime.state_dir / payload["attempt_source_manifest_ref"]).read_text())
    assert not any(r["source_id"] == "critical-reader-repair-context" for r in manifest["sources"])


def test_explicit_changed_feedback_is_not_silently_overwritten(tmp_path):
    _, payload, _, prepare = setup_retry(tmp_path)
    payload["rework_feedback"] = [{"message": "Different directions"}]
    with pytest.raises(WorkflowOperationError):
        prepare(payload, "a2")


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_replay_rejects_missing_or_tampered_frozen_feedback(tmp_path, damage):
    runtime, payload, _, prepare = setup_retry(tmp_path)
    source, = (runtime.state_dir / "artifacts/reader-repair-context").glob("*.json")
    if damage == "missing":
        source.unlink()
    else:
        source.write_text('{}')
    with pytest.raises((ValueError, FileNotFoundError)):
        prepare(payload, "a2")
    assert len([e for e in runtime.event_log.read_all()
                if e.type == "workflow.operation.requested"]) == 1


def test_replay_does_not_replace_changed_contract_with_prior_authority(tmp_path):
    _, payload, _, prepare = setup_retry(tmp_path)
    payload["contract_snapshot_digest"] = "b" * 64
    with pytest.raises(ValueError):
        prepare(payload, "a2")
