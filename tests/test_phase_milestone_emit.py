"""Integration test for kernel phase-milestone emission (doc 69 S-f).

Drives `emit_phase_milestones` against a real FeatureStore / TaskStore /
EventLog in a tmp dir — verifies the kernel emits delivery.phase.* once per
(feature, phase) transition and is idempotent across sweeps.
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.phase_milestone_emit import emit_phase_milestones

_MS = {"delivery.phase.started", "delivery.phase.evaluated", "delivery.phase.completed"}


def _writer(log: EventLog) -> EventWriter:
    return EventWriter(log)


def _feature(sd: Path) -> None:
    FeatureStore(sd / "feature_list.json").add(
        Feature(id="F-1", title="api", status="active")
    )


def _passing_phase(tmp_path: Path) -> tuple[Path, TaskStore, EventLog]:
    sd = tmp_path / ".zf"
    sd.mkdir()
    _feature(sd)
    store = TaskStore(sd / "kanban.json")
    store.add(Task(id="T1", title="schema", status="done",
                   contract=TaskContract(feature_id="F-1", phase="impl", wave=1)))
    store.add(Task(id="T2", title="router", status="done",
                   contract=TaskContract(feature_id="F-1", phase="impl", wave=1)))
    log = EventLog(sd / "events.jsonl")
    log.append(ZfEvent(type="judge.passed", task_id="T1"))
    log.append(ZfEvent(type="judge.passed", task_id="T2"))
    return sd, store, log


def test_emits_started_evaluated_completed_for_passed_phase(tmp_path):
    sd, store, log = _passing_phase(tmp_path)
    emitted = emit_phase_milestones(state_dir=sd, task_store=store,
                                    event_log=log, event_writer=_writer(log))
    types = {e.type for e in emitted}
    assert _MS <= types
    for e in emitted:
        assert e.payload["feature_id"] == "F-1"
        assert e.payload["phase_id"] == "impl"
    ev = next(e for e in emitted if e.type == "delivery.phase.evaluated")
    assert ev.payload["completion_rate"] == 1.0
    assert ev.payload["verdict"] == "pass"


def test_idempotent_second_sweep_emits_nothing(tmp_path):
    sd, store, log = _passing_phase(tmp_path)
    first = emit_phase_milestones(state_dir=sd, task_store=store,
                                  event_log=log, event_writer=_writer(log))
    assert first
    log2 = EventLog(sd / "events.jsonl")
    second = emit_phase_milestones(state_dir=sd, task_store=store,
                                   event_log=log2, event_writer=_writer(log2))
    assert second == []


def test_no_emit_without_active_feature(tmp_path):
    # tasks exist but no feature registered → the active-feature guard returns []
    sd = tmp_path / ".zf"
    sd.mkdir()
    store = TaskStore(sd / "kanban.json")
    store.add(Task(id="T1", title="schema", status="done",
                   contract=TaskContract(feature_id="F-1", phase="impl", wave=1)))
    log = EventLog(sd / "events.jsonl")
    log.append(ZfEvent(type="judge.passed", task_id="T1"))
    out = emit_phase_milestones(state_dir=sd, task_store=store,
                                event_log=log, event_writer=_writer(log))
    assert out == []


def test_failed_phase_evaluated_but_not_completed(tmp_path):
    # a single-task phase whose only gate FAILED → verdict 'fail' → evaluated
    # is emitted but completed is withheld.
    sd = tmp_path / ".zf"
    sd.mkdir()
    _feature(sd)
    store = TaskStore(sd / "kanban.json")
    store.add(Task(id="T1", title="schema", status="done",
                   contract=TaskContract(feature_id="F-1", phase="impl", wave=1)))
    log = EventLog(sd / "events.jsonl")
    log.append(ZfEvent(type="judge.failed", task_id="T1"))
    out = emit_phase_milestones(state_dir=sd, task_store=store,
                                event_log=log, event_writer=_writer(log))
    types = {e.type for e in out}
    assert "delivery.phase.evaluated" in types
    assert "delivery.phase.completed" not in types
