from __future__ import annotations

from dataclasses import replace

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.reader_fanout_recovery_snapshot import build_fanout_currentness
from tests.test_fanout_hardening import _FlakyTransport, _state_with_transport


def _events():
    started = ZfEvent(type="fanout.started", correlation_id="run-1", payload={
        "fanout_id": "reader-1", "stage_id": "verify", "topology": "fanout_reader",
        "pdd_id": "P-1", "trace_id": "run-1", "target_ref": "candidate/P-1",
        "trigger_payload": {"candidate_head_commit": "a" * 40},
    })
    failure = ZfEvent(type="test.failed", correlation_id="run-1", payload={
        "workflow_run_id": "run-1", "candidate_head_commit": "a" * 40,
    })
    rework = ZfEvent(type="task_map.ready", origin="kernel", correlation_id="run-1", payload={
        "source": "run_manager_candidate_rework", "workflow_run_id": "run-1",
        "candidate_head_commit": "a" * 40, "resume_checkpoint_ref": "checkpoint-1",
        "rework_of": failure.id, "rework_source": "test.failed",
    })
    return started, failure, rework


def test_exact_candidate_rework_fences_cached_and_direct_queries(tmp_path):
    _, log, _, orch = _state_with_transport(tmp_path, _FlakyTransport(alive=False))
    rows = _events()
    for event in rows:
        EventWriter(log).append(event)
    expected = ("candidate_superseded_by_controlled_rework", rows[-1].id)
    assert orch._fanout_identity_stale_reason("reader-1") == expected
    assert build_fanout_currentness(list(rows))["reader-1"] == expected


@pytest.mark.parametrize("change", ["other_run", "other_candidate", "missing_source",
                                   "missing_checkpoint", "worker_origin", "no_rework"])
def test_unproven_rework_does_not_cancel_reader(tmp_path, change):
    _, log, _, orch = _state_with_transport(tmp_path, _FlakyTransport(alive=False))
    started, failure, rework = _events()
    payload = dict(rework.payload)
    if change == "other_run":
        payload["workflow_run_id"] = "run-2"
    elif change == "other_candidate":
        payload["candidate_head_commit"] = "b" * 40
    elif change == "missing_source":
        payload["rework_of"] = "absent"
    elif change == "missing_checkpoint":
        payload.pop("resume_checkpoint_ref")
    elif change == "no_rework":
        payload.pop("source")
    rework = replace(rework, payload=payload, origin="worker" if change == "worker_origin" else "kernel")
    rows = [started, failure, rework]
    for event in rows:
        EventWriter(log).append(event)
    assert orch._fanout_identity_stale_reason("reader-1") == ("", "")
    assert build_fanout_currentness(rows)["reader-1"] == ("", "")


def test_controlled_rework_closes_pending_reader_before_dispatch(tmp_path):
    transport = _FlakyTransport(alive=False)
    _, log, _, orch = _state_with_transport(tmp_path, transport)
    trigger = ZfEvent(type="candidate.ready", correlation_id="run-1", payload={
        "pdd_id": "P-1", "candidate_head_commit": "a" * 40,
    })
    orch.run_once(events=[trigger])
    started = next(e for e in log.read_all() if e.type == "fanout.started")
    _, failure, rework = _events()
    for event in [failure, rework]:
        EventWriter(log).append(event)
    transport.alive = True
    orch.run_once(events=[])
    cancelled = [e for e in log.read_all() if e.type == "fanout.cancelled"
                 and e.payload.get("fanout_id") == started.payload["fanout_id"]]
    assert len(cancelled) == 1
    assert cancelled[0].payload["superseded_by"] == rework.id
    assert not transport.sent
    orch.run_once(events=[])
    assert len([e for e in log.read_all() if e.type == "fanout.cancelled"]) == 1


def test_rework_stops_current_reader_operation_and_rejects_late_result(tmp_path):
    import json
    from zf.runtime.workflow_operation import WorkflowOperationService, load_workflow_operation

    class Transport(_FlakyTransport):
        def __init__(self):
            super().__init__(alive=True)
            self.stopped = []

        def terminate(self, role):
            self.stopped.append(role)
            self.alive = False

    transport = Transport()
    state, log, _, orch = _state_with_transport(tmp_path, transport)
    orch.run_once(events=[ZfEvent(type="candidate.ready", correlation_id="run-1", payload={
        "pdd_id": "P-1", "candidate_head_commit": "a" * 40,
    })])
    started = next(e for e in log.read_all() if e.type == "fanout.started")
    fanout_id = started.payload["fanout_id"]
    manifest_path = state / "fanouts" / fanout_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    child = manifest["children"][0]
    service = WorkflowOperationService(state_dir=state, event_log=log, event_writer=EventWriter(log))
    operation = service.ensure_operation(
        workflow_run_id="run-1", operation_id="reader-operation", operation_type="fanout_reader_child",
        request={"stage_id": "review-candidate"}, parent_stage_id="review-candidate", role_instance="review-a",
    )
    service.mark_started(operation_id=operation.operation_id, request_hash=operation.request_hash,
                         workflow_run_id="run-1", role_instance="review-a",
                         active_attempt_id="attempt-1", dispatch_id=child["run_id"])
    child.update(operation_id=operation.operation_id, request_hash=operation.request_hash)
    manifest_path.write_text(json.dumps(manifest))
    _, failure, rework = _events()
    for event in [failure, rework]:
        EventWriter(log).append(event)
    orch.run_once(events=[])
    assert load_workflow_operation(log, operation.operation_id)["status"] == "cancelled"
    assert transport.stopped == ["review-a"]
    assert orch._last_worker_state["review-a"] == "idle"
    late = ZfEvent(type="review.approved", actor="review-a", correlation_id="run-1", payload={
        "fanout_id": fanout_id, "child_id": child["child_id"], "run_id": child["run_id"],
        "status": "approved", "report": {"status": "passed", "summary": "late result", "findings": []},
    })
    orch.run_once(events=[late])
    assert any(e.type == "fanout.child.stale_completion" and e.payload.get("result_event_id") == late.id
               for e in log.read_all())
    assert not any(e.type == "fanout.child.completed" and e.payload.get("fanout_id") == fanout_id
                   for e in log.read_all())
    assert transport.stopped == ["review-a"]


def test_rework_fence_leaves_new_candidate_and_writer_current():
    started, failure, rework = _events()
    new_candidate = replace(started, id="new-reader-start", payload={
        **started.payload, "fanout_id": "reader-new", "target_ref": "candidate/P-1-new",
        "trigger_payload": {"candidate_head_commit": "b" * 40},
    })
    writer = replace(started, id="writer-start", payload={
        **started.payload, "fanout_id": "writer-1", "topology": "fanout_writer_scoped",
    })
    current = build_fanout_currentness([started, failure, rework, new_candidate, writer])
    assert current["reader-1"] == ("candidate_superseded_by_controlled_rework", rework.id)
    assert current["reader-new"] == ("", "")
    assert current["writer-1"] == ("", "")


def test_goal_compat_projection_cannot_fence_recovery_reader():
    started, failure, rework = _events()
    failure.type = "judge.failed"
    failure.payload.update(authority="compat_projection", verdict="blocked")
    rework.payload["rework_source"] = "judge.failed"
    current = build_fanout_currentness([started, failure, rework])
    assert current["reader-1"] == ("", "")
