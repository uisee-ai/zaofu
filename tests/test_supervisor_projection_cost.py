"""Regression budgets for repeated read-only projection work."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import zf.runtime.automation_projection as automation
import zf.runtime.fanout_identity as identity
from zf.autoresearch.failure_signals import collect_failure_signals
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.kanban_projection import KANBAN_COLUMN_OPTIONS, kanban_column_projection
from zf.core.task.lifecycle import derive_phase
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def _dispatch(task_id: str, dispatch: str, generation: str, stage: str = "impl") -> ZfEvent:
    return ZfEvent(type="task.pipeline.stage.dispatched", origin="kernel", task_id=task_id,
                   payload={"workflow_run_id": "run", "task_map_generation": generation,
                            "operation_generation": 2, "task_pipeline_stage": stage,
                            "dispatch_id": dispatch})


def test_automation_board_keeps_scope_without_replaying_unrelated_events(tmp_path, monkeypatch):
    tasks = [
        Task(id="active", status="in_progress", active_dispatch_id="verify-new"),
        Task(id="legacy", status="in_progress"),
        Task(id="pending", status="backlog"),
    ]
    store = TaskStore(tmp_path / "kanban.json")
    for task in tasks:
        store.add(task)
    events = [_dispatch("active", "old", "old"),
              ZfEvent(type="verify.passed", task_id="active"),
              _dispatch("active", "impl-new", "new"),
              *[ZfEvent(type="agent.text", payload={"workflow_run_id": "other"})
                for _ in range(300)],
              ZfEvent(type="review.approved", task_id="unrelated"),
              ZfEvent(type="verify.failed", task_id="active",
                      payload={"task_map_generation": "old"}),
              _dispatch("active", "verify-new", "new", "verify"),
              ZfEvent(type="dev.build.done", task_id="legacy")]
    ready = automation._ready_ids(store)

    def reference():
        counts = Counter(kanban_column_projection(
            task, phase=derive_phase(task, events), ready=task.id in ready,
        ).label for task in tasks)
        return {label: counts.get(label, 0) for label in KANBAN_COLUMN_OPTIONS}

    expected = reference()
    calls = []
    def counted(task, selected):
        selected = list(selected)
        calls.append((task.id, selected))
        return derive_phase(task, selected)
    monkeypatch.setattr(automation, "derive_phase", counted)
    first = automation.project_automations(tmp_path, project_id="p", events=events)
    assert all(item["outputs"][0]["board_counts"] == expected for item in first["items"])
    assert calls
    assert all(all(event.task_id == task_id for event in selected)
               for task_id, selected in calls)
    # A later invocation sees new facts; no cache survives between snapshots.
    events.append(ZfEvent(type="test.passed", task_id="active",
                         payload={"workflow_run_id": "run", "task_map_generation": "new",
                                  "operation_generation": 2}))
    expected = reference()
    second = automation.project_automations(tmp_path, project_id="p", events=events)
    assert all(item["outputs"][0]["board_counts"] == expected for item in second["items"])


def _started(fanout_id: str) -> ZfEvent:
    return ZfEvent(type="fanout.started", payload={
        "fanout_id": fanout_id, "stage_id": "review", "topology": "fanout_reader",
        "target_ref": "candidate/T", "pdd_id": "P",
        "expected_children": [{"child_id": "reader"}],
    })


def _failed(fanout_id: str) -> ZfEvent:
    return ZfEvent(id=f"failure-{fanout_id}", type="fanout.timed_out",
                   payload={"fanout_id": fanout_id, "pending_children": ["reader"]})


def test_failure_collector_builds_one_identity_snapshot_and_observes_supersession(tmp_path, monkeypatch):
    log = EventLog(tmp_path / "events.jsonl")
    for event in [_started("old"), _failed("old"), _started("new"), _failed("new"),
                  _failed("legacy"),
                  *[ZfEvent(type="agent.text", payload={"fanout_id": "new"}) for _ in range(30)]]:
        log.append(event)
    original = identity.build_fanout_identity_projection
    calls = []
    def counted(events):
        rows = list(events)
        calls.append(len(rows))
        return original(rows)
    monkeypatch.setattr(identity, "build_fanout_identity_projection", counted)
    first = collect_failure_signals(tmp_path)
    event_ids = {event_id for signal in first if signal.category == "fanout_runtime_failure"
                 for event_id in signal.event_ids}
    assert "failure-old" not in event_ids
    assert {"failure-new", "failure-legacy"} <= event_ids
    assert len(calls) == 1
    calls.clear()
    log.append(_started("successor"))
    second = collect_failure_signals(tmp_path)
    event_ids = {event_id for signal in second if signal.category == "fanout_runtime_failure"
                 for event_id in signal.event_ids}
    assert "failure-new" not in event_ids
    assert "failure-legacy" in event_ids
    assert len(calls) == 1
