"""LH-0.T2: stage transition gate — zf kanban assign/move must have
the prior stage's completion event in events.jsonl before allowing
the transition. Prevents Layer 2 from jumping stages (e.g. going
straight from dev to done without review/test/judge).

Transition matrix (target → allowed predecessors):
  assign dev     : task.created | review.rejected | test.failed |
                   judge.failed | task.orphaned
  assign review  : dev.build.done
  assign test    : review.approved
  assign judge   : test.passed
  move done      : discriminator.passed when terminal discriminators are
                   configured, otherwise judge.passed/test.passed fallback
  move cancelled : always (human override)

Gate lives in the CLI layer (zf kanban assign / zf kanban move). Direct
TaskStore.update() calls from test fixtures bypass the gate by design
(store is the state primitive, CLI is the enforcement layer).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

from zf.cli.kanban import _run_assign, _run_move
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="session.started", actor="zf-cli")
    )
    (sd / "kanban.json").write_text("[]\n")
    # Minimal zf.yaml so the CLI accepts role names
    (tmp_path / "zf.yaml").write_text(
        "version: '1.0'\n"
        "project:\n  name: t\n"
        "session:\n  tmux_session: t\n"
        "roles:\n"
        "  - name: dev\n    backend: mock\n"
        "  - name: review\n    backend: mock\n"
        "  - name: test\n    backend: mock\n"
        "  - name: judge\n    backend: mock\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _assign(task_id: str, role: str) -> int:
    return _run_assign(argparse.Namespace(task_id=task_id, role=role))


def _move(task_id: str, status: str) -> int:
    return _run_move(argparse.Namespace(task_id=task_id, status=status))


def _seed_task(project: Path, task_id: str, status: str = "backlog") -> None:
    store = TaskStore(project / ".zf" / "kanban.json")
    store.add(Task(id=task_id, title=f"t-{task_id}", status=status))


def _append_event(project: Path, **kw) -> None:
    EventLog(project / ".zf" / "events.jsonl").append(ZfEvent(**kw))


class TestAssignGateAllowed:
    def test_assign_dev_after_task_created_ok(self, project):
        _seed_task(project, "T1")
        _append_event(
            project, type="task.created", actor="zf-cli", task_id="T1",
        )
        assert _assign("T1", "dev") == 0
        assert TaskStore(project / ".zf" / "kanban.json") \
            .get("T1").assigned_to == "dev"

    def test_assign_review_after_dev_build_done_ok(self, project):
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="dev.build.done", actor="dev", task_id="T1",
        )
        assert _assign("T1", "review") == 0

    def test_assign_test_after_review_approved_ok(self, project):
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="review.approved", actor="review", task_id="T1",
        )
        assert _assign("T1", "test") == 0

    def test_assign_judge_after_test_passed_ok(self, project):
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="test.passed", actor="test", task_id="T1",
        )
        assert _assign("T1", "judge") == 0

    def test_assign_dev_after_review_rejected_ok(self, project):
        """Rework path: review.rejected allows re-assign to dev."""
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="review.rejected", actor="review", task_id="T1",
        )
        assert _assign("T1", "dev") == 0


class TestAssignGateRejected:
    def test_skip_review_blocked(self, project, capsys):
        """assign test without review.approved → rejected + event emitted."""
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="dev.build.done", actor="dev", task_id="T1",
        )
        rc = _assign("T1", "test")
        assert rc != 0
        events = EventLog(project / ".zf" / "events.jsonl").read_all()
        assert any(e.type == "task.invalid_transition" and e.task_id == "T1"
                   for e in events)

    def test_skip_test_blocked(self, project):
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="review.approved", actor="review", task_id="T1",
        )
        assert _assign("T1", "judge") != 0

    def test_assign_review_without_build_done_blocked(self, project):
        _seed_task(project, "T1", status="in_progress")
        # No dev.build.done emitted.
        assert _assign("T1", "review") != 0

    def test_invalid_transition_event_payload(self, project):
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="dev.build.done", actor="dev", task_id="T1",
        )
        _assign("T1", "test")
        events = EventLog(project / ".zf" / "events.jsonl").read_all()
        inv = next(e for e in events if e.type == "task.invalid_transition")
        assert inv.payload.get("target") == "test"
        assert inv.payload.get("missing") == "review.approved"


class TestMoveGate:
    def test_move_done_after_judge_passed_ok(self, project):
        _seed_task(project, "T1", status="testing")
        _append_event(
            project, type="judge.passed", actor="judge", task_id="T1",
        )
        assert _move("T1", "done") == 0

    def test_move_done_from_in_progress_after_judge_passed_ok(self, project):
        """Layer2 keeps tasks in_progress while reassigning stages."""
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="judge.passed", actor="judge", task_id="T1",
        )
        assert _move("T1", "done") == 0

    def test_move_done_without_judge_passed_blocked(self, project):
        _seed_task(project, "T1", status="testing")
        _append_event(
            project, type="test.passed", actor="test", task_id="T1",
        )
        # test.passed is NOT enough — need judge.passed
        assert _move("T1", "done") != 0

    def test_move_done_uses_test_passed_when_no_judge_role(self, project):
        (project / "zf.yaml").write_text(
            "version: '1.0'\n"
            "project:\n  name: t\n"
            "roles:\n"
            "  - name: dev\n    backend: mock\n"
            "  - name: review\n    backend: mock\n"
            "  - name: test\n    backend: mock\n    publishes:\n      - test.passed\n"
        )
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="test.passed", actor="test", task_id="T1",
        )
        assert _move("T1", "done") == 0

    def test_move_done_with_terminal_discriminator_requires_discriminator_passed(
        self, project,
    ):
        (project / "zf.yaml").write_text(
            "version: '1.0'\n"
            "project:\n  name: t\n"
            "session:\n  tmux_session: t\n"
            "quality_gates:\n"
            "  test:\n"
            "    enabled: true\n"
            "    required_checks:\n"
            "      - 'true'\n"
            "verification:\n"
            "  contract:\n"
            "    required: true\n"
            "  architecture:\n"
            "    enabled: true\n"
            "roles:\n"
            "  - name: dev\n    backend: mock\n"
            "  - name: review\n    backend: mock\n"
            "  - name: test\n    backend: mock\n"
            "  - name: judge\n    backend: mock\n    publishes:\n      - judge.passed\n"
        )
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="judge.passed", actor="judge", task_id="T1",
        )

        assert _move("T1", "done") != 0

    def test_move_done_with_terminal_discriminator_passed_ok(self, project):
        (project / "zf.yaml").write_text(
            "version: '1.0'\n"
            "project:\n  name: t\n"
            "session:\n  tmux_session: t\n"
            "verification:\n"
            "  contract:\n"
            "    required: true\n"
            "roles:\n"
            "  - name: dev\n    backend: mock\n"
            "  - name: judge\n    backend: mock\n    publishes:\n      - judge.passed\n"
        )
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="judge.passed", actor="judge", task_id="T1",
        )
        _append_event(
            project,
            type="discriminator.passed",
            actor="zf-cli",
            task_id="T1",
        )

        assert _move("T1", "done") == 0

    def test_contract_required_alone_keeps_topology_terminal_event(
        self, project,
    ):
        (project / "zf.yaml").write_text(
            "version: '1.0'\n"
            "project:\n  name: t\n"
            "session:\n  tmux_session: t\n"
            "verification:\n"
            "  contract:\n"
            "    required: true\n"
            "roles:\n"
            "  - name: dev\n    backend: mock\n"
            "  - name: judge\n    backend: mock\n    publishes:\n"
            "      - judge.passed\n"
        )
        _seed_task(project, "T1", status="in_progress")
        _append_event(
            project, type="judge.passed", actor="judge", task_id="T1",
        )

        assert _move("T1", "done") == 0

    def test_move_cancelled_always_allowed(self, project):
        """Human override: cancel can go from any status with no predecessor."""
        _seed_task(project, "T1", status="in_progress")
        assert _move("T1", "cancelled") == 0


class TestWireUpProof:
    def test_gate_function_exists_in_kanban_cli(self):
        src = (Path(__file__).resolve().parents[1]
               / "src/zf/cli/kanban.py").read_text()
        assert "_validate_transition" in src
