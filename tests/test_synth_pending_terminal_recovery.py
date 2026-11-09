"""A failed fanout cannot keep a never-started synthesis call pending forever."""

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import reduce_workflow_operations

from tests.test_reader_fanout_runtime import _task_map_synth_orchestrator


def _failed_synth(tmp_path, monkeypatch):
    state_dir, log, orch, fanout_id = _task_map_synth_orchestrator(
        tmp_path, complete_child=False,
    )

    def fail(*args, **kwargs):
        raise RuntimeError("provider dispatch failed before start")

    monkeypatch.setattr(orch, "_send_transport_task", fail)
    orch.run_once(events=[ZfEvent(
        type="task_map.child.completed", actor="source-reader",
        correlation_id="trace-product",
        payload={"fanout_id": fanout_id, "child_id": "source-reader",
                 "run_id": f"run-{fanout_id}-source-reader", "status": "completed",
                 "summary": "read complete", "artifact_refs": ["docs/product-prd.md"]},
    )])
    op = next(o for o in reduce_workflow_operations(log.read_all()).values()
              if o["operation_type"] == "fanout_synth")
    assert op["status"] == "requested"
    return state_dir, log, orch, fanout_id, op


def test_recovery_sweep_settles_only_never_started_failed_synth(tmp_path, monkeypatch):
    _, log, orch, _, op = _failed_synth(tmp_path, monkeypatch)
    orch._recover_unrecorded_reader_fanout_results()
    current = reduce_workflow_operations(log.read_all())[op["operation_id"]]
    assert current["status"] == "superseded"
    terminal = [e for e in log.read_all() if e.type == "workflow.operation.superseded"]
    assert len(terminal) == 1
    assert terminal[0].payload["semantic_attempt_consumed"] is False
    assert terminal[0].causation_id in {e.id for e in log.read_all()
                                      if e.type == "fanout.aggregate.completed"}
    orch._recover_unrecorded_reader_fanout_results()
    assert len([e for e in log.read_all() if e.type == "workflow.operation.superseded"]) == 1
    assert not any(e.type == "fanout.synth.dispatched" for e in log.read_all())


@pytest.mark.parametrize("guard", ["running", "dispatched", "wrong_run", "wrong_stage",
                                    "worker_terminal", "reopened", "corrupt_request"])
def test_pending_recovery_preserves_unproven_or_live_operations(tmp_path, monkeypatch, guard):
    state_dir, log, orch, fanout_id, op = _failed_synth(tmp_path, monkeypatch)
    from zf.runtime.synth_pending_recovery import reconcile_failed_pending_synth
    events = log.read_all()
    if guard == "running":
        orch.event_writer.append(ZfEvent(
            type="workflow.operation.started", actor="zf-cli", payload={
                "operation_id": op["operation_id"], "request_hash": op["request_hash"],
                "workflow_run_id": op["workflow_run_id"],
                "active_attempt_id": op["active_attempt_id"],
            },
        ))
        events = log.read_all()
    elif guard == "dispatched":
        events.append(ZfEvent(type="fanout.synth.dispatched", payload={
            "fanout_id": fanout_id, "run_id": op["active_attempt_id"],
            "operation_id": op["operation_id"],
        }))
    elif guard == "reopened":
        events.append(ZfEvent(type="fanout.aggregate.started", origin="kernel",
                             payload={"fanout_id": fanout_id}))
    elif guard == "corrupt_request":
        (state_dir / op["request_ref"]["ref"]).write_text("{}")
    else:
        for e in events:
            if e.type == "fanout.aggregate.completed":
                if guard == "wrong_run":
                    e.payload["workflow_run_id"] = "another-run"
                elif guard == "wrong_stage":
                    e.payload["stage_id"] = "another-stage"
                else:
                    e.origin = "worker"
    assert not reconcile_failed_pending_synth(orch, events)
    assert not any(e.type == "workflow.operation.superseded" for e in log.read_all())
