"""LH-0.T1: rework cap — task.retry_count enforcement.

Rules:
  - review.rejected / test.failed / verify.failed / judge.failed increment
    task.retry_count
  - When retry_count > role.max_rework_attempts, dispatch is refused and
    task.rework.capped is emitted for Run Manager-owned recovery.
  - Default max_rework_attempts is 3; per-role override via RoleConfig.

Covers orchestrator_reactor (Layer 1 legacy rework path) and
orchestrator_dispatch (Layer 2 assign-driven path via _dispatch_ready).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig, RoleConfig, SessionConfig, ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
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
def legacy_config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="dev", backend="mock"),
            RoleConfig(name="review", backend="mock"),
        ],
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


class TestSchema:
    def test_task_has_retry_count_default_zero(self):
        t = Task(title="x")
        assert hasattr(t, "retry_count")
        assert t.retry_count == 0

    def test_roleconfig_has_max_rework_attempts_default_3(self):
        r = RoleConfig(name="dev")
        assert r.max_rework_attempts == 3

    def test_task_retry_count_round_trips_via_store(self, state_dir):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", retry_count=2))
        got = store.get("T1")
        assert got.retry_count == 2


class TestRetryCountIncrementsOnFailure:
    def test_review_rejected_increments(
        self, state_dir, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="review", assigned_to="dev"))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="review.rejected", actor="review", task_id="T1",
            payload={"reason": "style"},
        ))
        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()
        assert store.get("T1").retry_count == 1

    def test_test_failed_increments(
        self, state_dir, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="test.failed", actor="test", task_id="T1",
        ))
        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()
        assert store.get("T1").retry_count == 1

    def test_verify_failed_increments(
        self, state_dir, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="verify.failed", actor="verify", task_id="T1",
        ))
        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()
        assert store.get("T1").retry_count == 1

    def test_judge_failed_increments(
        self, state_dir, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="testing", assigned_to="dev"))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="judge.failed", actor="judge", task_id="T1",
        ))
        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()
        assert store.get("T1").retry_count == 1

    def test_success_events_do_not_increment(
        self, state_dir, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="review", assigned_to="dev"))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="review.approved", actor="review", task_id="T1",
        ))
        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()
        assert store.get("T1").retry_count == 0


class TestCapBlocksRework:
    def test_fourth_rework_is_capped(
        self, state_dir, legacy_config, transport
    ):
        """Task already at retry_count=3 (max default): the 4th rework
        attempt must be refused and produce a Run Manager-owned cap fact."""
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x", status="review",
            assigned_to="dev", retry_count=3,
        ))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="review.rejected", actor="review", task_id="T1",
            payload={"reason": "still wrong"},
        ))
        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        capped = [e for e in events if e.type == "task.rework.capped"]
        assert len(capped) == 1
        assert capped[0].task_id == "T1"
        assert capped[0].payload["recovery_owner"] == "run_manager"
        assert not any(e.type == "human.escalate" for e in events)

    def test_per_role_override_allows_more_attempts(self, state_dir, transport):
        """dev role with max_rework_attempts=5: still allowed at
        retry_count=4 → becomes 5 after the rejection, still <= cap."""
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            roles=[
                RoleConfig(name="dev", backend="mock",
                           max_rework_attempts=5),
                RoleConfig(name="review", backend="mock"),
            ],
        )
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x", status="review",
            assigned_to="dev", retry_count=4,
        ))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="review.rejected", actor="review", task_id="T1",
        ))
        orch = Orchestrator(state_dir, cfg, transport)
        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        assert not any(e.type == "task.rework.capped" for e in events)
        assert store.get("T1").retry_count == 5

    def test_capped_event_carries_context(
        self, state_dir, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(
            id="T1", title="x", status="review",
            assigned_to="dev", retry_count=3,
        ))
        EventLog(state_dir / "events.jsonl").append(ZfEvent(
            type="review.rejected", actor="review", task_id="T1",
            payload={"reason": "bad code"},
        ))
        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        events = EventLog(state_dir / "events.jsonl").read_all()
        capped = next(e for e in events if e.type == "task.rework.capped")
        assert capped.payload.get("retry_count") == 4
        assert capped.payload.get("max_attempts") == 3
        assert "bad code" in (capped.payload.get("last_reason") or "")
        assert capped.payload.get("failure_count") == 1
        assert capped.payload.get("semantic_triage_required") is False


class TestWireUpProof:
    """CLAUDE.md: every new enforcement component needs a runtime-import
    grep proof to avoid the library-without-callers anti-pattern."""

    def test_max_rework_attempts_wired_into_dispatch(self):
        import pathlib
        src = pathlib.Path(
            "src/zf/runtime/orchestrator_dispatch.py"
        ).read_text()
        assert "max_rework_attempts" in src, (
            "rework cap must be enforced in orchestrator_dispatch.py"
        )

    def test_retry_count_counted_in_housekeeping(self):
        import pathlib
        src = pathlib.Path("src/zf/runtime/orchestrator.py").read_text()
        hk = pathlib.Path(
            "src/zf/runtime/housekeeping.py"
        ).read_text()
        assert "retry_count" in src or "retry_count" in hk, (
            "retry_count increment must live in housekeeping path so "
            "it fires in both Layer 1 legacy and Layer 2 modes"
        )


class TestAttemptRetryScheduledEvent:
    """131-P2-1/P2-3:rework 派发发射 task.attempt.retry_scheduled。"""

    def test_rework_dispatch_emits_retry_scheduled_with_lease(
        self, state_dir, legacy_config, transport
    ):
        store = TaskStore(state_dir / "kanban.json")
        store.add(Task(id="T1", title="x", status="review", assigned_to="dev"))
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="review.rejected", actor="review", task_id="T1",
            payload={"reason": "style"},
        ))
        orch = Orchestrator(state_dir, legacy_config, transport)
        orch.run_once()

        scheduled = [
            e for e in log.read_all()
            if e.type == "task.attempt.retry_scheduled"
        ]
        assert len(scheduled) == 1
        payload = scheduled[0].payload
        assert payload["ordinal"] >= 1
        assert payload["cap"] == 3  # role 默认 max_rework_attempts
        assert payload["lease_token"].startswith("disp-")
        assert payload["trigger_event_type"] == "review.rejected"
