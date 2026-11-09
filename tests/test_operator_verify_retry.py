import argparse

import pytest

from zf.cli.recover import register
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_reconciler import _derive_stage
from zf.runtime.task_pipeline_rework import derive_impl_rework_requests
from zf.runtime.workflow_operation import reduce_workflow_operations


@pytest.fixture
def rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: retry\n  state_dir: state\nroles: []\n')
    state = tmp_path / "state"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log, default_origin="kernel")
    TaskStore(state / "kanban.json").add(Task(id="T1", status="in_progress"))
    identity = dict(workflow_run_id="run-1", task_map_generation="map-1", task_id="T1", operation_generation=4)
    writer.emit("task.pipeline.generation.admitted", payload={**identity, "schema_version": "task-pipeline-generation.v1", "task_ids": ["T1"]})
    sources = {}
    for stage, verdict, event_type in [("impl", "passed", "dev.build.done"), ("verify", "rejected", "task.pipeline.verify.completed")]:
        p = {**identity, "operation_id": stage, "task_pipeline_stage": stage}
        writer.emit("workflow.operation.requested", task_id="T1", payload=p)
        source = writer.emit(event_type, task_id="T1", payload={**p, "source_commit": "a" * 40, "target_commit": "a" * 40})
        sources[stage] = source
        writer.emit("workflow.call.result.admitted", task_id="T1", payload={**p, "semantic_verdict": verdict, "control_result_ref": {"source_event_id": source.id}})
        writer.emit("workflow.operation.settled", task_id="T1", payload=p)
    monkeypatch.setattr("zf.runtime.task_pipeline_reconciler.task_pipeline_policy_for_contexts", lambda *a, **k: {"max_rework_attempts": 2})
    return state, log, writer, identity, sources


def _invoke(state, *extra):
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers())
    args = parser.parse_args(["recover", "task-impl", "--state-dir", str(state), "--operation", "verify", "--reason", "reviewed independent rejection and bounded repair", *extra])
    return args.func(args)


def test_verify_rejection_requires_bound_grant_and_preserves_verdict(rejected):
    state, log, _, _, _ = rejected
    before = log.path.read_bytes()
    assert _invoke(state) == 1
    assert _invoke(state, "--authorize-extra-attempt") == 1
    grant = ("--authorize-extra-attempt", "--approval-ref", "owner:one-repair")
    assert _invoke(state, *grant) == 0
    assert log.path.read_bytes() == before
    for _ in range(2):
        assert _invoke(state, *grant, "--apply") == 0
    events = log.read_all()
    assert len([e for e in events if e.type == "task.rework.requested"]) == 1
    ops = reduce_workflow_operations(events)
    assert ops["verify"]["semantic_verdict"] == "rejected"
    requests = derive_impl_rework_requests(events=events, generation_contexts=task_pipeline_generation_contexts(events), operation_rows=ops.values())
    assert _derive_stage(ops, task={}, policy={"max_rework_attempts": 2, "semantic_failure_count": 3}, impl_rework_request=requests["T1"])[:2] == ("impl_rework_ready", 5)
    next_impl = {"impl": {**ops["impl"], "operation_generation": 5}, "verify": ops["verify"]}
    assert _derive_stage(next_impl, task={}, policy={"max_rework_attempts": 2, "semantic_failure_count": 3}, impl_rework_request=requests["T1"])[:2] == ("verify_ready", 5)
    current = {"impl": {**ops["impl"], "operation_generation": 5}, "verify": {**ops["verify"], "operation_generation": 5}}
    assert _derive_stage(current, task={}, policy={"max_rework_attempts": 2, "semantic_failure_count": 3}, impl_rework_request=requests["T1"])[0] == "blocked"


@pytest.mark.parametrize("change", ["passed", "new_impl", "new_verify", "different_target", "impl_not_passed"])
def test_verify_retry_rejects_stale_or_unbound_target(rejected, change):
    state, log, writer, identity, sources = rejected
    if change in {"new_impl", "new_verify"}:
        stage = change.removeprefix("new_")
        writer.emit("workflow.operation.requested", task_id="T1", payload={**identity, "operation_id": "new", "operation_generation": 5, "task_pipeline_stage": stage})
        writer.emit("workflow.operation.settled", task_id="T1", payload={**identity, "operation_id": "new", "operation_generation": 5, "task_pipeline_stage": stage})
    else:
        stage = "impl" if change == "impl_not_passed" else "verify"
        p = {**identity, "operation_id": stage, "task_pipeline_stage": stage}
        source = sources[stage]
        if change == "different_target":
            source = writer.emit("task.pipeline.verify.completed", task_id="T1", payload={**p, "target_commit": "b" * 40})
        writer.emit("workflow.call.result.admitted", task_id="T1", payload={**p, "semantic_verdict": "failed" if change == "impl_not_passed" else "rejected" if change == "different_target" else "passed", "control_result_ref": {"source_event_id": source.id}})
    before = log.path.read_bytes()
    assert _invoke(state, "--authorize-extra-attempt", "--approval-ref", "owner:review", "--apply") == 1
    assert log.path.read_bytes() == before
