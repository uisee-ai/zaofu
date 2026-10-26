"""Operator retry creates a new Impl generation without rewriting verdicts."""

import argparse
import json

import pytest

from tests.test_task_pipeline_reconciler import _policy
from zf.cli.recover import register
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_reconciler import TaskPipelineReconciler
from zf.runtime.task_pipeline_rework import derive_impl_rework_requests
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.fixture(params=["blocked", "failed"])
def case(tmp_path, monkeypatch, request):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text(
        'version: "1.0"\nproject:\n  name: retry-test\n  state_dir: runtime-state\nroles: []\n'
    )
    state = tmp_path / "runtime-state"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log)
    TaskStore(state / "kanban.json").add(Task(id="T1", status="in_progress"))
    identity = dict(
        workflow_run_id="run-1",
        task_map_generation="map-1",
        task_id="T1",
        operation_id="op-1",
        operation_generation=1,
        task_pipeline_stage="impl",
    )
    writer.append(
        ZfEvent(
            type="task.pipeline.generation.admitted",
            payload={
                "schema_version": "task-pipeline-generation.v1",
                "task_ids": ["T1"],
                **identity,
            },
        )
    )
    writer.append(
        ZfEvent(type="workflow.operation.requested", task_id="T1", payload=identity)
    )
    source = writer.append(
        ZfEvent(type="dev.blocked", task_id="T1", origin="kernel", payload=identity)
    )
    admitted = writer.append(
        ZfEvent(
            type="workflow.call.result.admitted",
            task_id="T1",
            payload={
                **identity,
                "semantic_verdict": request.param,
                "control_result_ref": {"source_event_id": source.id},
            },
        )
    )
    writer.append(
        ZfEvent(type="workflow.operation.settled", task_id="T1", payload=identity)
    )
    return state, log, writer, identity, admitted


def invoke(state, *, apply=False, operation="op-1"):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    register(sub)
    args = parser.parse_args(
        [
            "recover",
            "task-impl",
            "--state-dir",
            str(state),
            "--operation",
            operation,
            "--reason",
            "write guard repaired and regression checked",
            *(["--apply"] if apply else []),
        ]
    )
    return args.func(args)


def test_cli_preview_then_apply_and_scheduler_advance(case, capsys):
    state, log, _, _, _ = case
    before = log.path.read_bytes()
    assert invoke(state) == 0
    assert log.path.read_bytes() == before
    assert invoke(state, apply=True) == 0
    assert invoke(state, apply=True) == 0
    events = log.read_all()
    requests = [e for e in events if e.type == "task.rework.requested"]
    assert len(requests) == 1
    operations = list(reduce_workflow_operations(events).values())
    assert operations[0]["status"] == "settled"
    assert operations[0]["semantic_verdict"] in {"blocked", "failed"}
    rework = derive_impl_rework_requests(
        events=events,
        generation_contexts=task_pipeline_generation_contexts(events),
        operation_rows=operations,
    )
    policy = {**_policy(), "max_rework_attempts": 2}
    plan = TaskPipelineReconciler().reconcile(
        policy=policy,
        tasks=TaskStore(state / "kanban.json").list_all(),
        operations=operations,
        impl_rework_requests=rework,
        attempts=[],
    )
    assert plan["tasks"][0]["stage"] == "impl_rework_ready"
    assert plan["dispatchable"]["impl"][0]["operation_generation"] == "2"
    assert plan["dispatchable"]["impl"][0]["stage"] == "impl"


@pytest.mark.parametrize(
    "change",
    ["passed", "running", "old_map", "new_operation", "wrong_stage", "terminal_task"],
)
def test_cli_rejects_non_current_or_non_blocked_target(case, change):
    state, log, writer, identity, _ = case
    if change == "passed":
        writer.append(
            ZfEvent(
                type="workflow.call.result.admitted",
                task_id="T1",
                payload={**identity, "semantic_verdict": "passed"},
            )
        )
    elif change == "running":
        writer.append(
            ZfEvent(
                type="workflow.operation.requested",
                task_id="T1",
                payload={**identity, "operation_id": "op-2", "operation_generation": 2},
            )
        )
    elif change == "old_map":
        writer.append(
            ZfEvent(
                type="task.pipeline.generation.admitted",
                payload={
                    **identity,
                    "schema_version": "task-pipeline-generation.v1",
                    "task_ids": ["T1"],
                    "task_map_generation": "map-2",
                },
            )
        )
    elif change == "new_operation":
        writer.append(
            ZfEvent(
                type="workflow.operation.requested",
                task_id="T1",
                payload={**identity, "operation_id": "op-2", "operation_generation": 2},
            )
        )
        writer.append(
            ZfEvent(
                type="workflow.operation.settled",
                task_id="T1",
                payload={**identity, "operation_id": "op-2", "operation_generation": 2},
            )
        )
    elif change == "wrong_stage":
        writer.append(
            ZfEvent(
                type="workflow.operation.settled",
                task_id="T1",
                payload={**identity, "task_pipeline_stage": "verify"},
            )
        )
    else:
        TaskStore(state / "kanban.json").update("T1", status="done")
    before = log.path.read_bytes()
    assert invoke(state, apply=True) == 1
    assert log.path.read_bytes() == before


def test_replayed_request_does_not_rework_newer_generation(case):
    state, log, writer, identity, _ = case
    assert invoke(state, apply=True) == 0
    writer.append(
        ZfEvent(
            type="workflow.operation.requested",
            task_id="T1",
            payload={**identity, "operation_id": "op-2", "operation_generation": 2},
        )
    )
    events = log.read_all()
    ops = list(reduce_workflow_operations(events).values())
    rework = derive_impl_rework_requests(
        events=events,
        generation_contexts=task_pipeline_generation_contexts(events),
        operation_rows=ops,
    )
    assert rework == {}


def test_operator_retry_keeps_rework_cap(case):
    state, log, _, _, _ = case
    assert invoke(state, apply=True) == 0
    events = log.read_all()
    operations = list(reduce_workflow_operations(events).values())
    rework = derive_impl_rework_requests(
        events=events,
        generation_contexts=task_pipeline_generation_contexts(events),
        operation_rows=operations,
    )
    plan = TaskPipelineReconciler().reconcile(
        policy={**_policy(), "max_rework_attempts": 0},
        tasks=TaskStore(state / "kanban.json").list_all(),
        operations=operations,
        attempts=[],
        impl_rework_requests=rework,
    )
    assert plan["dispatchable"]["impl"] == []
    assert plan["tasks"][0]["stage"] == "blocked"


def test_non_kernel_request_cannot_retry_blocked_operation(case):
    _, log, writer, identity, admitted = case
    source_id = admitted.payload["control_result_ref"]["source_event_id"]
    writer.append(
        ZfEvent(
            type="task.rework.requested",
            task_id="T1",
            origin="agent",
            payload={
                **identity,
                "source": "task_pipeline_operator_retry",
                "source_event_id": source_id,
            },
        )
    )
    events = log.read_all()
    assert (
        derive_impl_rework_requests(
            events=events,
            generation_contexts=task_pipeline_generation_contexts(events),
            operation_rows=list(reduce_workflow_operations(events).values()),
        )
        == {}
    )


def test_cli_preserves_run_contract_gate(case, monkeypatch):
    state, log, _, _, _ = case
    monkeypatch.setattr(
        "zf.cli.recover._run_contract_recovery_allowed", lambda *args: False
    )
    before = log.path.read_bytes()
    assert invoke(state, apply=True) == 1
    assert log.path.read_bytes() == before
