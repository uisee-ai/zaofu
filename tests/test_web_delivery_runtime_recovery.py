"""Delivery fallback cards must reflect ordered runtime recovery facts."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.web.server import create_app


@pytest.fixture
def delivery(tmp_path):
    state = tmp_path / "runtime-state"
    state.mkdir()
    store = TaskStore(state / "kanban.json")
    log = EventLog(state / "events.jsonl")
    for suffix in ("current", "other"):
        store.add(Task(
            id=f"task-{suffix}", title=suffix, status="in_progress",
            contract=TaskContract(feature_id="", owner_role="dev"),
        ))
        for event_type in ("fanout.started", "fanout.cancelled", "run.goal.blocked"):
            log.append(ZfEvent(
                type=event_type, actor="zf-cli", origin="kernel",
                task_id=f"task-{suffix}", correlation_id=f"trace-{suffix}",
                payload={"fanout_id": f"fanout-{suffix}", "run_id": f"trace-{suffix}"},
            ))
    with TestClient(create_app(state)) as client:
        yield state, log, client


def _rows(client):
    response = client.get("/api/state")
    assert response.status_code == 200
    data = response.json()
    assert data["features"] == data["delivery_features"]
    return {row["id"]: row["status"] for row in data["delivery_features"]}


def _append(log, event_type, **payload):
    log.append(ZfEvent(
        type=event_type, actor="zf-cli", origin="kernel",
        task_id="task-current", correlation_id="trace-current",
        payload={"fanout_id": "fanout-current", "run_id": "trace-current", **payload},
    ))


@pytest.mark.parametrize("recovered_status", ["active", "running"])
def test_delivery_refreshes_only_the_recovered_target(delivery, recovered_status):
    state, log, client = delivery
    rows = _rows(client)
    assert rows["fanout-current"] == "cancelled"
    assert rows["trace-current"] == "blocked"
    _append(log, "run.goal.updated", status=recovered_status)
    _append(log, "fanout.aggregate.started", recovery_kind="operator_budget_amendment")
    before = (state / "events.jsonl").read_bytes()

    rows = _rows(client)

    assert rows["fanout-current"] == "active"
    assert rows["trace-current"] == "active"
    assert rows["fanout-other"] == "cancelled"
    assert rows["trace-other"] == "blocked"
    assert (state / "events.jsonl").read_bytes() == before
    # Later terminal facts still win; recovery is not a permanent override.
    _append(log, "fanout.cancelled")
    _append(log, "run.goal.blocked", status="blocked")
    rows = _rows(client)
    assert rows["fanout-current"] == "cancelled"
    assert rows["trace-current"] == "blocked"


@pytest.mark.parametrize("status", [None, "blocked", "unrecognized"])
def test_goal_metadata_update_does_not_imply_recovery(delivery, status):
    _state, log, client = delivery
    _append(log, "run.goal.updated", **({"status": status} if status else {}))
    assert _rows(client)["trace-current"] == "blocked"


def test_goal_completion_is_not_reopened_by_active_update(delivery):
    _state, log, client = delivery
    _append(log, "run.goal.completed")
    _append(log, "run.goal.updated", status="active")
    assert _rows(client)["trace-current"] == "done"


@pytest.mark.parametrize("status,expected", [
    ("failed", "blocked"), ("timed_out", "blocked"),
    ("cancelled", "cancelled"), ("completed", "done"), (None, "done"),
])
def test_aggregate_completion_preserves_its_actual_outcome(delivery, status, expected):
    _state, log, client = delivery
    _append(log, "fanout.aggregate.completed", **({"status": status} if status else {}))
    assert _rows(client)["fanout-current"] == expected
