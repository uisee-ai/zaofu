"""Approval deduplication follows the reviewed version and current blockers."""

import hashlib
from types import SimpleNamespace

from tests.test_writer_fanout_runtime import _approval_orch
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter


def _trigger(package: str) -> ZfEvent:
    return ZfEvent(
        type="task_map.ready",
        payload={
            "pdd_id": "F-11111111",
            "plan_artifact_package_digest": package,
        },
    )


def _check(orch, event):
    return orch._plan_approval_satisfied(
        stage_id="impl",
        trigger_event=event,
        trace_id="run-1",
        loaded=SimpleNamespace(
            pdd_id="F-11111111",
            task_map_ref="map.json",
            task_items=[{"task_id": "TASK-1"}, {"task_id": "TASK-2"}],
        ),
    )


def test_changed_package_requires_its_own_approval_with_same_task_ids(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    first, second = _trigger("a" * 64), _trigger("b" * 64)
    assert _check(orch, first) is False
    assert _check(orch, second) is False
    requests = [e for e in log.read_all() if e.type == "plan.approval.requested"]
    assert [e.payload["plan_id"] for e in requests] == [first.id, second.id]
    assert (
        requests[0].payload["plan_fingerprint"]
        != requests[1].payload["plan_fingerprint"]
    )
    EventWriter(log).emit(
        "plan.approved", actor="operator", payload={"plan_id": first.id}
    )
    assert _check(orch, second) is False


def test_same_package_remains_deduplicated_without_repeated_suppression(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    first, second = _trigger("a" * 64), _trigger("a" * 64)
    assert _check(orch, first) is False
    for _ in range(3):
        assert _check(orch, second) is False
    assert len([e for e in log.read_all() if e.type == "plan.approval.requested"]) == 1
    assert len([e for e in log.read_all() if e.type == "plan.minting.suppressed"]) == 1


def test_suppressed_request_is_reconsidered_after_blocker_decision(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    first, second = _trigger("a" * 64), _trigger("a" * 64)
    _check(orch, first)
    _check(orch, second)
    EventWriter(log).emit(
        "plan.rejected", actor="operator", payload={"plan_id": first.id}
    )
    assert _check(orch, second) is False
    assert any(
        e.type == "plan.approval.requested" and e.payload["plan_id"] == second.id
        for e in log.read_all()
    )


def test_historical_task_only_suppression_does_not_latch_new_package(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    first, second = _trigger("a" * 64), _trigger("b" * 64)
    legacy = hashlib.sha256(b"impl|F-11111111|TASK-1|TASK-2").hexdigest()[:16]
    writer = EventWriter(log)
    writer.emit(
        "plan.approval.requested",
        actor="zf-cli",
        payload={
            **first.payload,
            "plan_id": first.id,
            "plan_fingerprint": legacy,
        },
    )
    writer.emit(
        "plan.minting.suppressed",
        actor="zf-cli",
        payload={
            **second.payload,
            "plan_id": second.id,
            "plan_fingerprint": legacy,
            "duplicate_of": first.id,
        },
    )
    assert _check(orch, second) is False
    requests = [e for e in log.read_all() if e.type == "plan.approval.requested"]
    assert len(requests) == 2
    assert requests[-1].payload["plan_id"] == second.id
