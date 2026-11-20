import pytest

from zf.autoresearch.failure_signals import detect_completion_without_gate
from zf.core.events import ZfEvent


def event(kind, task="A", **payload):
    return ZfEvent(type=kind, task_id=task, payload=payload)


@pytest.mark.parametrize("gate_task,gate_run,gate_generation", [
    ("B", "run-1", "g1"), ("A", "run-2", "g1"), ("A", "run-1", "g0"),
    ("A", "", "g1"),
])
def test_foreign_gate_cannot_suppress_completion_alert(tmp_path, gate_task, gate_run, gate_generation):
    done = event("task.done", workflow_run_id="run-1", task_map_generation="g1")
    gate = event("test.passed", gate_task, workflow_run_id=gate_run, task_map_generation=gate_generation)
    signals = detect_completion_without_gate([gate, done], state_dir=tmp_path)
    assert len(signals) == 1
    assert signals[0].event_ids == [done.id]


def test_one_task_gate_only_closes_its_own_alert(tmp_path):
    gate = event("test.passed")
    done = event("task.done")
    other = event("task.done", "B")
    signals = detect_completion_without_gate([gate, done, other], state_dir=tmp_path)
    assert len(signals) == 1
    assert signals[0].event_ids == [other.id]


@pytest.mark.parametrize("reset", ["task.requeued", "task.rework.requested", "task.contract.update"])
def test_legacy_gate_does_not_survive_new_task_work(tmp_path, reset):
    done = event("task.done")
    assert detect_completion_without_gate([event("test.passed"), event(reset), done], state_dir=tmp_path)


def test_gate_must_precede_completion(tmp_path):
    done = event("task.done")
    assert detect_completion_without_gate([done, event("test.passed")], state_dir=tmp_path)
    assert not detect_completion_without_gate([event("test.passed"), done], state_dir=tmp_path)


def test_legacy_explicit_correlation_scopes_are_not_globally_unscoped(tmp_path):
    gate, done = event("test.passed"), event("task.done")
    gate.correlation_id, done.correlation_id = "run-1", "run-2"
    assert detect_completion_without_gate([gate, done], state_dir=tmp_path)


def test_other_run_requeue_does_not_invalidate_gate(tmp_path):
    events = [event("test.passed", workflow_run_id="run-1"),
              event("task.requeued", workflow_run_id="run-2"),
              event("task.done", workflow_run_id="run-1")]
    assert not detect_completion_without_gate(events, state_dir=tmp_path)


@pytest.mark.parametrize("fault", ["", "wrong_task", "wrong_generation", "wrong_digest", "worker_origin", "missing"])
def test_typed_terminal_uses_its_exact_integration_receipt(tmp_path, fault):
    from types import SimpleNamespace
    from zf.core.events import EventLog, EventWriter
    from zf.runtime.sidecar_refs import write_sidecar_json
    from zf.runtime.task_pipeline_terminal import _emit_task_done_once

    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    body = {"task_id": "A", "workflow_run_id": "run-1", "task_map_generation": "g1",
            "candidate_generation": "cg1", "new_candidate_head": "a" * 40}
    descriptor = write_sidecar_json(tmp_path, "artifacts/integration.json", body,
        kind="task-integration-receipt", schema_version="task-integration-receipt.v1", created_by="kernel")
    integrated = writer.append(ZfEvent(type="integration.queue.integrated", origin="kernel", task_id="A",
        payload={**body, "receipt_ref": descriptor, "receipt_digest": descriptor["sha256"]}))
    _emit_task_done_once(SimpleNamespace(event_log=log, event_writer=writer), task_id="A",
        receipt=body, receipt_ref=descriptor, integration_event=integrated)
    events = log.read_all()
    done = events[-1]
    if fault == "wrong_task":
        integrated.task_id = "B"
        events[0] = integrated
    elif fault == "wrong_generation":
        done.payload["task_map_generation"] = "g2"
    elif fault == "wrong_digest":
        done.payload["integration_receipt_digest"] = "b" * 64
    elif fault == "worker_origin":
        done.origin = "worker"
    elif fault == "missing":
        (tmp_path / "artifacts/integration.json").unlink()
    assert bool(detect_completion_without_gate(events, state_dir=tmp_path)) == bool(fault)
