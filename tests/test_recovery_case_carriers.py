"""Recovery facts follow the current fanout and explicit pipeline handoff."""

import pytest

from tests.test_recovery_case_file import _NOW, _ev
from zf.runtime.recovery_case_file import build_case_file


def _case(events):
    return build_case_file(
        events,
        tasks=[{"id": "P", "status": "in_progress", "assigned_to": ""}],
        instance_states={"unrelated": "busy"},
        now_epoch=_NOW.timestamp(),
    )


def _child(kind="failed", child="a", fanout="f1", **payload):
    return _ev(
        "fanout.child." + kind,
        minutes_ago=10,
        task="P",
        fanout_id=fanout,
        child_id=child,
        workflow_run_id="run-1",
        workflow_generation="proposal-1",
        **payload,
    )


def _handoff(**patch):
    payload = {
        "schema_version": "task-pipeline-generation.v1",
        "workflow_run_id": "run-1",
        "generation_id": "pg-1",
        "task_map_generation": "map-1",
        "fanout_id": "f1",
        "pdd_id": "P",
        "task_ids": ["IMPLEMENT"],
        "task_map_admitted_event_id": "admitted-1",
    }
    payload.update(patch)
    return _ev("task.pipeline.generation.admitted", minutes_ago=5, **payload)


def _started(fanout):
    return _ev(
        "fanout.started",
        minutes_ago=20,
        task="P",
        fanout_id=fanout,
        stage_id="plan",
        target_ref="prd.md",
    )


def test_pipeline_handoff_replaces_reader_as_parent_carrier():
    failed, admitted = _child(), _handoff()
    cf = _case([failed, admitted])
    assert cf["contradictions"] == []
    receipt = cf["continuations"][0]
    assert receipt["task"] == "P"
    assert receipt["fanout_id"] == "f1"
    assert receipt["source_event_id"] == failed.id
    assert receipt["generation_admitted_event_id"] == admitted.id
    assert receipt["task_map_generation"] == "map-1"


@pytest.mark.parametrize(
    "patch",
    [
        {"workflow_run_id": "other"},
        {"fanout_id": "other"},
        {"pdd_id": "other"},
        {"schema_version": "unknown"},
        {"generation_id": ""},
        {"task_map_generation": ""},
        {"task_ids": []},
    ],
)
def test_unrelated_or_incomplete_handoff_does_not_hide_orphan(patch):
    assert _case([_child(), _handoff(**patch)])["contradictions"]


def test_old_pipeline_generation_cannot_suppress_current_orphan():
    assert _case(
        [
            _child(),
            _handoff(),
            _handoff(fanout_id="f2", generation_id="pg-2", task_map_generation="map-2"),
        ]
    )["contradictions"]


def test_sibling_inflight_prevents_false_orphan_but_last_terminal_restores_it():
    events = [_child("dispatched", "a"), _child("dispatched", "b"), _child()]
    assert _case(events)["contradictions"] == []
    events.append(_child("failed", "b"))
    assert _case(events)["contradictions"]


def test_closed_fanout_does_not_retain_sibling_carrier():
    events = [
        _child("dispatched", "b"),
        _child(),
        _ev("fanout.timed_out", minutes_ago=1, fanout_id="f1"),
    ]
    assert _case(events)["contradictions"]


def test_late_superseded_failure_does_not_override_current_dispatch():
    events = [
        _started("f1"),
        _child("dispatched"),
        _started("f2"),
        _child("dispatched", fanout="f2"),
        _child(),
    ]
    assert _case(events)["contradictions"] == []


def test_superseded_busy_child_cannot_hide_current_orphan():
    failed = _child(fanout="f2")
    events = [_started("f1"), _child("dispatched"), _started("f2"), failed]
    row = _case(events)["contradictions"][0]
    assert row["evidence"]["source_event_id"] == failed.id
    assert row["evidence"]["fanout_id"] == "f2"
    assert row["evidence"]["workflow_generation"] == "proposal-1"


def test_plan_approval_or_unrelated_busy_worker_is_not_a_pipeline_handoff():
    events = [_child(), _ev("plan.approved", minutes_ago=5, task="P")]
    assert _case(events)["contradictions"]


def test_other_task_in_same_fanout_is_not_this_tasks_carrier():
    events = [
        _child(),
        _ev(
            "fanout.child.dispatched",
            minutes_ago=1,
            task="OTHER",
            fanout_id="f1",
            child_id="other-child",
        ),
    ]
    assert _case(events)["contradictions"]


def test_standard_tick_writes_handoff_without_mutating_task(tmp_path):
    import json
    from tests.test_tick_services import _FakeOrchestrator, _state
    from zf.core.config.schema import ZfConfig
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.tick_services import (
        TickServiceIntervals,
        TickServiceState,
        run_standard_tick_services,
    )

    state_dir = _state(tmp_path)
    (state_dir / "role_sessions.yaml").write_text("{}\n")
    TaskStore(state_dir / "kanban.json").add(
        Task(id="P", title="Parent", status="in_progress")
    )
    original = (state_dir / "kanban.json").read_bytes()
    runtime = _FakeOrchestrator(state_dir, ZfConfig())
    runtime.event_writer.append(_child())
    admitted = runtime.event_writer.append(_handoff())
    run_standard_tick_services(
        runtime,
        state=TickServiceState(),
        now=100,
        intervals=TickServiceIntervals(
            heartbeat_sweep_s=999,
            bug_scan_s=999,
            supervisor_inspection_s=999,
            spine_projection_s=999,
            cost_blackout_check_s=999,
            stillness_audit_s=0,
        ),
    )
    result = json.loads((state_dir / "projections/recovery_case_file.json").read_text())
    assert result["contradictions"] == []
    assert result["continuations"][0]["generation_admitted_event_id"] == admitted.id
    assert (state_dir / "kanban.json").read_bytes() == original
