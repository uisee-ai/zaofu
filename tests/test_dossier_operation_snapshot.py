"""A Dossier shares projection inputs without dropping operation evidence."""

from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime import goal_dossier, operation_projection, workflow_operation


NOW = datetime(2026, 11, 13, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def _fixture(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(operation_projection, "datetime", _FrozenDatetime)
    state = tmp_path / "state"
    state.mkdir()
    store = TaskStore(state / "kanban.json")
    for task_id in ("T1", "T2"):
        store.add(Task(id=task_id, title=task_id, status="in_progress"))
    (state / "feature_list.json").write_text("[]")
    roles = {
        "first": {"dispatch_id": "shared", "session_id": "session-first"},
        "duplicate": {"dispatch_id": "shared", "session_id": "session-ignored"},
        "second": {"active_dispatch_id": "second", "session_id": "session-two"},
    }
    (state / "role_sessions.yaml").write_text(json.dumps(roles))
    (state / "resume_packets").mkdir()
    (state / "resume_packets/T1.shared.json").write_text("{}")
    events = []

    def emit(kind, task_id=None, **payload):
        event = ZfEvent(
            id=f"evt-{len(events)}", type=kind, task_id=task_id,
            ts=NOW.isoformat(), correlation_id="run-1", origin="kernel",
            payload={"workflow_run_id": "run-1", **payload},
        )
        events.append(event)
        return event

    emit("run.goal.started", goal_id="G1", objective="Deliver both tasks")
    emit("workflow.operation.requested", "T1", operation_id="op-1", request_hash="a" * 64)
    emit("task.dispatched", "T1", dispatch_id="shared", backend="codex")
    emit("worker.progress", "T2", dispatch_id="shared", phase="implement", message="shared evidence")
    emit("worker.progress", active_dispatch_id="shared", message="taskless evidence")
    emit("task.dispatched", "T2", dispatch_id="second")
    emit("agent.text", dispatch_id="shared", active_dispatch_id="second", message="TOKEN=secret-value")
    # Payload task identity must not create a new per-task dispatch discovery.
    payload_only = emit("agent.text", dispatch_id="payload-only")
    payload_only.payload["task_id"] = "T1"
    emit("workflow.call.result.admitted", operation_id="op-1", semantic_verdict="passed",
         control_result_ref={"ref": "results/one.json", "sha256": "b" * 64})
    emit("workflow.operation.settled", operation_id="op-1", request_hash="a" * 64,
         admitted_call_result_ref={"ref": "envelopes/one.json"})
    emit("workflow.operation.requested", "T2", operation_id="op-2", request_hash="c" * 64)
    emit("run.goal.blocked", run_id="run-1", goal_id="G1", reason="owner input", task_ids=["T1", "T2"])
    return state, events, roles


def test_batch_matches_complete_individual_projections(tmp_path, monkeypatch):
    state, events, _roles = _fixture(tmp_path, monkeypatch)
    expected = [
        operation_projection.project_task_operations(state, task, events=events)
        for task in ("T1", "T2", "T1")
    ]

    actual = list(operation_projection.project_task_operations_batch(
        state, ["T1", "T2", "T1"], events=events,
    ))

    assert actual == expected
    first = actual[0]["operations"][0]
    assert first["provider_session_ref"] == "session-first"
    assert [row["event_id"] for row in first["timeline"]] == ["evt-2", "evt-3", "evt-4", "evt-6"]
    assert first["resume_packet_path"].endswith("T1.shared.json")
    assert "secret-value" not in json.dumps(actual)
    assert [row["dispatch_id"] for row in actual[1]["operations"]] == ["shared", "second"]
    workflow = actual[0]["workflow_operations"][0]
    assert workflow["status"] == "settled"
    assert workflow["semantic_verdict"] == "passed"
    assert workflow["admitted_control_result_ref"]["ref"] == "results/one.json"
    workflow["admitted_control_result_ref"]["ref"] = "changed"
    assert actual[2]["workflow_operations"][0]["admitted_control_result_ref"]["ref"] == "results/one.json"
    assert events[8].payload["control_result_ref"]["ref"] == "results/one.json"


def test_real_dossier_caller_loads_roles_and_reduces_operations_once(tmp_path, monkeypatch):
    state, events, _roles = _fixture(tmp_path, monkeypatch)
    with patch.object(yaml, "safe_load", wraps=yaml.safe_load) as roles, patch.object(
        workflow_operation, "reduce_workflow_operations",
        wraps=workflow_operation.reduce_workflow_operations,
    ) as reducer:
        dossier = goal_dossier.build_goal_dossier(state, "run-1", events=events, now=NOW)

    assert dossier["terminal"]["status"] == "blocked"
    assert set(dossier["roadmap"]["task_order"]) == {"T1", "T2"}
    assert [row["operation_id"] for row in dossier["operations"]] == [
        "op-shared", "op-1", "op-second", "op-2",
    ]
    assert roles.call_count == 1
    assert reducer.call_count == 1


def test_complete_dossier_matches_legacy_individual_caller(tmp_path, monkeypatch):
    state, events, _roles = _fixture(tmp_path, monkeypatch)
    actual = goal_dossier.build_goal_dossier(state, "run-1", events=events, now=NOW)

    def individual(state_dir, task_ids, *, events):
        return [operation_projection.project_task_operations(state_dir, task, events=events)
                for task in task_ids]

    monkeypatch.setattr(goal_dossier, "project_task_operations_batch", individual)
    expected = goal_dossier.build_goal_dossier(state, "run-1", events=events, now=NOW)
    assert actual == expected


@pytest.mark.parametrize("roles_text", [None, "broken: [", "[1, 2]", "{}"])
def test_missing_or_invalid_roles_preserves_fallback(tmp_path, monkeypatch, roles_text):
    state, events, _roles = _fixture(tmp_path, monkeypatch)
    path = state / "role_sessions.yaml"
    if roles_text is None:
        path.unlink()
    else:
        path.write_text(roles_text)
    actual = list(operation_projection.project_task_operations_batch(state, ["T1", "T2"], events=events))
    assert actual == [operation_projection.project_task_operations(state, task, events=events)
                      for task in ("T1", "T2")]
    assert actual[0]["operations"][0]["provider_session_ref"] == ""


def test_new_build_observes_event_and_role_changes(tmp_path, monkeypatch):
    state, events, roles = _fixture(tmp_path, monkeypatch)
    first = list(operation_projection.project_task_operations_batch(state, ["T1"], events=events))
    roles["first"]["session_id"] = "session-new"
    (state / "role_sessions.yaml").write_text(json.dumps(roles))
    events.append(ZfEvent(
        id="new", type="worker.progress", task_id=None,
        payload={"dispatch_id": "shared", "message": "new evidence"},
    ))
    events.append(ZfEvent(
        id="new-admission", type="workflow.call.result.admitted",
        payload={"operation_id": "op-1", "semantic_verdict": "rejected"},
    ))
    second = list(operation_projection.project_task_operations_batch(state, ["T1"], events=events))
    assert first[0]["operations"][0]["provider_session_ref"] == "session-first"
    assert second[0]["operations"][0]["provider_session_ref"] == "session-new"
    assert second[0]["operations"][0]["timeline"][-1]["event_id"] == "new"
    assert len(first[0]["operations"][0]["timeline"]) == 4
    assert first[0]["workflow_operations"][0]["semantic_verdict"] == "passed"
    assert second[0]["workflow_operations"][0]["semantic_verdict"] == "rejected"


def test_batch_preserves_empty_task_identity_and_streams_results(tmp_path, monkeypatch):
    state, events, _roles = _fixture(tmp_path, monkeypatch)
    events.append(ZfEvent(type="worker.progress", task_id="", payload={"dispatch_id": "empty"}))
    expected = operation_projection.project_task_operations(state, "", events=events)
    with patch.object(operation_projection, "project_task_operations",
                      wraps=operation_projection.project_task_operations) as project:
        batch = operation_projection.project_task_operations_batch(state, ["", "T1"], events=events)
        assert next(batch) == expected
        assert project.call_count == 1
        assert next(batch)["task_id"] == "T1"
        with pytest.raises(StopIteration):
            next(batch)
    assert list(operation_projection.project_task_operations_batch(state, [], events=events)) == []
