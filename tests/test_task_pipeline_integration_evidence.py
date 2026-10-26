"""A settled operation alone cannot authorize candidate integration."""
from types import SimpleNamespace

import pytest

from tests.test_candidate_incremental import _setup
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.task_contract_snapshot import effective_contract_revision
from zf.runtime.task_pipeline_integration import reconcile_task_pipeline_integration
from zf.runtime.task_pipeline_terminal import reconcile_task_pipeline_terminals
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.candidates import CandidateRebuilder
from tests.test_task_pipeline_reconciler import _policy


@pytest.mark.parametrize("fault", ["none", "missing", "digest", "target", "run", "contract", "newer_failed"])
def test_only_current_admitted_verify_unlocks_integration_and_dependencies(tmp_path, fault):
    state, config, log, writer, base, target = _setup(tmp_path)
    store = TaskStore(state / "kanban.json")
    store.add(Task(id="TASK-B", title="Downstream", blocked_by=["TASK-A"]))
    rebuilder = CandidateRebuilder(state_dir=state, project_root=tmp_path, config=config, event_log=log)
    runtime = SimpleNamespace(
        state_dir=state, project_root=tmp_path, config=config,
        event_log=log, event_writer=writer, task_store=store,
        transport=SimpleNamespace(is_alive=lambda _: False),
        _task_ref_entry=lambda task_id: rebuilder._task_index().get(task_id, {}),
    )
    context = {"workflow_run_id": "run-1", "task_map_generation": "map-g1", "dispatch_base_commit": base}
    body = {
        "schema_version": "verification-result.v1", "task_id": "TASK-A",
        "workflow_run_id": "run-1", "task_map_generation": "map-g1",
        "contract_revision": effective_contract_revision(store.get("TASK-A")),
        "target_commit": target, "verdict": "passed",
    }
    if fault in {"target", "run", "contract"}:
        body[{"target": "target_commit", "run": "workflow_run_id", "contract": "contract_revision"}[fault]] = "stale"
    descriptor = write_sidecar_json(
        state, "artifacts/verified.json", body, kind="control_result",
        schema_version="verification-result.v1", created_by="test-admission",
    )
    if fault == "digest":
        descriptor["sha256"] = "0" * 64
    operation = {
        "operation_id": "op-verify", "task_id": "TASK-A", "task_pipeline_stage": "verify",
        "workflow_run_id": "run-1", "task_map_generation": "map-g1",
        "operation_generation": 1, "status": "settled", "semantic_verdict": "passed",
        "admitted_control_result_ref": descriptor, "call_result_admitted_event_id": "evt-admitted",
    }
    if fault == "missing":
        operation.pop("admitted_control_result_ref")
        operation.pop("call_result_admitted_event_id")
    operations = [operation]
    if fault == "newer_failed":
        operations.append({**operation, "operation_generation": 2, "status": "failed", "semantic_verdict": "failed"})
    tasks = [store.get("TASK-A"), store.get("TASK-B")]
    reconciler = TaskPipelineReconciler()
    before = reconciler.reconcile(policy=_policy(), tasks=tasks, operations=operations, attempts=[])
    assert "TASK-B" not in [row["task_id"] for row in before["dispatchable"]["impl"]]
    decisions = reconcile_task_pipeline_integration(
        runtime, projection={"queues": {"integration_ready": ["TASK-A"]}},
        generation_contexts={"TASK-A": context}, operation_rows=operations,
    )
    reconcile_task_pipeline_terminals(runtime, generation_contexts={"TASK-A": context})
    if fault != "none":
        assert decisions == []
        assert store.get("TASK-A").status != "done"
        assert not any(e.type == "integration.queue.integrated" for e in log.read_all())
        assert any(e.payload.get("reason") == "waiting_for_current_verify_result" for e in log.read_all())
        return
    assert decisions[0].action == "task_pipeline_integrated"
    assert store.get("TASK-A").status == "done"
    after = reconciler.reconcile(
        policy=_policy(), tasks=[store.get("TASK-A"), store.get("TASK-B")],
        operations=operations, attempts=[],
    )
    assert after["dispatchable"]["impl"][0]["task_id"] == "TASK-B"
    reconcile_task_pipeline_terminals(runtime, generation_contexts={"TASK-A": context})
    assert len([e for e in log.read_all() if e.type == "task.done"]) == 1
