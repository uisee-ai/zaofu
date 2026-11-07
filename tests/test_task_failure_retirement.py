"""Task failure diagnostics retire only on exact later kernel terminal facts."""
from dataclasses import replace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.run_manager import _pending_semantic_event_actions, build_run_manager_projection


def failure(kind="task.attempt.failed", task="T1", run="R1"):
    return ZfEvent(type=kind, task_id=task, origin="kernel", correlation_id=run,
                   payload={"task_id": task, "workflow_run_id": run, "run_id": run})


def terminal(kind="task.done", task="T1", run="R1", origin="kernel"):
    payload = {"task_id": task, "workflow_run_id": run}
    if kind == "task.superseded":
        # Actual writer adoption carries Run identity in correlation_id.
        payload = {"source": "writer_task_map_adoption", "status": "cancelled",
                   "superseded_by_task_map_ref": "artifacts/task-map-v2.json"}
    return ZfEvent(type=kind, task_id=task, origin=origin, correlation_id=run, payload=payload)


@pytest.mark.parametrize("kind", ["task.attempt.failed", "task.pipeline.verify.failed"])
@pytest.mark.parametrize("ending", ["task.done", "task.superseded"])
def test_later_kernel_terminal_retires_exact_task_failure(kind, ending):
    assert _pending_semantic_event_actions([failure(kind), terminal(ending)]) == []


@pytest.mark.parametrize("ending", [
    terminal(task="T2"), terminal(run="R2"), terminal(origin="worker"),
    terminal(origin="external"), terminal(origin=""), terminal(run=""),
    terminal("dev.build.done"), terminal("task.attempt.succeeded"),
    terminal("task_map.ready"), terminal("fanout.started"),
    terminal("verify.passed", task="T2"),
    replace(terminal("task_map.ready"), payload={"run_id": "R1"}),
    replace(terminal("verify.passed", task="T2"), payload={"run_id": "R1"}),
    replace(terminal("task.superseded"), payload={"status": "cancelled"}),
    replace(terminal(), payload={"task_id": "T2", "workflow_run_id": "R1"}),
])
def test_unrelated_or_unproven_progress_cannot_close_failure(ending):
    failed = failure()
    actions = _pending_semantic_event_actions([failed, ending])
    assert len(actions) == 1
    assert actions[0]["source_event_ids"] == [failed.id]


def test_terminal_before_failure_does_not_hide_new_failure():
    assert len(_pending_semantic_event_actions([terminal(), failure()])) == 1


def test_new_failure_after_terminal_remains_visible():
    old, new = failure(), failure()
    actions = _pending_semantic_event_actions([old, terminal(), new])
    assert len(actions) == 1
    assert actions[0]["source_event_ids"] == [new.id]


def test_missing_run_cannot_close_on_task_id_alone():
    assert len(_pending_semantic_event_actions([failure(run=""), terminal()])) == 1


def test_old_diagnoses_do_not_fill_bounded_queue_before_current_failure():
    events = []
    for i in range(12):
        events.extend([failure(task=f"OLD-{i}"), terminal(task=f"OLD-{i}")])
    current = failure(task="CURRENT")
    actions = _pending_semantic_event_actions([*events, current])
    assert [a["source_event_ids"] for a in actions] == [[current.id]]


def test_run_manager_projection_uses_task_terminal_retirement(tmp_path):
    from tests.test_run_manager import _config, _state
    state, log, _ = _state(tmp_path)
    for event in [failure(), terminal(), failure(task="CURRENT")]:
        log.append(event)
    projection = build_run_manager_projection(state, events=log.read_all(), config=_config())
    actions = [a for a in projection["pending_actions"] if a.get("failure_class") == "task_attempt_failed"]
    assert [a["task_id"] for a in actions] == ["CURRENT"]
