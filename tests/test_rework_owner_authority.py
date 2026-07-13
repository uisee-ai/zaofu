"""ZF-E2E-RACING-P2 (2026-07-11): triage's owner decision must bind.

Racing e2e: zf.yaml mapped dev.blocked → orchestrator; triage agreed
(classification=yaml_routing, suspected_owner=orchestrator, retryable=false,
recorded in task.rework.triage.completed) — yet the rework was dispatched to
dev-2 five seconds later and burned a full round on the same scope wall.
Root cause: `_route_rework_trigger`'s retry gate looked only at the
classification bucket, never at the owner — routing a rework briefing at the
control-plane role (orchestrator) is circular (it is woken by events, it does
not take task lanes), so those must go to the block/notify path. Worker roles
— including readers like arch, which the gate.failed → arch replan route
dispatches to — keep the normal rework dispatch. Stage-level backedges
(explicit on_fail lane pins) retain their specificity above global routing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    ZfConfig,
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
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


def _orchestrator(state_dir: Path, transport, *, rework_routing: dict) -> Orchestrator:
    cfg = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        workflow=WorkflowConfig(rework_routing=rework_routing),
        roles=[
            RoleConfig(name="orchestrator", backend="mock", role_kind="reader"),
            RoleConfig(name="dev", backend="mock"),
        ],
    )
    return Orchestrator(state_dir, cfg, transport)


def _blocked_task(state_dir: Path) -> Task:
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", assigned_to="dev"))
    store.update("T1", status="in_progress", assigned_to="dev")
    return store.get("T1")


class TestExplicitRoutingWithoutBackedge:
    def test_rework_routing_resolves_when_no_stage_backedge_declared(
        self, state_dir, transport
    ):
        # Racing shape: task-model config, no workflow.stages/on_fail — the
        # same-lane branch must not fire and explicit routing must resolve.
        orch = _orchestrator(
            state_dir, transport, rework_routing={"dev.blocked": "orchestrator"}
        )
        task = _blocked_task(state_dir)
        event = ZfEvent(
            type="dev.blocked",
            actor="dev-1",
            task_id="T1",
            payload={"reason": "scope guard blocked required fix"},
        )

        role = orch._resolve_rework_role(task, event)

        assert role is not None
        assert role.name == "orchestrator"

    def test_unrouted_event_still_falls_back_to_dev(self, state_dir, transport):
        orch = _orchestrator(state_dir, transport, rework_routing={})
        task = _blocked_task(state_dir)
        event = ZfEvent(
            type="review.rejected",
            actor="review",
            task_id="T1",
            payload={"reason": "needs changes"},
        )

        role = orch._resolve_rework_role(task, event)

        assert role is not None
        assert role.name == "dev"


class TestControlPlaneOwnerBlocksLaneRework:
    def test_dev_blocked_routed_to_orchestrator_blocks_not_redispatches(
        self, state_dir, transport
    ):
        orch = _orchestrator(
            state_dir, transport, rework_routing={"dev.blocked": "orchestrator"}
        )
        task = _blocked_task(state_dir)
        event = ZfEvent(
            type="dev.blocked",
            actor="dev-1",
            task_id="T1",
            payload={"reason": "scope guard blocked required fix"},
        )

        decision = orch._route_rework_trigger(
            task, event, reason="dev.blocked → rework"
        )

        assert decision.action == "block"
        assert decision.role == "orchestrator"
        dispatched = [
            e for e in orch.event_log.read_all()
            if e.type in {"task.dispatched", "task.rework.requested"}
        ]
        assert dispatched == []

    def test_writer_owner_keeps_normal_rework_dispatch_path(
        self, state_dir, transport
    ):
        # Owner resolving to a writer role must NOT trip the gate — the
        # decision stays on the rework-dispatch branch (whatever its
        # downstream outcome).
        orch = _orchestrator(
            state_dir, transport, rework_routing={"review.rejected": "dev"}
        )
        triage = orch._ensure_rework_triage(ZfEvent(
            type="review.rejected",
            actor="review",
            task_id="T1",
            payload={"reason": "needs changes"},
        ))
        assert orch._triage_owner_excludes_lane_rework(triage) is False

    def test_reader_worker_owner_does_not_trip_gate(self, state_dir, transport):
        # Readers like arch take dispatched work (gate.failed → arch replan);
        # only the control-plane role is excluded from lane rework.
        cfg = ZfConfig(
            project=ProjectConfig(name="t"),
            session=SessionConfig(tmux_session="t"),
            workflow=WorkflowConfig(rework_routing={"gate.failed": "arch"}),
            roles=[
                RoleConfig(name="arch", backend="mock", role_kind="reader"),
                RoleConfig(name="dev", backend="mock"),
            ],
        )
        orch = Orchestrator(state_dir, cfg, transport)
        triage = orch._ensure_rework_triage(ZfEvent(
            type="gate.failed",
            actor="critic",
            task_id="T1",
            payload={"reason": "design gate failed"},
        ))
        assert orch._triage_owner_excludes_lane_rework(triage) is False

    def test_unknown_owner_role_does_not_trip_gate(self, state_dir, transport):
        orch = _orchestrator(state_dir, transport, rework_routing={})
        triage = orch._ensure_rework_triage(ZfEvent(
            type="discriminator.failed",
            actor="zf-cli",
            task_id="T1",
            payload={"reason": "verification command failed (rc=1)"},
        ))
        # design_issue → suspected_owner=arch; arch absent from config —
        # same-lane retry stays the only automated option (cap bounds it).
        assert orch._triage_owner_excludes_lane_rework(triage) is False
