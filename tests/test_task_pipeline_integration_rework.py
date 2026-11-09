"""Integration failures can request bounded source repair without changing old verdicts."""

import argparse
import json
from dataclasses import asdict

import pytest

from tests.test_task_pipeline_verify_retry import seed
from tests.test_task_pipeline_reconciler import _policy
from tests.test_task_pipeline_capsule import dispatch_fixture  # noqa: F401
from zf.cli.recover import register
from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.task_pipeline_dispatch import dispatch_task_pipeline_stage
from zf.runtime.task_pipeline_integration_rework import integration_rework_feedback
from zf.runtime.task_pipeline_integration_retry import IMPL_REWORK_REASON_PREFIX
from zf.runtime.workflow_operation import reduce_workflow_operations


def blocked_case(tmp_path, monkeypatch, **kwargs):
    state, log, writer, task, descriptor = seed(
        tmp_path, monkeypatch, verdict="passed", execution="completed", **kwargs,
    )
    identity = dict(task_id=task.id, workflow_run_id="run", task_map_generation="map",
                    operation_generation=2, operation_id="integration",
                    task_pipeline_stage="integration", request_hash="integration-hash")
    writer.append(ZfEvent(type="workflow.operation.requested", task_id=task.id, payload=identity))
    writer.append(ZfEvent(type="workflow.operation.blocked", task_id=task.id, payload={
        **identity, "reason": "rolling smoke left candidate worktree dirty",
    }))
    source = writer.append(ZfEvent(type="integration.queue.needs_review", origin="kernel",
                                   task_id=task.id, payload=identity))
    return state, log, writer, task, descriptor, identity, source


def invoke(state, *, apply=False, rework=True):
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args([
        "recover", "task-integration", "--state-dir", str(state),
        "--operation", "integration", "--reason", "write smoke evidence outside tracked snapshots",
        *(["--apply"] if apply else []), *(["--restart-from-impl"] if rework else []),
    ])
    return args.func(args)


@pytest.mark.mock_e2e
def test_cli_rework_reenters_impl_then_requires_new_verify(tmp_path, monkeypatch, capsys):
    state, log, writer, task, _, identity, source = blocked_case(tmp_path, monkeypatch)
    original = log.path.read_bytes()
    original_task = asdict(TaskStore(state / "kanban.json").get(task.id))
    assert invoke(state) == 0
    assert log.path.read_bytes() == original
    preview = json.loads(capsys.readouterr().out)
    assert preview["next_stage"] == "impl"
    assert preview["target_commit"] == "a" * 40
    assert invoke(state, apply=True) == 0
    applied = log.path.read_bytes()
    assert invoke(state, apply=True) == 0
    assert log.path.read_bytes() == applied
    assert applied.startswith(original)
    assert asdict(TaskStore(state / "kanban.json").get(task.id)) == original_task
    assert json.loads((state / "refs/task-index.json").read_text())[task.id]["source_commit"] == "a" * 40
    rows = reduce_workflow_operations(log.read_all())
    assert rows["impl"]["status"] == rows["verify"]["status"] == "settled"
    assert rows["verify"]["semantic_verdict"] == "passed"
    superseded = [e for e in log.read_all() if e.type == "workflow.operation.superseded"]
    assert len(superseded) == 1 and superseded[0].causation_id == source.id
    plan = TaskPipelineReconciler().reconcile(
        policy=_policy(), tasks=[task], operations=rows.values(), attempts=[],
    )
    assert plan["dispatchable"]["impl"][0]["operation_generation"] == "3"
    assert plan["dispatchable"]["verify"] == []
    assert plan["queues"]["integration_ready"] == []
    assert integration_rework_feedback(
        rows.values(), task_id=task.id, workflow_run_id="run",
        task_map_generation="map", operation_generation=2,
    )[0]["reason"] == preview["reason"]
    p = {**identity, "operation_id": "new-impl", "task_pipeline_stage": "impl",
         "operation_generation": 3, "request_hash": "new-hash"}
    writer.append(ZfEvent(type="workflow.operation.requested", task_id=task.id, payload=p))
    writer.append(ZfEvent(type="workflow.call.result.admitted", task_id=task.id,
                         payload={**p, "semantic_verdict": "passed"}))
    writer.append(ZfEvent(type="workflow.operation.settled", task_id=task.id, payload=p))
    plan = TaskPipelineReconciler().reconcile(
        policy=_policy(), tasks=[task],
        operations=reduce_workflow_operations(log.read_all()).values(), attempts=[],
    )
    assert plan["dispatchable"]["impl"] == []
    assert plan["dispatchable"]["verify"][0]["operation_generation"] == "3"
    assert plan["queues"]["integration_ready"] == []


@pytest.mark.parametrize("fault", [
    "verify_only", "missing_impl", "failed_impl", "contract", "target", "digest",
    "operation_id", "attempt_id", "request_hash", "task_map_generation", "mode",
    "pause", "run_pause", "new_stage", "active_attempt", "task_done", "missing_source",
])
def test_rework_rejects_invalid_evidence_or_ownership_without_writes(tmp_path, monkeypatch, capsys, fault):
    seed_args = {fault: True} if fault in {"verify_only", "missing_impl", "missing_source"} else {}
    if fault in {"operation_id", "attempt_id", "request_hash", "task_map_generation"}:
        seed_args = {"mismatch": fault}
    state, log, writer, task, descriptor, identity, _ = blocked_case(tmp_path, monkeypatch, **seed_args)
    if fault == "failed_impl":
        writer.append(ZfEvent(type="workflow.operation.failed", payload={"operation_id": "impl"}))
    elif fault == "contract":
        task.contract.behavior = "changed"
        TaskStore(state / "kanban.json").update(task.id, contract=task.contract)
    elif fault == "target":
        (state / "refs/task-index.json").write_text(json.dumps({task.id: {"source_commit": "b" * 40}}))
    elif fault == "digest":
        (state / descriptor["ref"]).write_text('{}')
    elif fault == "mode":
        assert invoke(state, apply=True, rework=False) == 0
        capsys.readouterr()
    elif fault in {"pause", "run_pause"}:
        writer.append(ZfEvent(type="loop.pause_requested" if fault == "pause" else "run.paused",
                             payload={"workflow_run_id": "run"}))
    elif fault == "new_stage":
        p = {**identity, "operation_id": "new-impl", "operation_generation": 3,
             "task_pipeline_stage": "impl"}
        writer.append(ZfEvent(type="workflow.operation.requested", payload=p, task_id=task.id))
    elif fault == "active_attempt":
        from zf.core.state.task_attempts import TaskAttemptStore
        TaskAttemptStore(state / "task_attempts.json").ensure_for_dispatch(
            run_id="run", task_id=task.id, dispatch_id="old-verify", role="verify",
            instance_id="verify-1", operation_id="verify", briefing_ref="briefing.md",
            created_at="2026-11-08T00:00:00+00:00", lease_expires_at="2026-11-08T01:00:00+00:00",
            max_attempts=3,
        )
    elif fault == "task_done":
        TaskStore(state / "kanban.json").update(task.id, status="done")
    before = log.path.read_bytes()
    assert invoke(state, apply=True) == 1
    assert log.path.read_bytes() == before
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"


def test_dispatch_carries_bound_integration_repair_to_next_worker(dispatch_fixture):
    runtime, task, snapshot, prepared, _ = dispatch_fixture
    row = dict(task_id=task.id, workflow_run_id="run-1", task_map_generation="map-new",
               operation_generation=2, task_pipeline_stage="integration", status="superseded",
               reason=IMPL_REWORK_REASON_PREFIX + "fix evidence output", operation_id="integration")
    dispatch_task_pipeline_stage(
        runtime, policy={}, task=task,
        assignment={"stage": "impl", "role_instance": "dev-0", "operation_generation": 3},
        generation_context={"workflow_run_id": "run-1", "flow_kind": "prd",
                            "task_map_generation": "map-new", "generation_admitted_event_id": "evt-admitted",
                            **{k: v for k, v in snapshot.items() if k.startswith("plan_artifact_package_")}},
        operation_rows=[{**row, "task_map_generation": "old", "reason": IMPL_REWORK_REASON_PREFIX + "stale"}, row],
        attempt_rows=[],
    )
    feedback = prepared[0]["payload"]["rework_feedback"]
    assert len(feedback) == 1
    assert feedback[0]["source_operation_id"] == "integration"
    assert feedback[0]["reason"] == "fix evidence output"
