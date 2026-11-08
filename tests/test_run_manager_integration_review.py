"""Actual blocked integration facts must reach the available resident diagnosis owner."""

import json

import pytest

from tests.test_run_manager_diagnosis_consumer import _setup, _tick
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.run_manager import _pending_semantic_event_actions


def seed_review(writer, *, task_id="T1", operation="integration-1", generation=1, map_id="map-1"):
    identity = dict(task_id=task_id, workflow_run_id="run-1", task_map_generation=map_id,
                    operation_generation=generation, task_pipeline_stage="integration",
                    operation_id=operation, request_hash="hash-" + operation)
    writer.append(ZfEvent(type="task.pipeline.generation.admitted", origin="kernel", payload={
        **identity, "task_ids": [task_id], "schema_version": "task-pipeline-generation.v1",
    }))
    writer.append(ZfEvent(type="workflow.operation.requested", task_id=task_id, origin="kernel", payload=identity))
    writer.append(ZfEvent(type="workflow.operation.blocked", task_id=task_id, origin="kernel", payload={
        **identity, "reason": "candidate_incremental_failed:CandidateIncrementalError",
    }))
    source = writer.append(ZfEvent(type="integration.queue.needs_review", task_id=task_id, origin="kernel", payload={
        **{k: v for k, v in identity.items() if k != "request_hash"},
        "reason": "CandidateIncrementalError: rolling smoke left candidate worktree dirty",
    }))
    return identity, source


def setup_review(tmp_path, monkeypatch):
    state, log, writer, cfg, calls = _setup(tmp_path, monkeypatch)
    path = state / "projections/supervisor/snapshot.json"
    body = json.loads(path.read_text()); body["attention_items"] = []
    path.write_text(json.dumps(body))
    TaskStore(state / "kanban.json").add(Task(id="T1", status="in_progress"))
    identity, source = seed_review(writer)
    return state, log, writer, cfg, calls, identity, source


@pytest.mark.mock_e2e
@pytest.mark.parametrize("old_autoresearch", [False, True])
def test_integration_review_reaches_resident_without_duplicate_block_diagnosis(tmp_path, monkeypatch, old_autoresearch):
    state, log, writer, cfg, calls, identity, source = setup_review(tmp_path, monkeypatch)
    if old_autoresearch:
        writer.emit("run.manager.autoresearch.requested", payload={
            "request_id": "old-unrelated", "checkpoint_id": "other-fault",
        })
    for _ in range(2):
        _tick(state, log, writer, cfg, tmp_path)
    events = log.read_all()
    prompts = [e for e in events if e.type == "run.manager.resident.prompted" and e.payload.get("reprompt")]
    assert len(prompts) == 1
    assert prompts[0].payload["expected_output"] == ["run.manager.agent.recommendation"]
    context = json.loads(next((state / "diagnostics/run-manager").glob("*/context.json")).read_text())
    action = context["action"]
    assert action["target_operation_id"] == identity["operation_id"]
    assert action["target_request_hash"] == identity["request_hash"]
    assert action["task_map_generation"] == "map-1"
    assert action["source_event_id"] == source.id
    assert action["failure_class"] == "candidate_integration_needs_review"
    assert "rolling smoke left candidate worktree dirty" in action["summary"]
    assert len([e for e in events if e.type == "run.manager.autoresearch.requested"]) == int(old_autoresearch)
    assert not any(e.type in {"task.done", "workflow.operation.superseded", "run.manager.action.effect.passed"} for e in events)
    assert TaskStore(state / "kanban.json").get("T1").status == "in_progress"


@pytest.mark.parametrize("terminal", ["superseded", "settled", "new_map", "new_impl"])
def test_recovered_or_replaced_integration_removes_old_diagnostic(tmp_path, monkeypatch, terminal):
    state, log, writer, cfg, calls, identity, source = setup_review(tmp_path, monkeypatch)
    assert len(_pending_semantic_event_actions(log.read_all())) == 1
    if terminal == "new_map":
        writer.append(ZfEvent(type="task.pipeline.generation.admitted", origin="kernel", payload={
            **identity, "task_ids": ["T1"], "schema_version": "task-pipeline-generation.v1",
            "task_map_generation": "map-2",
        }))
    elif terminal == "new_impl":
        writer.append(ZfEvent(type="workflow.operation.requested", task_id="T1", origin="kernel", payload={
            **identity, "operation_id": "impl-2", "task_pipeline_stage": "impl", "operation_generation": 2,
        }))
    else:
        writer.append(ZfEvent(type="workflow.operation." + terminal, task_id="T1", origin="kernel", payload=identity))
    assert not _pending_semantic_event_actions(log.read_all())


def test_ignored_late_cancellation_cannot_hide_existing_block(tmp_path, monkeypatch):
    _, log, writer, _, _, identity, source = setup_review(tmp_path, monkeypatch)
    # The operation reducer rejects cancellation of an already terminal block.
    writer.append(ZfEvent(type="workflow.operation.cancelled", task_id="T1", origin="kernel", payload=identity))
    assert _pending_semantic_event_actions(log.read_all())[0]["source_event_id"] == source.id


def test_other_task_progress_cannot_hide_failure_and_new_operation_is_distinct(tmp_path, monkeypatch):
    _, log, writer, _, _, identity, source = setup_review(tmp_path, monkeypatch)
    first = _pending_semantic_event_actions(log.read_all())[0]
    writer.emit("task.done", task_id="OTHER", payload={"workflow_run_id": "run-1"})
    assert _pending_semantic_event_actions(log.read_all())[0]["source_event_id"] == source.id
    writer.append(ZfEvent(type="workflow.operation.superseded", task_id="T1", origin="kernel", payload=identity))
    _, newer = seed_review(writer, operation="integration-2", generation=2)
    second = _pending_semantic_event_actions(log.read_all())
    assert len(second) == 1 and second[0]["source_event_id"] == newer.id
    assert second[0]["fingerprint"] != first["fingerprint"]


def test_paused_diagnosis_does_not_send_to_resident(tmp_path, monkeypatch):
    state, log, writer, cfg, calls, *_ = setup_review(tmp_path, monkeypatch)
    writer.emit("dispatch.paused", actor="operator")
    _tick(state, log, writer, cfg, tmp_path)
    assert not any(c[:2] == ["tmux", "send-keys"] for c in calls)


def test_current_review_requires_canonical_bound_event(tmp_path, monkeypatch):
    _, log, writer, _, _, identity, source = setup_review(tmp_path, monkeypatch)
    events = [e for e in log.read_all() if e.id != source.id]
    events.append(ZfEvent(type="integration.queue.needs_review", task_id="T1", origin="worker", payload=identity))
    pending = _pending_semantic_event_actions(events)
    assert not any(a["failure_class"] == "candidate_integration_needs_review" for a in pending)
    assert any(a["failure_class"] == "workflow_operation_divergent" for a in pending)
