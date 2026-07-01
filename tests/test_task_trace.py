"""LH-5.T1/T2/T5: `zf task trace <task_id>` + causation + metrics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.metrics.collector import MetricsCollector
from zf.core.task.schema import Task, TaskEvidence
from zf.core.task.store import TaskStore


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "kanban.json").write_text("[]\n")
    log = EventLog(sd / "events.jsonl")
    log.append(ZfEvent(type="session.started", actor="zf-cli"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _seed_full_trace(project: Path, task_id: str = "T1") -> list[str]:
    """Seed a realistic task lifecycle; return event.id per stage."""
    store = TaskStore(project / ".zf" / "kanban.json")
    log = EventLog(project / ".zf" / "events.jsonl")

    user_evt = ZfEvent(type="user.message", actor=None,
                       payload={"text": "build auth"})
    log.append(user_evt)
    store.add(Task(id=task_id, title="auth module"))
    created = ZfEvent(type="task.created", actor="zf-cli", task_id=task_id,
                      causation_id=user_evt.id)
    log.append(created)
    dispatched = ZfEvent(type="task.dispatched", actor="orchestrator",
                         task_id=task_id, causation_id=created.id,
                         payload={"role": "dev"})
    log.append(dispatched)
    build_done = ZfEvent(type="dev.build.done", actor="dev", task_id=task_id,
                         causation_id=dispatched.id)
    log.append(build_done)
    approved = ZfEvent(type="review.approved", actor="review",
                       task_id=task_id, causation_id=build_done.id)
    log.append(approved)
    test_passed = ZfEvent(type="test.passed", actor="test",
                          task_id=task_id, causation_id=approved.id)
    log.append(test_passed)
    judge_passed = ZfEvent(type="judge.passed", actor="judge",
                           task_id=task_id, causation_id=test_passed.id)
    log.append(judge_passed)
    store.update(task_id, status="done",
                 evidence=TaskEvidence(commit="abc123"))
    return [user_evt.id, created.id, dispatched.id, build_done.id,
            approved.id, test_passed.id, judge_passed.id]


class TestTraceCli:
    def test_trace_outputs_task_tree(self, project, capsys):
        _seed_full_trace(project, "T1")
        rc = main(["task", "trace", "T1"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "T1" in out
        for expected in ("task.dispatched", "dev.build.done",
                          "review.approved", "test.passed",
                          "judge.passed"):
            assert expected in out

    def test_trace_unknown_task_exits_nonzero(self, project, capsys):
        rc = main(["task", "trace", "T-NOPE"])
        assert rc != 0

    def test_trace_json_output(self, project, capsys):
        _seed_full_trace(project, "T1")
        rc = main(["task", "trace", "T1", "--format", "json"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["task_id"] == "T1"
        assert isinstance(data["events"], list)
        assert len(data["events"]) >= 5  # created + dispatched + stages

    def test_trace_causation_flag_shows_parent(self, project, capsys):
        _seed_full_trace(project, "T1")
        rc = main(["task", "trace", "T1", "--causation"])
        assert rc == 0
        out = capsys.readouterr().out
        # causation column or marker shows at least once.
        assert "caus" in out.lower() or "parent" in out.lower() or "←" in out


class TestCausationBackfill:
    def test_kanban_add_stamps_causation_from_latest_user_message(
        self, project, capsys
    ):
        """LH-5.T2: when `zf kanban add` runs after a user.message, the
        emitted task.created event carries that message's id as its
        causation_id so trace can walk the full chain."""
        log = EventLog(project / ".zf" / "events.jsonl")
        user = ZfEvent(type="user.message", actor=None,
                       payload={"text": "build it"})
        log.append(user)

        rc = main(["kanban", "add", "auth module"])
        assert rc == 0

        events = log.read_all()
        created = next(e for e in events if e.type == "task.created")
        assert created.causation_id == user.id

    def test_kanban_add_inherits_chat_correlation(self, project, capsys):
        rc = main(["chat", "build it"])
        assert rc == 0
        log = EventLog(project / ".zf" / "events.jsonl")
        user = next(e for e in log.read_all() if e.type == "user.message")

        rc = main(["kanban", "add", "auth module"])
        assert rc == 0

        created = next(e for e in log.read_all() if e.type == "task.created")
        assert created.causation_id == user.id
        assert created.correlation_id == user.correlation_id


class TestMetricsFields:
    def test_trace_complete_rate_counts_complete_chains(self, project):
        """LH-5.T5: MetricsSnapshot gains trace_complete_rate +
        avg_task_duration_minutes + avg_events_per_task."""
        _seed_full_trace(project, "T1")
        from zf.core.cost.tracker import CostTracker

        events = EventLog(project / ".zf" / "events.jsonl")
        tasks = TaskStore(project / ".zf" / "kanban.json")
        cost = CostTracker(project / ".zf" / "cost.jsonl")
        snap = MetricsCollector.compute(
            events=events, tasks=tasks, cost=cost,
        )
        assert hasattr(snap, "trace_complete_rate")
        assert hasattr(snap, "avg_events_per_task")
        # T1 has created → dispatched → build_done → approved → test →
        # judge all linked → trace complete
        assert snap.trace_complete_rate == pytest.approx(1.0)


class TestWireUp:
    def test_task_trace_wired_in_cli_main(self):
        src = Path("src/zf/cli/main.py").read_text()
        assert "task_trace" in src or "task.trace" in src.replace("_", ".")

    def test_kanban_add_causation_logic_exists(self):
        src = Path("src/zf/cli/kanban.py").read_text()
        assert "causation_id" in src
