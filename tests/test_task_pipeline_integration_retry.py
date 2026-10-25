import argparse
import json
from types import SimpleNamespace

import pytest

from tests.test_candidate_incremental import _integrate, _setup
from tests.test_task_pipeline_reconciler import _policy
from zf.cli.recover import register
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_integration import reconcile_task_pipeline_integration
from zf.runtime.task_pipeline_integration_retry import request_integration_retry
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.workflow_operation import reduce_workflow_operations


def seed_verified(writer, task_id="T1", generation="map-g1"):
    identity = dict(task_id=task_id, workflow_run_id="run-1", task_map_generation=generation,
                    operation_generation=1)
    writer.append(ZfEvent(type="task.pipeline.generation.admitted", payload={
        **identity, "schema_version": "task-pipeline-generation.v1", "task_ids": [task_id],
    }))
    for stage in ("impl", "verify"):
        p = {**identity, "operation_id": f"op-{stage}", "task_pipeline_stage": stage,
             "request_hash": f"hash-{stage}", "role_instance": f"{stage}-1"}
        writer.append(ZfEvent(type="workflow.operation.requested", task_id=task_id, payload=p))
        writer.append(ZfEvent(type="workflow.call.result.admitted", task_id=task_id,
                             payload={**p, "semantic_verdict": "passed"}))
        writer.append(ZfEvent(type="workflow.operation.settled", task_id=task_id, payload=p))
    return identity


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: retry\n  state_dir: state\nroles: []\n')
    state = tmp_path / "state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(Task(id="T1", status="backlog"))
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    identity = {**seed_verified(writer), "operation_id": "op-integration",
                "task_pipeline_stage": "integration", "request_hash": "hash-integration"}
    writer.append(ZfEvent(type="workflow.operation.requested", task_id="T1", payload=identity))
    writer.append(ZfEvent(type="workflow.operation.blocked", task_id="T1", payload=identity))
    source = writer.append(ZfEvent(type="integration.queue.needs_review", task_id="T1",
                                   origin="kernel", payload=identity))
    return state, log, writer, identity, source


def invoke(state, apply=False):
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args(["recover", "task-integration", "--state-dir", str(state),
                              "--operation", "op-integration", "--reason", "setup repaired",
                              *(["--apply"] if apply else [])])
    return args.func(args)


def test_cli_preview_apply_and_integration_only_next_generation(case, capsys):
    state, log, _, _, source = case
    before = log.path.read_bytes()
    assert invoke(state) == 0
    assert log.path.read_bytes() == before
    assert json.loads(capsys.readouterr().out)["next_operation_generation"] == 2
    assert invoke(state, True) == 0
    first = log.path.read_bytes()
    assert invoke(state, True) == 0
    assert log.path.read_bytes() == first
    events = log.read_all()
    superseded = [e for e in events if e.type == "workflow.operation.superseded"]
    assert len(superseded) == 1
    assert superseded[0].causation_id == source.id
    assert source in events
    operations = reduce_workflow_operations(events)
    plan = TaskPipelineReconciler().reconcile(
        policy=_policy(), tasks=TaskStore(state / "kanban.json").list_all(),
        operations=operations.values(), attempts=[],
    )
    assert plan["queues"]["integration_ready"] == ["T1"]
    assert plan["tasks"][0]["next_operation_generation"] == 2
    assert plan["dispatchable"]["impl"] == []
    assert plan["dispatchable"]["verify"] == []


@pytest.mark.parametrize("fault", ["wrong_stage", "old_map", "active_operation", "new_integration",
                                   "new_impl", "verify_failed", "task_done", "run_done",
                                   "missing_source", "settled", "cancelled"])
def test_rejects_stale_or_unsafe_retry(case, fault, capsys):
    state, log, writer, identity, _ = case
    if fault == "wrong_stage":
        # Exact current operation request cannot be changed; target a fresh non-integration id.
        with pytest.raises(ValueError, match="integration operation"):
            request_integration_retry(state_dir=state, event_log=log, event_writer=writer,
                                      operation_id="op-impl", reason="repair", apply=True)
        return
    if fault == "old_map":
        writer.append(ZfEvent(type="task.pipeline.generation.admitted", payload={
            **identity, "schema_version": "task-pipeline-generation.v1", "task_ids": ["T1"],
            "task_map_generation": "map-new",
        }))
    elif fault in {"active_operation", "new_integration", "new_impl"}:
        p = {**identity, "operation_id": "op-new", "operation_generation": 2,
             "task_pipeline_stage": "impl" if fault == "new_impl" else "integration"}
        writer.append(ZfEvent(type="workflow.operation.requested", task_id="T1", payload=p))
        if fault != "active_operation":
            writer.append(ZfEvent(type="workflow.operation.blocked", task_id="T1", payload=p))
    elif fault == "verify_failed":
        writer.append(ZfEvent(type="workflow.operation.failed", task_id="T1",
                             payload={"operation_id": "op-verify"}))
    elif fault == "task_done":
        TaskStore(state / "kanban.json").update("T1", status="done")
    elif fault == "run_done":
        writer.append(ZfEvent(type="run.completed", payload={"workflow_run_id": "run-1"}))
    elif fault == "cancelled":
        p = {**identity, "operation_id": "op-cancelled", "operation_generation": 2}
        writer.append(ZfEvent(type="workflow.operation.requested", task_id="T1", payload=p))
        writer.append(ZfEvent(type="workflow.operation.cancelled", task_id="T1", payload=p))
        with pytest.raises(ValueError, match="blocked integration"):
            request_integration_retry(state_dir=state, event_log=log, event_writer=writer,
                                      operation_id="op-cancelled", reason="repair", apply=True)
        return
    elif fault == "missing_source":
        # A blocked operation without a corresponding needs_review receipt is ineligible.
        p = {**identity, "operation_id": "op-unreviewed", "operation_generation": 2}
        writer.append(ZfEvent(type="workflow.operation.requested", task_id="T1", payload=p))
        writer.append(ZfEvent(type="workflow.operation.blocked", task_id="T1", payload=p))
        with pytest.raises(ValueError, match="needs_review"):
            request_integration_retry(state_dir=state, event_log=log, event_writer=writer,
                                      operation_id="op-unreviewed", reason="repair", apply=True)
        return
    else:
        writer.append(ZfEvent(type=f"workflow.operation.{fault}", task_id="T1", payload=identity))
    before = log.path.read_bytes()
    assert invoke(state, True) == 1
    assert log.path.read_bytes() == before
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"


def test_cli_respects_run_contract_gate(case, monkeypatch, capsys):
    state, log, *_ = case
    monkeypatch.setattr("zf.cli.recover._run_contract_recovery_allowed", lambda *args: False)
    before = log.path.read_bytes()
    assert invoke(state, True) == 1
    assert log.path.read_bytes() == before
    assert "run contract drift" in capsys.readouterr().out


def test_active_attempt_blocks_retry_even_after_operation_settled(case, capsys):
    state, log, *_ = case
    TaskAttemptStore(state / "task_attempts.json").ensure_for_dispatch(
        run_id="run-1", task_id="T1", dispatch_id="verify-delivery",
        role="verify", instance_id="verify-1", operation_id="op-verify",
        briefing_ref="briefing.md", created_at="2026-10-25T12:00:00+00:00",
        lease_expires_at="2026-10-25T13:00:00+00:00", max_attempts=3,
    )
    before = log.path.read_bytes()
    assert invoke(state, True) == 1
    assert log.path.read_bytes() == before
    assert "TaskAttempt must settle" in capsys.readouterr().out


@pytest.mark.mock_e2e
def test_setup_repair_retries_real_candidate_integration_without_provider(tmp_path):
    state, config, log, writer, base, _ = _setup(tmp_path)
    seed_verified(writer, task_id="TASK-A")
    config.project.setup_script = "exit 2"
    failed = _integrate(tmp_path, state, config, log, writer, base)
    assert failed.status == "needs_review"
    old_id = failed.payload["operation_id"]
    config.project.setup_script = "mkdir -p node_modules && touch node_modules/prepared"
    request_integration_retry(state_dir=state, event_log=log, event_writer=writer,
                              operation_id=old_id, reason="setup command repaired", apply=True)
    operations = list(reduce_workflow_operations(log.read_all()).values())
    plan = TaskPipelineReconciler().reconcile(
        policy=_policy(), tasks=TaskStore(state / "kanban.json").list_all(),
        operations=operations, attempts=[],
    )
    contexts = task_pipeline_generation_contexts(log.read_all())
    contexts["TASK-A"]["dispatch_base_commit"] = base
    runtime = SimpleNamespace(state_dir=state, project_root=tmp_path, config=config,
                              event_log=log, event_writer=writer,
                              task_store=TaskStore(state / "kanban.json"))
    decisions = reconcile_task_pipeline_integration(
        runtime, projection=plan, generation_contexts=contexts, operation_rows=operations,
    )
    assert decisions[0].action == "task_pipeline_integrated"
    assert (state / "candidates/F-11111111/worktree/node_modules/prepared").exists()
    rows = reduce_workflow_operations(log.read_all())
    assert rows[old_id]["status"] == "superseded"
    newer = [r for r in rows.values() if r.get("task_pipeline_stage") == "integration" and r["operation_id"] != old_id]
    assert len(newer) == 1
    assert newer[0]["status"] == "settled"
    assert newer[0]["operation_generation"] == 2
    assert rows["op-verify"]["status"] == "settled"
