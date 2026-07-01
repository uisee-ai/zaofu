"""End-to-end lifecycle tests (G-LIFE-5).

Cover the full chain through every Sprint A addition:
- arch → review → dev → review → test → judge → done (happy path)
- judge.failed loops task back to dev (rework)
- unrecovered stuck worker escalates end-to-end
  (stuck → recovery_failed + human.escalate)
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_types import OrchestratorDecision
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def five_role_config() -> ZfConfig:
    """Legacy mode with all 5 worker roles (no orchestrator → Python kernel drives)."""
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="arch", backend="mock"),
            RoleConfig(name="dev", backend="mock"),
            RoleConfig(name="review", backend="mock"),
            RoleConfig(name="test", backend="mock"),
            RoleConfig(
                name="judge", backend="mock",
                stuck_threshold_seconds=0.05,
            ),
        ],
    )


class _ScriptedTransport(TmuxTransport):
    def __init__(self, outputs: dict[str, list[str]] | None = None):
        super().__init__(TmuxSession(session_name="t", dry_run=True))
        self._scripts = {k: list(v) for k, v in (outputs or {}).items()}

    def capture_log(self, role: str, lines: int = 200) -> str:
        script = self._scripts.get(role, [])
        if not script:
            return ""
        if len(script) == 1:
            return script[0]
        return script.pop(0)


def _emit(state_dir: Path, event: ZfEvent) -> None:
    EventLog(state_dir / "events.jsonl").append(event)


class TestHappyPathFullChain:
    def test_dev_build_review_test_judge_to_done(
        self, state_dir: Path, five_role_config
    ):
        """Full chain: task in testing + judge.passed → done."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="build oauth", status="testing",
                       assigned_to="test"))

        _emit(state_dir, ZfEvent(type="judge.passed", actor="judge", task_id="T1"))

        orch = Orchestrator(state_dir, five_role_config, _ScriptedTransport())
        orch.run_once()

        task = store.get("T1")
        assert task is not None
        assert task.status == "done", f"expected done, got {task.status}"


class TestJudgeFailedLoopsBackToDev:
    def test_judge_failed_task_reenters_in_progress(
        self, state_dir: Path, five_role_config
    ):
        """When judge rejects, task goes back to in_progress for dev to fix."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="oauth impl", status="testing",
                       assigned_to="test"))

        _emit(state_dir, ZfEvent(
            type="judge.failed", actor="judge", task_id="T1",
            payload={"reason": "rubric item 3 missing coverage"},
        ))

        orch = Orchestrator(state_dir, five_role_config, _ScriptedTransport())
        orch.run_once()

        task = store.get("T1")
        assert task.status == "in_progress"

    def test_dev_then_review_then_judge_loop_works(
        self, state_dir: Path, five_role_config
    ):
        """Full recovery path: judge fails → in_progress → dev.build.done →
        review → review.approved → testing → test.passed → judge.passed → done."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing",
                       assigned_to="test"))
        orch = Orchestrator(state_dir, five_role_config, _ScriptedTransport())

        # 1. judge fails → in_progress
        _emit(state_dir, ZfEvent(type="judge.failed", actor="judge", task_id="T1"))
        orch.run_once()
        assert store.get("T1").status == "in_progress"

        # 2. dev finishes rework → review
        _emit(state_dir, ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))
        orch.run_once()
        assert store.get("T1").status == "review"

        # 3. review approves → testing
        _emit(state_dir, ZfEvent(type="review.approved", actor="review", task_id="T1"))
        orch.run_once()
        assert store.get("T1").status == "testing"

        # 4. test passes → done (test.passed moves to done; judge.passed
        #    does same but only via separate event after test)
        _emit(state_dir, ZfEvent(type="test.passed", actor="test", task_id="T1"))
        orch.run_once()
        assert store.get("T1").status == "done"


class TestStuckWorkerEndToEnd:
    def test_stuck_worker_emits_human_escalate(
        self, state_dir: Path, five_role_config
    ):
        """Worker that stops producing new output trips stuck detector
        → failed recovery keeps EscalationManager as the human fallback."""
        # judge has tiny threshold (0.05s via fixture)
        # E2: stuck detector only fires for busy workers — assign a task
        # to judge so is_idle=False.
        TaskStore(state_dir / "kanban.json").add(Task(
            title="T-active", status="in_progress", assigned_to="judge",
        ))
        transport = _ScriptedTransport({
            "arch": ["a init"],
            "dev": ["d init"],
            "review": ["r init"],
            "test": ["t init"],
            "judge": ["j init"],  # same output forever = stuck
        })
        orch = Orchestrator(state_dir, five_role_config, transport)

        def fail_respawn(role):  # noqa: ANN001
            return OrchestratorDecision(
                action="respawn_failed",
                role=role.instance_id,
                reason="forced test failure",
            )

        orch._respawn_instance = fail_respawn  # type: ignore[method-assign]
        orch.run_once()  # seed detectors
        time.sleep(0.1)
        orch.run_once()  # trip stuck

        events = EventLog(state_dir / "events.jsonl").read_all()
        types = [e.type for e in events]
        assert "worker.stuck" in types
        assert "worker.stuck.recovery_failed" in types
        assert "human.escalate" in types
        # steer marker file should exist (EscalationManager side effect)
        assert (state_dir / "steer").exists()

    def test_stuck_reported_once_per_role(
        self, state_dir: Path, five_role_config
    ):
        """A stuck worker should not flood events.jsonl with the same
        worker.stuck every cycle — once reported, don't re-report until
        the detector observes new output."""
        # E2: assign a busy task to judge so the stuck path fires.
        TaskStore(state_dir / "kanban.json").add(Task(
            title="T-active", status="in_progress", assigned_to="judge",
        ))
        transport = _ScriptedTransport({
            "arch": ["a"],
            "dev": ["d"],
            "review": ["r"],
            "test": ["t"],
            "judge": ["j same"],
        })
        orch = Orchestrator(state_dir, five_role_config, transport)
        orch.run_once()
        time.sleep(0.1)
        for _ in range(5):
            orch.run_once()
            # Cross the tiny stuck threshold after any recovery reset. The
            # dedupe must still suppress repeats until pane output changes.
            time.sleep(0.06)

        events = EventLog(state_dir / "events.jsonl").read_all()
        stuck_for_judge = [
            e for e in events
            if e.type == "worker.stuck" and e.actor == "judge"
        ]
        assert len(stuck_for_judge) == 1, (
            f"expected exactly 1 worker.stuck for judge, got {len(stuck_for_judge)}"
        )
