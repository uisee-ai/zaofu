"""Phase 0 integration test — full flow in isolated tmpdir.

init -> validate -> create task -> transitions -> gate -> query events
"""

from __future__ import annotations

import json
from pathlib import Path

from zf.cli.main import main
from zf.core.events.log import EventLog
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.statemachine.task import TaskStateMachine, InvalidTransition
from zf.core.verification.gates import CommandGate, FileExistsGate
from zf.core.verification.evidence import EvidenceCollector

import pytest


class TestPhase0Integration:
    """End-to-end flow exercising all Phase 0 components."""

    def test_full_lifecycle(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)

        # 1. Create zf.yaml
        (tmp_path / "zf.yaml").write_text(
            'version: "1.0"\nproject:\n  name: integration-test\n'
            'roles:\n  - name: dev\n    backend: python\n    model: x\n'
        )

        # 2. zf init
        assert main(["init"]) == 0
        state_dir = tmp_path / ".zf"
        assert state_dir.is_dir()
        assert (state_dir / "events.jsonl").exists()
        assert (state_dir / "kanban.json").exists()
        assert (state_dir / "session.yaml").exists()

        # 3. zf validate
        capsys.readouterr()
        assert main(["validate"]) == 0

        # 4. zf status
        capsys.readouterr()
        assert main(["status"]) == 0
        out = capsys.readouterr().out
        assert "sess-" in out

        # 5. Create task via TaskStore
        store = TaskStore(state_dir / "kanban.json")
        task = store.add(Task(
            title="Implement login endpoint",
            key="auth:login",
            contract=TaskContract(
                behavior="POST /login returns JWT",
                verification="echo 'test passed'",
                scope=["src/auth/**"],
                acceptance="exit_code=0",
            ),
        ))
        assert task.status == "backlog"

        # 6. State machine transitions
        sm = TaskStateMachine()
        new_status = sm.transition(task.status, "in_progress")
        store.update(task.id, status=new_status)

        new_status = sm.transition(new_status, "review")
        store.update(task.id, status=new_status)

        new_status = sm.transition(new_status, "testing")
        store.update(task.id, status=new_status)

        # 7. Run verification gate
        gate = CommandGate(
            name="login-test",
            command=task.contract.verification,
        )
        result = gate.run()
        assert result.passed

        # Collect evidence
        collector = EvidenceCollector()
        collector.record(result)
        assert collector.all_passed()

        # 8. Transition to done
        new_status = sm.transition(new_status, "done")
        store.update(task.id, status=new_status)

        # Verify final state
        final = store.get(task.id)
        assert final.status == "done"

        # 9. Emit events
        assert main(["emit", "dev.build.done", "--task", task.id]) == 0
        assert main(["emit", "review.approved", "--task", task.id]) == 0

        # 10. Query events
        event_log = EventLog(state_dir / "events.jsonl")
        all_events = event_log.read_all()
        assert len(all_events) >= 3  # session.started + 2 emitted
        types = [e.type for e in all_events]
        assert "session.started" in types
        assert "dev.build.done" in types
        assert "review.approved" in types

        # 11. Events are in chronological order
        timestamps = [e.ts for e in all_events]
        assert timestamps == sorted(timestamps)

    def test_backward_transition_rejected(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
        main(["init"])

        sm = TaskStateMachine()
        with pytest.raises(InvalidTransition):
            sm.transition("done", "review")

    def test_invalid_config_detected(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zf.yaml").write_text('version: "1.0"\norchestrator:\n  backend: x\n')
        assert main(["validate"]) == 1

    def test_ensure_task_idempotent(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: test\n')
        main(["init"])

        store = TaskStore(tmp_path / ".zf" / "kanban.json")
        t1 = store.ensure(key="auth:login", title="Login v1")
        t2 = store.ensure(key="auth:login", title="Login v2")
        assert t1.id == t2.id
        assert len(store.list_all()) == 1

    def test_file_gate_integration(self, tmp_path: Path):
        (tmp_path / "required.txt").write_text("present")
        gate = FileExistsGate(
            name="artifact-check",
            paths=[str(tmp_path / "required.txt")],
        )
        result = gate.run()
        assert result.passed

        gate_missing = FileExistsGate(
            name="artifact-check",
            paths=[str(tmp_path / "missing.txt")],
        )
        result = gate_missing.run()
        assert not result.passed
