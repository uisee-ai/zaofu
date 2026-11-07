"""A WRC-admitted successor owns recovery before an obsolete reader retries."""

from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.workflow_operation import load_workflow_operation
from tests.test_reader_fanout_runtime import _durable_reader_state
from tests.test_fanout_hardening import _FlakyTransport, _state_with_transport


@pytest.mark.parametrize("proof", ["complete", "complete_retried", "complete_restart", "unadmitted", "other_cause", "other_run", "partial", "retained", "not_kernel"])
def test_recovery_tick_closes_only_causally_replaced_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, proof: str,
) -> None:
    state, log, transport, runtime, config, child = _durable_reader_state(
        tmp_path, rework_source=True,
    )
    source = next(e for e in log.read_all() if e.type == "dev.blocked")
    writer = EventWriter(log, default_origin="kernel")
    expected_sends = 1
    if proof == "complete_retried":
        closed = writer.append(ZfEvent(type="provider.turn.closed", actor="verify-1", payload={
            "backend": "codex", "instance_id": "verify-1", "turn_id": "interrupted-turn",
        }))
        runtime.run_once(events=[closed])
        expected_sends = 2
        assert len(transport.sent) == expected_sends
    ready = writer.append(ZfEvent(
        type="task_map.ready", correlation_id="run-durable", payload={
            "workflow_run_id": "run-durable",
            "rework_of": "other-failure" if proof == "other_cause" else source.id,
            "rework_summary": {"gap_tasks": [
                {"task_id": "T-SUCCESSOR", "supersedes_task_ids": ["T-VERIFY"]},
                *([{"task_id": "T-OTHER", "supersedes_task_ids": ["T-VERIFY"]}]
                  if proof == "partial" else []),
            ]},
        },
    ))
    if proof != "unadmitted":
        admitted = writer.append(ZfEvent(
            type="task.pipeline.generation.admitted", correlation_id="run-durable",
            origin="external" if proof == "not_kernel" else "kernel",
            payload={
                "schema_version": "task-pipeline-generation.v1",
                "workflow_run_id": "different" if proof == "other_run" else "run-durable",
                "trigger_event_id": ready.id,
                "task_ids": ["T-SUCCESSOR", *(["T-VERIFY"] if proof == "retained" else [])],
            },
        ))
    stops: list[str] = []
    monkeypatch.setattr(transport, "terminate", lambda role: stops.append(role), raising=False)
    monkeypatch.setattr(transport, "is_alive", lambda role: role not in stops)
    # The stale result is not a product pass; provider closure must not turn it
    # into a fresh delivery for the already replaced task.
    writer.append(ZfEvent(type="provider.turn.closed", actor="verify-1",
                         payload={"backend": "codex", "instance_id": "verify-1",
                                  "provider_status": "completed", "turn_id": "old-turn"}))
    if proof == "complete_restart":
        with monkeypatch.context() as interrupted:
            interrupted.setattr(runtime, "_cancel_superseded_reader_fanout_operations", lambda **kwargs: None)
            runtime.run_once(events=[])
        assert not stops
        runtime = Orchestrator(state, config, transport)
    runtime.run_once(events=[])
    cancelled = [e for e in log.read_all() if e.type == "fanout.cancelled"
                 and e.payload.get("fanout_id") == child["fanout_id"]]
    if not proof.startswith("complete"):
        assert not cancelled
        assert not stops
        return
    assert len(cancelled) == 1
    assert cancelled[0].payload["superseded_by"] == admitted.id
    assert cancelled[0].payload["reason"] == "superseded_by_admitted_task_replacement"
    assert stops == ["verify-1"]
    operation_id = child.get("operation_id") or child["payload"]["operation_id"]
    assert load_workflow_operation(log, operation_id)["status"] == "cancelled"
    assert len(transport.sent) == expected_sends
    assert sum(e.type == "fanout.child.dispatch_lost" for e in log.read_all()) == expected_sends - 1
    assert not any(e.type in {"task.done", "review.approved",
                             "review.rejected", "run.goal.blocked"} for e in log.read_all())
    restarted = Orchestrator(state, config, transport)
    restarted.run_once(events=[])
    assert stops == ["verify-1"]
    assert len(transport.sent) == expected_sends
    assert sum(e.type == "fanout.cancelled" for e in log.read_all()) == 1


def test_delayed_recovery_trigger_is_cancelled_before_first_provider_dispatch(tmp_path: Path) -> None:
    state, log, transport, runtime = _state_with_transport(tmp_path, _FlakyTransport(alive=True))
    writer = EventWriter(log, default_origin="kernel")
    source = writer.append(ZfEvent(type="dev.blocked", task_id="T-OLD", correlation_id="trace-1"))
    trigger = writer.append(ZfEvent(type="candidate.ready", correlation_id="diagnosis-trace", payload={
        "workflow_run_id": "trace-1", "pdd_id": "F-11111111", "rework_of": source.id,
        "affected_task_ids": ["T-OLD"],
    }))
    ready = writer.append(ZfEvent(type="task_map.ready", correlation_id="trace-1", payload={
        "workflow_run_id": "trace-1", "rework_of": source.id,
        "rework_summary": {"gap_tasks": [{"task_id": "T-NEW", "supersedes_task_ids": ["T-OLD"]}]},
    }))
    writer.append(ZfEvent(type="task.pipeline.generation.admitted", correlation_id="trace-1", payload={
        "workflow_run_id": "trace-1", "schema_version": "task-pipeline-generation.v1",
        "trigger_event_id": ready.id, "task_ids": ["T-NEW"],
    }))
    runtime.run_once(events=[trigger])
    runtime.run_once(events=[])
    assert not transport.sent
    assert sum(e.type == "fanout.cancelled" for e in log.read_all()) == 1
    assert not any(e.type in {"fanout.child.dispatched", "workflow.operation.requested"} for e in log.read_all())
