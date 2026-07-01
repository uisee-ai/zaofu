"""Phase 1 integration test — multi-agent coordination flow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from zf.cli.main import main
from zf.core.config.schema import ZfConfig, ProjectConfig, RoleConfig, SessionConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.task.wip import WipEnforcer
from zf.core.security.nonce import NonceManager
from zf.core.security.signing import EventSigner
from zf.core.workflow.topology import WorkflowTopology
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def project(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = {
        "version": "1.0",
        "project": {"name": "phase1-test", "state_dir": ".zf"},
        "session": {"tmux_session": "test-zf"},
        "orchestrator": {"backend": "mock"},
        "roles": [
            {"name": "dev", "backend": "mock", "stages": ["implement"],
             "triggers": ["task.assigned"], "publishes": ["dev.build.done"]},
            {"name": "review", "backend": "mock", "stages": ["code_review"],
             "triggers": ["dev.build.done"], "publishes": ["review.approved"]},
            {"name": "test", "backend": "mock", "stages": ["independent_test"],
             "triggers": ["dev.build.done"], "publishes": ["test.passed"]},
        ],
        "quality_gates": {"static": {"enabled": True, "required_checks": ["true"]}},
    }
    (tmp_path / "zf.yaml").write_text(yaml.dump(config))
    main(["init"])
    return tmp_path


class TestPhase1MultiAgentFlow:
    """Full multi-role coordination: add task → orchestrator dispatches →
    agent emits build.done → orchestrator moves to review → review approves →
    test passes → task done."""

    def test_full_coordination_cycle(self, project: Path):
        from zf.core.config.loader import load_config

        config = load_config(project / "zf.yaml")
        state_dir = project / ".zf"
        transport = TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True))
        event_log = EventLog(state_dir / "events.jsonl")
        store = TaskStore(state_dir / "kanban.json")

        # 1. Add task via CLI
        main(["kanban", "add", "Implement auth module"])
        tasks = store.list_all()
        assert len(tasks) == 1
        task_id = tasks[0].id

        # 2. Orchestrator dispatches to dev
        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()
        dispatch = [d for d in decisions if d.action == "dispatch"]
        assert len(dispatch) == 1
        assert dispatch[0].task_id == task_id
        assert dispatch[0].role == "dev"

        task = store.get(task_id)
        assert task.status == "in_progress"
        assert task.assigned_to == "dev"

        # 3. Dev emits build.done
        event_log.append(ZfEvent(type="dev.build.done", actor="dev", task_id=task_id))

        # 4. Orchestrator reacts: moves to review
        decisions = orch.run_once()
        task = store.get(task_id)
        assert task.status == "review"

        # 5. Reviewer approves
        event_log.append(ZfEvent(type="review.approved", actor="review", task_id=task_id))

        # 6. Orchestrator reacts: moves to testing
        decisions = orch.run_once()
        task = store.get(task_id)
        assert task.status == "testing"

        # 7. Test passes
        event_log.append(ZfEvent(type="test.passed", actor="test", task_id=task_id))

        # 8. Orchestrator reacts: moves to done
        decisions = orch.run_once()
        task = store.get(task_id)
        assert task.status == "done"

        # 9. Verify complete event trail
        events = event_log.read_all()
        types = [e.type for e in events]
        assert "task.created" in types
        assert "task.dispatched" in types
        assert "dev.build.done" in types
        assert "review.approved" in types
        assert "test.passed" in types

    def test_topology_matches_config(self, project: Path):
        from zf.core.config.loader import load_config
        config = load_config(project / "zf.yaml")
        topo = WorkflowTopology.from_config(config)
        edges = topo.edges()
        # dev --[dev.build.done]--> review
        # dev --[dev.build.done]--> test
        from_to = {(e[0], e[1]) for e in edges}
        assert ("dev", "review") in from_to
        assert ("dev", "test") in from_to

    def test_wip_prevents_double_dispatch(self, project: Path):
        from zf.core.config.loader import load_config
        config = load_config(project / "zf.yaml")
        state_dir = project / ".zf"
        transport = TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True))
        store = TaskStore(state_dir / "kanban.json")

        main(["kanban", "add", "Task A"])
        main(["kanban", "add", "Task B"])

        orch = Orchestrator(state_dir, config, transport)
        decisions = orch.run_once()
        dispatch = [d for d in decisions if d.action == "dispatch"]
        # Should only dispatch 1 (WIP=1 per role)
        assert len(dispatch) == 1

    def test_nonce_issue_and_validate(self, project: Path):
        state_dir = project / ".zf"
        mgr = NonceManager(state_dir / "nonces")
        nonce = mgr.issue("dev")
        assert mgr.validate(nonce)
        mgr.consume(nonce)
        assert not mgr.validate(nonce)

    def test_event_signing(self, project: Path):
        signer = EventSigner(b"test-secret")
        data = '{"type": "test"}'
        sig = signer.sign(data)
        assert signer.verify(data, sig)
        assert not signer.verify(data + "tampered", sig)

    def test_gate_run_via_cli(self, project: Path):
        result = main(["gate", "run", "static", "--command", "true"])
        assert result == 0

    def test_kanban_board_view(self, project: Path, capsys):
        main(["kanban", "add", "Task A"])
        result = main(["kanban"])
        assert result == 0
        captured = capsys.readouterr()
        assert "Task A" in captured.out

    def test_blocked_by_resolution(self, project: Path):
        state_dir = project / ".zf"
        store = TaskStore(state_dir / "kanban.json")

        main(["kanban", "add", "First"])
        tasks = store.list_all()
        first_id = tasks[0].id

        # Add second task blocked by first
        t2 = Task(title="Second", blocked_by=[first_id])
        store.add(t2)

        ready = store.ready()
        ready_ids = {t.id for t in ready}
        assert first_id in ready_ids
        assert t2.id not in ready_ids

        # Complete first
        store.update(first_id, status="done")
        ready = store.ready()
        ready_ids = {t.id for t in ready}
        assert t2.id in ready_ids
