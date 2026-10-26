import json
from types import SimpleNamespace

import pytest

from zf.core.events import EventWriter, ZfEvent
from tests.test_writer_fanout_runtime import _approval_orch, _approval_start


def _request(orch, log, *, payload=None, tasks=None, trace="run-1"):
    event = EventWriter(log).append(ZfEvent(
        type="task_map.ready", correlation_id=trace,
        payload={"pdd_id": "PDD-1", **(payload or {})},
    ))
    loaded = SimpleNamespace(
        pdd_id="PDD-1", task_map_ref="", task_items=tasks or [
            {"task_id": "T-1", "validation": {"timeout_seconds": 180}},
        ],
    )
    assert not orch._plan_approval_satisfied(
        stage_id="impl", trigger_event=event, loaded=loaded, trace_id=trace,
    )
    return event, loaded


@pytest.mark.parametrize("change", ["package", "generation", "run", "contract", "plan_body"])
def test_changed_plan_is_reviewable_with_same_task_ids(tmp_path, change):
    state, log, orch = _approval_orch(tmp_path, enabled=True)
    payload = {"plan_artifact_package_digest": "a" * 64,
               "task_map_generation": "generation-1"}
    if change == "plan_body":
        payload = {"plan_artifact_ref": "artifacts/plan.md"}
        (state / "artifacts/plan.md").write_text("# Original plan\n")
    _request(orch, log, payload=payload)
    kwargs = {"payload": dict(payload)}
    if change == "package":
        kwargs["payload"]["plan_artifact_package_digest"] = "b" * 64
    elif change == "generation":
        kwargs["payload"]["task_map_generation"] = "generation-2"
    elif change == "run":
        kwargs["trace"] = "run-2"
    elif change == "contract":
        kwargs["tasks"] = [{"task_id": "T-1", "validation": {"timeout_seconds": 900}}]
    else:
        (state / "artifacts/plan.md").write_text("# Revised plan\n")
    second, _ = _request(orch, log, **kwargs)
    requested = [e for e in log.read_all() if e.type == "plan.approval.requested"]
    assert len(requested) == 2
    assert requested[-1].payload["plan_id"] == second.id
    assert not any(e.type in {"plan.approved", "fanout.started"} for e in log.read_all())


def test_same_candidate_stays_deduplicated_on_replay(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    _request(orch, log)
    duplicate, loaded = _request(orch, log)
    for _ in range(2):
        assert not orch._plan_approval_satisfied(
            stage_id="impl", trigger_event=duplicate, loaded=loaded, trace_id="run-1",
        )
    assert len([e for e in log.read_all() if e.type == "plan.approval.requested"]) == 1
    assert len([e for e in log.read_all() if e.type == "plan.minting.suppressed"]) == 1


def test_legacy_suppression_does_not_permanently_hide_new_candidate(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    event = EventWriter(log).append(ZfEvent(type="task_map.ready", payload={"pdd_id": "PDD-1"}))
    EventWriter(log).append(ZfEvent(type="plan.minting.suppressed", payload={
        "plan_id": event.id, "duplicate_of": "old-plan", "plan_fingerprint": "old-task-id-hash",
    }))
    loaded = SimpleNamespace(pdd_id="PDD-1", task_map_ref="", task_items=[{"task_id": "T-1"}])
    for _ in range(2):
        assert not orch._plan_approval_satisfied(
            stage_id="impl", trigger_event=event, loaded=loaded, trace_id="run-1",
        )
    assert len([e for e in log.read_all() if e.type == "plan.approval.requested"]) == 1
    assert not any(e.type == "plan.approved" for e in log.read_all())


def test_writer_entrypoint_requests_review_for_changed_contract(tmp_path):
    state, log, orch = _approval_orch(tmp_path, enabled=True)
    _approval_start(orch, log)
    path = state / "artifacts/F-11111111/task_map.json"
    task_map = json.loads(path.read_text())
    task_map["tasks"][0]["allowed_paths"] = ["revised.txt"]
    path.write_text(json.dumps(task_map))
    _approval_start(orch, log)
    assert len([e for e in log.read_all() if e.type == "plan.approval.requested"]) == 2
    assert not any(e.type == "fanout.started" for e in log.read_all())


def test_recovery_replays_previously_suppressed_rework_through_writer_gate(tmp_path):
    _, log, orch = _approval_orch(tmp_path, enabled=True)
    original = _approval_start(orch, log)
    writer = EventWriter(log)
    revised = writer.append(ZfEvent(
        type="task_map.ready", correlation_id="trace-1",
        payload={"pdd_id": "F-11111111", "rework_of": "integration-failure",
                 "task_map_generation": "new-generation"},
    ))
    writer.append(ZfEvent(type="plan.minting.suppressed", payload={
        "plan_id": revised.id, "duplicate_of": original.id,
        "plan_fingerprint": "legacy-task-id-hash",
    }))
    for _ in range(2):
        orch._resume_unstarted_rework_task_maps(log.read_all())
    requested = [e for e in log.read_all() if e.type == "plan.approval.requested"]
    assert [e.payload["plan_id"] for e in requested] == [original.id, revised.id]
    assert not any(e.type in {"plan.approved", "fanout.started"} for e in log.read_all())
