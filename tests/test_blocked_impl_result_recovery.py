from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.task_pipeline_rework import reconcile_task_ref_repair_replays


def _case(tmp_path, *, gate_status="updated", variant="current"):
    log = EventLog(tmp_path / "events.jsonl")
    w = EventWriter(log, default_origin="kernel")
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="T1", status="done" if variant == "task_done" else "in_progress"))
    identity = dict(workflow_run_id="run-1", task_id="T1", task_map_generation="map-1", operation_generation=1, operation_id="impl-1", task_pipeline_stage="impl")
    w.emit("run.admission.admitted", payload={"run_id": "run-1"})
    w.emit("workflow.operation.requested", task_id="T1", payload=identity)
    w.emit("run.goal.blocked", payload={"run_id": "run-1"})
    source = w.emit("dev.build.done", task_id="T1", payload={**identity, "source_commit": "a" * 40})
    w.emit("workflow.call.result.admitted", task_id="T1", payload={**identity, "semantic_verdict": "failed" if variant == "failed" else "passed", "control_result_ref": {"source_event_id": "wrong" if variant == "unbound" else source.id}})
    w.emit("workflow.operation.settled", task_id="T1", payload=identity)
    rejection = w.emit("run.result.rejected", task_id="T1", payload={"run_id": "run-1", "workflow_run_id": "run-1", "source_event_id": source.id, "reason": "run_terminal:cancelled" if variant == "cancelled" else "run_terminal:blocked", "terminal_type": "run.goal.blocked"})
    resume = w.emit("run.goal.updated", payload={"run_id": "run-1", "status": "blocked" if variant == "still_blocked" else "active"})
    if variant == "newer_operation":
        w.emit("workflow.operation.requested", task_id="T1", payload={**identity, "operation_id": "impl-2", "operation_generation": 2})
    calls = []
    def process(event):
        calls.append(event.id)
        return SimpleNamespace(status=gate_status, payload={"trigger_event_id": event.id, "source_commit": event.payload["source_commit"], "reason": "scope checked"})
    runtime = SimpleNamespace(event_log=log, event_writer=w, task_store=store, _process_task_ref_for_progress_event=process)
    contexts = {"T1": {"workflow_run_id": "other" if variant == "other_run" else "run-1", "task_map_generation": "map-2" if variant == "stale_map" else "map-1"}}
    return runtime, contexts, calls, source, rejection, resume


@pytest.mark.parametrize("gate_status", ["updated", "rejected"])
def test_resumed_run_replays_only_task_ref_gate_for_admitted_impl(tmp_path, gate_status):
    runtime, contexts, calls, source, rejection, resume = _case(tmp_path, gate_status=gate_status)
    first = reconcile_task_ref_repair_replays(runtime, events=runtime.event_log.read_all(), generation_contexts=contexts)
    # Reconstruct the runtime to prove ledger idempotency, not only a memory fence.
    runtime = SimpleNamespace(event_log=runtime.event_log, event_writer=runtime.event_writer, task_store=runtime.task_store, _process_task_ref_for_progress_event=runtime._process_task_ref_for_progress_event)
    second = reconcile_task_ref_repair_replays(runtime, events=runtime.event_log.read_all(), generation_contexts=contexts)
    assert calls == [source.id]
    assert len(first) == 1 and second == []
    assert first[0].type == f"task.ref.{gate_status}"
    assert first[0].payload["resume_event_id"] == resume.id
    assert first[0].payload["run_result_rejection_event_id"] == rejection.id
    assert not any(e.type in {"task.done", "verify.passed"} for e in runtime.event_log.read_all())


@pytest.mark.parametrize("variant", ["failed", "unbound", "cancelled", "still_blocked", "newer_operation", "other_run", "stale_map", "task_done"])
def test_blocked_result_replay_rejects_unproven_or_obsolete_result(tmp_path, variant):
    runtime, contexts, calls, *_ = _case(tmp_path, variant=variant)
    assert reconcile_task_ref_repair_replays(runtime, events=runtime.event_log.read_all(), generation_contexts=contexts) == []
    assert calls == []
