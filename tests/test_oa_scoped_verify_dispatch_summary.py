"""The scoped Verify decision can consult the same frozen Run view on demand."""
import json

import pytest

from tests.test_oa_verify_predecessor_release import setup_run
from tests.test_orchestrator_agent_run_plan import _checkpoint, _runtime
from zf.runtime.artifact_read_ledger import read_attempt_artifact
from zf.runtime.orchestrator_agent_operations import prepared_operation_from_checkpoint_event
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_pipeline_dispatch_summary import build_dispatch_summary


def _pipeline_projection(run):
    tasks = [
        {"task_id": task, "title": task, "workflow_run_id": scope, "task_status": "in_progress",
         "pipeline_stage": "verify_ready"}
        for task, scope in (("task-local", run), ("report", run), ("unrelated", "other-project-run"))
    ]
    return {
        "tasks": tasks, "operations": [], "projection_digest": "c" * 64,
        "capacity": {"verify": {"limit": 3, "available": 2}},
        "backpressure": {"state": "normal"},
        "dispatchable": {"verify": [{"task_id": row["task_id"]} for row in tasks]},
    }


def test_corrective_checkpoint_keeps_only_its_target_in_the_required_summary(tmp_path, monkeypatch):
    runtime, task, context, _, _, sweep, decide, _ = setup_run(tmp_path, monkeypatch)
    run = context["workflow_run_id"]
    projection = _pipeline_projection(run)
    monkeypatch.setattr("zf.runtime.task_pipeline_projection.read_task_pipeline_projection",
                        lambda *args, **kwargs: projection)
    runtime.task_store.update(task.id, active_dispatch_id="")
    requested, = sweep()
    prepared = prepared_operation_from_checkpoint_event(runtime, requested)
    ctx = prepared.context
    assert ctx.stage_card["objective"] == ctx.input_body["objective"]
    assert task.id in ctx.stage_card["objective"]
    assert "independent verification" in ctx.stage_card["objective"]
    execution = hydrate_sidecar_ref(runtime.state_dir, ctx.input_body["execution_context_ref"]).payload
    assert execution["dispatch_summary"] == build_dispatch_summary(projection, workflow_run_id=run, task_ids={task.id})
    assert execution["dispatch_summary"]["capacity"] == projection["capacity"]
    assert execution["dispatch_summary"]["backpressure"] == projection["backpressure"]

    all_tasks, = [source for source in ctx.source_manifest["sources"] if source["source_id"] == "run-dispatch-summary"]
    assert all_tasks["sha256"] == execution["full_dispatch_summary_ref"]["sha256"]
    assert "run-dispatch-summary" not in {row["source_id"] for row in ctx.read_policy["required_reads"]}
    original = build_dispatch_summary(projection, workflow_run_id=run)
    assert [row["task_id"] for row in original["tasks"]] == [task.id, "report"]
    projection["tasks"].clear()
    read = read_attempt_artifact(runtime.state_dir, manifest=ctx.source_manifest, source_id=all_tasks["source_id"],
                                 artifact_id=all_tasks["artifact_id"], artifact_sha256=all_tasks["sha256"], record=False)
    assert json.loads(read["content"]) == original
    replayed, = sweep()
    assert replayed.id == requested.id
    assert prepared_operation_from_checkpoint_event(runtime, replayed).request_hash == prepared.request_hash
    assert decide(prepared)["status"] == "applied"


@pytest.mark.parametrize("execution", [True, False])
def test_initial_pre_impl_keeps_its_plan_objective_and_original_sources(tmp_path, monkeypatch, execution):
    runtime, trigger, loaded, _, _ = _runtime(tmp_path)
    if execution:
        runtime.config.workflow.orchestration.autonomous_actions = ["delegate"]
    projection = _pipeline_projection(loaded.workflow_run_id)
    monkeypatch.setattr("zf.runtime.task_pipeline_projection.read_task_pipeline_projection",
                        lambda *args, **kwargs: projection)
    _, prepared = _checkpoint(runtime, trigger, loaded)
    ctx = prepared.context
    assert ctx.stage_card["objective"] == "Adopt the Plan-bound execution graph before implementation."
    assert "run-dispatch-summary" not in {src["source_id"] for src in ctx.source_manifest["sources"]}
    if execution:
        context = hydrate_sidecar_ref(runtime.state_dir, ctx.input_body["execution_context_ref"]).payload
        assert "full_dispatch_summary_ref" not in context
        assert context["dispatch_summary"] == build_dispatch_summary(projection, workflow_run_id=loaded.workflow_run_id)
