"""Recovery batches resolve a complete event snapshot once without stale scope."""
from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.runtime import escalation_terminal, run_scope
from zf.runtime.workflow_resume import _reader_stage_replan_checkpoints
from tests.test_reader_replan_index import config, event


def count_aliases(monkeypatch):
    original = run_scope.run_aliases
    calls = []

    def counted(events):
        calls.append(True)
        return original(events)

    monkeypatch.setattr(run_scope, "run_aliases", counted)
    monkeypatch.setattr(escalation_terminal, "run_aliases", counted, raising=False)
    return calls


@pytest.mark.parametrize("case", ["empty", "single", "multiple", "late-alias", "conflict"])
def test_precomputed_scope_preserves_existing_resolution(case):
    rows = [] if case == "empty" else [event("run.goal.started", "A")]
    if case in {"multiple", "conflict"}:
        rows.append(event("run.goal.started", "B"))
    unscoped = ZfEvent(type="human.escalate")
    alias = ZfEvent(type="human.escalate", correlation_id="trace-A")
    rows += [unscoped, alias]
    if case == "late-alias":
        rows.append(ZfEvent(type="task.dispatched", correlation_id="trace-A",
                           payload={"workflow_run_id": "A"}))
    if case == "conflict":
        rows.append(ZfEvent(type="task.dispatched", correlation_id="A",
                           payload={"workflow_run_id": "B"}))
    expected = [run_scope.resolve_run_for_event(rows, row) for row in rows]
    aliases = run_scope.run_aliases(rows)
    assert [run_scope.resolve_run_for_event(rows, row, _aliases=aliases)
            for row in rows] == expected


def test_escalation_batch_reuses_scope_and_refreshes_next_call(tmp_path: Path, monkeypatch):
    calls = count_aliases(monkeypatch)
    rows = [event("run.goal.started", "A"), event("run.goal.started", "B")]
    rows += [event("human.escalate", "A", reason="stage replan cap exhausted")
             for _ in range(5)]
    rows += [event("human.escalate", "B", reason="stage replan cap exhausted"),
             event("human.resolved", "A")]
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    assert escalation_terminal.converge_unrecoverable_escalations(rows, writer=writer) == 1
    emitted = writer.event_log.read_all()
    assert [row.payload["run_id"] for row in emitted] == ["B"]
    assert len(calls) == 1
    rows.append(event("human.resolved", "B"))
    assert escalation_terminal.converge_unrecoverable_escalations(rows, writer=writer) == 0
    assert len(calls) == 2
    assert writer.event_log.read_all() == emitted


def test_reader_checkpoint_batch_reuses_scope_and_observes_new_success(monkeypatch):
    calls = count_aliases(monkeypatch)
    rows = []
    for run in ["A", "B", "C"]:
        rows += [event("run.goal.started", run),
                 event("issue.requested", run, target_ref="requirements.md"),
                 event("issue.triage.failed", run)]
    first = _reader_stage_replan_checkpoints(rows, [], config())
    assert {r.workflow_run_id for r in first} == {"A", "B", "C"}
    assert len(calls) == 1
    rows.append(event("issue.triage.done", "A"))
    second = _reader_stage_replan_checkpoints(rows, [], config())
    assert {r.workflow_run_id for r in second} == {"B", "C"}
    assert len(calls) == 2
    assert [r.__dict__ for r in second] == [r.__dict__ for r in first if r.workflow_run_id != "A"]


def test_no_recovery_candidate_needs_no_alias_scan(tmp_path, monkeypatch):
    calls = count_aliases(monkeypatch)
    rows = [event("run.goal.started", "A"), event("worker.heartbeat", "A")]
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    assert escalation_terminal.converge_unrecoverable_escalations(rows, writer=writer) == 0
    assert _reader_stage_replan_checkpoints(rows, [], config()) == []
    assert not calls
