"""P1-1: rework routing resolution — task.contract.rework_to →
config.workflow.rework_routing → 'dev' fallback.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from zf.core.config.schema import (
    FanoutAssignmentConfig,
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    ContractDConfig,
    VerificationConfig,
    WorkflowAffinityLaneConfig,
    WorkflowAffinityLaneProfileConfig,
    WorkflowConfig,
    WorkflowStageBackedgeConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def state_dir(tmp_path) -> Path:
    d = tmp_path / ".zf"
    d.mkdir()
    (d / "events.jsonl").touch()
    return d


def _config(
    rework_routing: dict[str, str] | None = None,
    extra_roles: list[RoleConfig] | None = None,
    verification: VerificationConfig | None = None,
) -> ZfConfig:
    roles = [
        RoleConfig(
            name="dev", backend="mock", stages=["implement"],
            publishes=["dev.build.done", "dev.blocked"],
        ),
        RoleConfig(
            name="review", backend="mock", stages=["code_review"],
            publishes=["review.approved", "review.rejected"],
        ),
    ]
    if extra_roles:
        roles.extend(extra_roles)
    return ZfConfig(
        project=ProjectConfig(name="test"),
        session=SessionConfig(tmux_session="test-zf"),
        workflow=WorkflowConfig(rework_routing=rework_routing or {}),
        roles=roles,
        verification=verification or VerificationConfig(),
    )


@pytest.fixture
def transport():
    return TmuxTransport(TmuxSession(session_name="t", dry_run=True))


# -- Resolution order --

def test_default_fallback_to_dev(state_dir, transport):
    """No task.contract.rework_to, no workflow.rework_routing → 'dev'."""
    config = _config()
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="review", assigned_to="dev"))
    orch = Orchestrator(state_dir, config, transport)
    task = store.get("T1")

    role = orch._resolve_rework_role(task, ZfEvent(type="review.rejected"))
    assert role is not None
    assert role.name == "dev"


def test_workflow_rework_routing_overrides_default(state_dir, transport):
    """config.workflow.rework_routing routes per failure event type."""
    config = _config(
        rework_routing={"review.rejected": "review"},
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="review", assigned_to="dev"))
    orch = Orchestrator(state_dir, config, transport)
    task = store.get("T1")

    role = orch._resolve_rework_role(task, ZfEvent(type="review.rejected"))
    assert role.name == "review"


def test_contract_rework_to_overrides_workflow_routing(state_dir, transport):
    """task.contract.rework_to wins over project-level routing."""
    config = _config(rework_routing={"review.rejected": "dev"})
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1", title="x", status="review", assigned_to="dev",
        contract=TaskContract(behavior="b", rework_to="review"),
    ))
    orch = Orchestrator(state_dir, config, transport)
    task = store.get("T1")

    role = orch._resolve_rework_role(task, ZfEvent(type="review.rejected"))
    assert role.name == "review"  # contract wins


def test_generic_dev_rework_prefers_owner_role_affinity_lane(
    state_dir,
    transport,
):
    config = _config(
        rework_routing={"review.child.failed": "dev"},
        extra_roles=[
            RoleConfig(
                name="dev-lane-1",
                backend="mock",
                role_kind="writer",
                stages=["implement"],
                publishes=["dev.build.done", "dev.failed"],
                triggers=["task.assigned"],
            ),
        ],
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="lane task",
        status="in_progress",
        assigned_to="review-lane-1",
        contract=TaskContract(behavior="b", owner_role="dev-lane-1"),
    ))
    orch = Orchestrator(state_dir, config, transport)

    role = orch._resolve_rework_role(
        store.get("T1"),
        ZfEvent(type="review.child.failed", task_id="T1"),
    )

    assert role is not None
    assert role.name == "dev-lane-1"
    assert role.instance_id == "dev-lane-1"


def test_stage_backedge_emit_routes_same_affinity_lane(
    state_dir,
    transport,
):
    config = _config(extra_roles=[
        RoleConfig(
            name="dev",
            instance_id="dev-lane-0",
            backend="mock",
            role_kind="writer",
            publishes=["dev.build.done", "dev.failed"],
        ),
        RoleConfig(
            name="dev",
            instance_id="dev-lane-1",
            backend="mock",
            role_kind="writer",
            publishes=["dev.build.done", "dev.failed"],
        ),
        RoleConfig(
            name="review",
            instance_id="review-lane-1",
            backend="mock",
            role_kind="reader",
            publishes=["review.approved", "review.rejected"],
        ),
    ])
    config.workflow = WorkflowConfig(
        stages=[
            WorkflowStageConfig(
                id="impl",
                topology="fanout_writer_scoped",
                roles=["dev-lane-0", "dev-lane-1"],
                assignment=FanoutAssignmentConfig(
                    strategy="affinity_stage_slots",
                    lane_profile="refactor-2",
                    stage_slot="impl",
                ),
            ),
            WorkflowStageConfig(
                id="review",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["review-lane-1"],
                assignment=FanoutAssignmentConfig(
                    strategy="affinity_stage_slots",
                    lane_profile="refactor-2",
                    stage_slot="review",
                ),
                on_reject=WorkflowStageBackedgeConfig(
                    event="review.rejected",
                    restart_stage="impl",
                    target_affinity="same_lane",
                    max_attempts=2,
                    feedback_artifact="review-feedback.md",
                    emit="impl.rework.requested",
                ),
            ),
        ],
        affinity_lanes={
            "refactor-2": WorkflowAffinityLaneProfileConfig(
                affinity_key="affinity_tag",
                lanes=[
                    WorkflowAffinityLaneConfig(
                        id="lane0",
                        impl="dev-lane-0",
                        review="review-lane-0",
                    ),
                    WorkflowAffinityLaneConfig(
                        id="lane1",
                        impl="dev-lane-1",
                        review="review-lane-1",
                    ),
                ],
            ),
        },
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T-LANE",
        title="gateway lane",
        status="in_progress",
        assigned_to="review-lane-1",
        retry_count=1,
        contract=TaskContract(behavior="b"),
    ))
    orch = Orchestrator(state_dir, config, transport)
    trigger = ZfEvent(
        type="review.rejected",
        actor="review-lane-1",
        task_id="T-LANE",
        payload={
            "lane_id": "lane1",
            "reason": "gateway provider parity missing",
        },
    )
    orch.event_log.append(trigger)

    dispatched_role = orch._dispatch_rework(store.get("T-LANE"), trigger)

    assert dispatched_role == "dev"
    task = store.get("T-LANE")
    assert task is not None
    assert task.assigned_to == "dev-lane-1"
    events = orch.event_log.read_all()
    rework = next(event for event in events if event.type == "task.rework.requested")
    assert rework.payload["assignee"] == "dev-lane-1"
    assert rework.payload["max_attempts"] == 2
    assert rework.payload["target_affinity"] == "same_lane"
    assert rework.payload["lane_id"] == "lane1"
    assert rework.payload["feedback_artifact"] == "review-feedback.md"
    feedback_ref = rework.payload["feedback_artifact_ref"]
    assert feedback_ref
    feedback_path = Path(feedback_ref)
    assert feedback_path.exists()
    feedback_text = feedback_path.read_text()
    assert "gateway provider parity missing" in feedback_text
    assert f"- trigger_event_id: `{trigger.id}`" in feedback_text
    assert f"- rework_request_event_id: `{rework.id}`" in feedback_text
    emitted = next(event for event in events if event.type == "impl.rework.requested")
    assert emitted.causation_id == rework.id
    assert emitted.payload["assignee"] == "dev-lane-1"
    assert emitted.payload["lane_id"] == "lane1"
    assert emitted.payload["feedback_artifact"] == "review-feedback.md"
    assert emitted.payload["feedback_artifact_ref"] == feedback_ref
    briefing = (state_dir / "briefings" / "dev-T-LANE-rework.md").read_text()
    assert "## Feedback Artifact" in briefing
    assert feedback_ref in briefing


def test_impl_dev_failed_backedge_requeues_same_dev_lane_before_global_route(
    state_dir,
    transport,
):
    config = _config(
        rework_routing={"dev.failed": "arch"},
        extra_roles=[
            RoleConfig(
                name="arch",
                instance_id="arch",
                backend="mock",
                role_kind="reader",
                publishes=["arch.proposal.done"],
            ),
            RoleConfig(
                name="dev",
                instance_id="dev-lane-0",
                backend="mock",
                role_kind="writer",
                publishes=["dev.build.done", "dev.failed"],
            ),
            RoleConfig(
                name="dev",
                instance_id="dev-lane-1",
                backend="mock",
                role_kind="writer",
                publishes=["dev.build.done", "dev.failed"],
            ),
        ],
    )
    config.workflow = WorkflowConfig(
        rework_routing={"dev.failed": "arch"},
        stages=[
            WorkflowStageConfig(
                id="impl",
                topology="fanout_writer_scoped",
                roles=["dev-lane-0", "dev-lane-1"],
                assignment=FanoutAssignmentConfig(
                    strategy="affinity_stage_slots",
                    lane_profile="refactor-2",
                    stage_slot="impl",
                ),
                on_fail=WorkflowStageBackedgeConfig(
                    event="dev.failed",
                    restart_stage="impl",
                    target_affinity="same_lane",
                    max_attempts=2,
                    feedback_artifact="dev-feedback.md",
                    emit="impl.rework.requested",
                ),
            ),
        ],
        affinity_lanes={
            "refactor-2": WorkflowAffinityLaneProfileConfig(
                affinity_key="affinity_tag",
                lanes=[
                    WorkflowAffinityLaneConfig(id="lane0", impl="dev-lane-0"),
                    WorkflowAffinityLaneConfig(id="lane1", impl="dev-lane-1"),
                ],
            ),
        },
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T-IMPL-FAIL",
        title="provider migration",
        status="in_progress",
        assigned_to="dev-lane-1",
        retry_count=1,
        contract=TaskContract(behavior="b"),
    ))
    orch = Orchestrator(state_dir, config, transport)
    trigger = ZfEvent(
        type="dev.failed",
        actor="dev-lane-1",
        task_id="T-IMPL-FAIL",
        payload={"reason": "typescript compile failed"},
    )
    orch.event_log.append(trigger)

    role = orch._resolve_rework_role(store.get("T-IMPL-FAIL"), trigger)

    assert role is not None
    assert role.name == "dev"
    assert role.instance_id == "dev-lane-1"

    dispatched_role = orch._dispatch_rework(store.get("T-IMPL-FAIL"), trigger)

    assert dispatched_role == "dev"
    task = store.get("T-IMPL-FAIL")
    assert task is not None
    assert task.assigned_to == "dev-lane-1"
    events = orch.event_log.read_all()
    rework = next(event for event in events if event.type == "task.rework.requested")
    assert rework.payload["assignee"] == "dev-lane-1"
    assert rework.payload["target_affinity"] == "same_lane"
    assert rework.payload["lane_id"] == "lane1"
    assert not (state_dir / "briefings" / "arch-T-IMPL-FAIL-rework.md").exists()


def test_rework_context_recovers_feedback_artifact_ref_from_request(
    state_dir,
    transport,
):
    config = _config(rework_routing={"review.rejected": "dev"})
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T-RESUME",
        title="resume rework",
        status="in_progress",
        assigned_to="dev",
        retry_count=1,
        contract=TaskContract(behavior="b"),
    ))
    orch = Orchestrator(state_dir, config, transport)
    trigger = ZfEvent(
        type="review.rejected",
        actor="review",
        task_id="T-RESUME",
        payload={"reason": "provider adapter mismatch"},
    )
    orch.event_log.append(trigger)
    feedback_ref = str(
        state_dir
        / "artifacts"
        / "rework-feedback"
        / "T-RESUME"
        / "review-feedback.md"
    )
    orch.event_writer.append(ZfEvent(
        type="task.rework.requested",
        actor="zf-cli",
        task_id="T-RESUME",
        payload={
            "trigger_event_id": trigger.id,
            "feedback_artifact_ref": feedback_ref,
            "reason": "provider adapter mismatch",
        },
        causation_id=trigger.id,
    ))

    context = orch._rework_context_for_dispatch(  # type: ignore[attr-defined]
        store.get("T-RESUME"),
        config.roles[0],
    )

    assert "### Feedback Artifact" in context
    assert feedback_ref in context


def test_stage_backedge_attempt_exhausted_uses_backedge_cap(
    state_dir,
    transport,
):
    config = _config(extra_roles=[
        RoleConfig(
            name="dev",
            instance_id="dev-lane-1",
            backend="mock",
            role_kind="writer",
            max_rework_attempts=5,
            publishes=["dev.build.done", "dev.failed"],
        ),
        RoleConfig(
            name="verify",
            instance_id="verify-lane-1",
            backend="mock",
            role_kind="reader",
            publishes=["verify.passed", "verify.failed"],
        ),
    ])
    config.workflow = WorkflowConfig(
        stages=[
            WorkflowStageConfig(
                id="impl",
                topology="fanout_writer_scoped",
                roles=["dev-lane-1"],
                assignment=FanoutAssignmentConfig(
                    strategy="affinity_stage_slots",
                    lane_profile="refactor-1",
                    stage_slot="impl",
                ),
            ),
            WorkflowStageConfig(
                id="verify",
                trigger="candidate.ready",
                topology="fanout_reader",
                roles=["verify-lane-1"],
                assignment=FanoutAssignmentConfig(
                    strategy="affinity_stage_slots",
                    lane_profile="refactor-1",
                    stage_slot="verify",
                ),
                on_fail=WorkflowStageBackedgeConfig(
                    event="verify.failed",
                    restart_stage="impl",
                    target_affinity="same_lane",
                    max_attempts=2,
                    feedback_artifact="verify-feedback.md",
                    emit="impl.rework.requested",
                ),
            ),
        ],
        affinity_lanes={
            "refactor-1": WorkflowAffinityLaneProfileConfig(
                affinity_key="affinity_tag",
                lanes=[
                    WorkflowAffinityLaneConfig(
                        id="lane1",
                        impl="dev-lane-1",
                        verify="verify-lane-1",
                    ),
                ],
            ),
        },
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T-CAP",
        title="verify cap",
        status="testing",
        assigned_to="verify-lane-1",
        retry_count=3,
        contract=TaskContract(behavior="b"),
    ))
    orch = Orchestrator(state_dir, config, transport)
    trigger = ZfEvent(
        type="verify.failed",
        actor="verify-lane-1",
        task_id="T-CAP",
        payload={"lane_id": "lane1", "reason": "integration contract still fails"},
    )
    orch.event_log.append(trigger)

    dispatched_role = orch._dispatch_rework(store.get("T-CAP"), trigger)

    events = orch.event_log.read_all()
    capped = next(event for event in events if event.type == "task.rework.capped")
    assert dispatched_role is None
    assert capped.payload["max_attempts"] == 2
    assert capped.payload["max_attempts_source"] == "workflow_stage_backedge"
    assert capped.payload["trigger_event_type"] == "verify.failed"
    assert "integration contract still fails" in capped.payload["last_reason"]
    assert capped.payload["failure_class"] == "product_rejection"
    assert capped.payload["recovery_scope"] == "task"
    assert capped.payload["contract_revision"] == "legacy"
    assert capped.payload["recovery_owner"] == "run_manager"
    assert not any(event.type == "task.rework.requested" for event in events)
    assert not any(event.type == "impl.rework.requested" for event in events)
    assert not (state_dir / "artifacts" / "rework-feedback" / "T-CAP").exists()


def test_resolve_returns_none_for_unknown_role(state_dir, transport):
    """Unresolvable target emits dispatch_failed + returns None."""
    config = _config(rework_routing={"review.rejected": "nobody"})
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="review", assigned_to="dev"))
    orch = Orchestrator(state_dir, config, transport)
    task = store.get("T1")

    role = orch._resolve_rework_role(task, ZfEvent(type="review.rejected"))
    assert role is None

    # Diagnostic event fired
    events = list(orch.event_log.read_all())
    assert any(
        e.type == "orchestrator.dispatch_failed" and
        "nobody" in str(e.payload)
        for e in events
    )


# -- Non-dev rework E2E --

def test_rework_routes_to_arch_for_critic_style_rejection(state_dir, transport):
    """design-first scenario: critic rejects design → arch re-works it."""
    config = _config(
        rework_routing={"review.rejected": "arch"},
        extra_roles=[
            RoleConfig(
                name="arch", backend="mock", stages=["design"],
                publishes=["arch.proposal.done", "clarification.needed"],
            ),
        ],
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1", title="design auth", status="review", assigned_to="dev",
        contract=TaskContract(behavior="b"),
    ))

    orch = Orchestrator(state_dir, config, transport)
    trigger = ZfEvent(
        type="review.rejected", actor="review", task_id="T1",
        payload={"reason": "arch question unclear"},
    )
    orch.event_log.append(trigger)
    orch.run_once(events=[trigger])

    # Briefing should have been written for arch, not dev
    briefings = list((state_dir / "briefings").glob("*.md"))
    names = [b.name for b in briefings]
    assert any("arch-T1-rework.md" == n for n in names)
    assert not any("dev-T1-rework.md" == n for n in names)

    # Briefing content references arch's completion event
    arch_briefing = next(
        b for b in briefings if b.name == "arch-T1-rework.md"
    ).read_text()
    assert "arch.proposal.done" in arch_briefing
    assert "dev.build.done" not in arch_briefing


def test_briefing_uses_inferred_success_event(state_dir, transport):
    """Rework briefing calls out the role's actual success event
    (from role.publishes), not a hardcoded 'dev.build.done'."""
    config = _config(
        rework_routing={"review.rejected": "review"},
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1", title="x", status="review", assigned_to="dev",
        contract=TaskContract(behavior="b"),
    ))

    orch = Orchestrator(state_dir, config, transport)
    trigger = ZfEvent(
        type="review.rejected", actor="review", task_id="T1",
        payload={"reason": "poor style"},
    )
    orch.event_log.append(trigger)
    orch.run_once(events=[trigger])

    briefing_path = state_dir / "briefings" / "review-T1-rework.md"
    assert briefing_path.exists()
    text = briefing_path.read_text()
    assert "review.approved" in text


def test_decision_role_reflects_actual_dispatch(state_dir, transport):
    """OrchestratorDecision.role is the role we really dispatched to,
    not a hardcoded 'dev'."""
    config = _config(
        rework_routing={"review.rejected": "review"},
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1", title="x", status="review", assigned_to="dev",
        contract=TaskContract(behavior="b"),
    ))

    orch = Orchestrator(state_dir, config, transport)
    trigger = ZfEvent(
        type="review.rejected", actor="review", task_id="T1",
    )
    orch.event_log.append(trigger)
    decisions = orch.run_once(events=[trigger])

    rework_decisions = [d for d in decisions if d.action == "dispatch"]
    assert rework_decisions, f"no dispatch decision produced: {decisions}"
    assert rework_decisions[0].role == "review"


def test_gate_failed_rework_briefing_carries_required_actions(state_dir, transport):
    """critic gate payload must become explicit arch rework instructions."""
    config = _config(
        rework_routing={"gate.failed": "arch"},
        extra_roles=[
            RoleConfig(
                name="arch", backend="mock", stages=["design"],
                publishes=["arch.proposal.done"],
            ),
        ],
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1", title="self-eval contract", status="review", assigned_to="critic",
        retry_count=1, contract=TaskContract(behavior="b"),
    ))

    orch = Orchestrator(state_dir, config, transport)
    trigger = ZfEvent(
        type="gate.failed",
        actor="critic",
        task_id="T1",
        payload={
            "summary": "Provider wrapper bypass still accepted",
            "findings": [{
                "severity": "MAJOR",
                "evidence": "contract.py accepts bash -lc 'codex exec run'",
                "required_action": "Reject bash/sh -c and env-wrapped provider calls",
            }],
        },
    )
    orch.event_log.append(trigger)

    assert orch._dispatch_rework(store.get("T1"), trigger) == "arch"

    briefing = (state_dir / "briefings" / "arch-T1-rework.md").read_text()
    assert "Required Rework Items" in briefing
    assert "Reject bash/sh -c and env-wrapped provider calls" in briefing
    assert "Provider wrapper bypass still accepted" in briefing
    assert "Trigger Payload Evidence" in briefing

    events = orch.event_log.read_all()
    rework = next(e for e in events if e.type == "task.rework.requested")
    assert "required_actions" in rework.payload
    assert any("Reject bash/sh -c" in item for item in rework.payload["required_actions"])


def test_dispatch_token_required_rejects_missing_token(state_dir, transport):
    config = _config(
        verification=VerificationConfig(
            contract=ContractDConfig(dispatch_token_required=True),
        ),
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="x",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-good",
        contract=TaskContract(behavior="login works", verification="true"),
    ))
    orch = Orchestrator(state_dir, config, transport)

    decisions = orch.run_once([
        ZfEvent(type="dev.build.done", actor="dev", task_id="T1"),
    ])

    assert decisions and decisions[0].action == "block"
    assert store.get("T1").status == "in_progress"
    assert any(e.type == "runtime.action.rejected" for e in orch.event_log.read_all())


def test_dispatch_token_required_accepts_current_token(state_dir, transport):
    config = _config(
        verification=VerificationConfig(
            contract=ContractDConfig(dispatch_token_required=True),
        ),
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="x",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-good",
        contract=TaskContract(behavior="login works", verification="true"),
    ))
    orch = Orchestrator(state_dir, config, transport)

    orch.run_once([
        ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="T1",
            payload={"dispatch_id": "disp-good"},
        ),
    ])

    assert store.get("T1").status == "review"


def test_rework_no_delta_blocks_success_event(state_dir, transport):
    config = _config(
        verification=VerificationConfig(
            contract=ContractDConfig(rework_delta_required=True),
        ),
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="x",
        status="in_progress",
        assigned_to="dev",
        retry_count=1,
        contract=TaskContract(
            behavior="login refresh returns token",
            verification="true",
            scope=["src/auth.py"],
            acceptance="refresh endpoint returns a token",
        ),
    ))
    orch = Orchestrator(state_dir, config, transport)
    orch.event_log.append(ZfEvent(
        type="task.rework.requested",
        actor="zf-cli",
        task_id="T1",
        payload={
            "required_actions": ["add regression test"],
            "base_git_head": "",
        },
    ))

    decisions = orch.run_once([
        ZfEvent(type="dev.build.done", actor="dev", task_id="T1", payload={}),
    ])

    assert decisions and decisions[0].action == "block"
    assert store.get("T1").status == "in_progress"
    failed = [e for e in orch.event_log.read_all() if e.type == "discriminator.failed"]
    assert failed
    assert failed[-1].payload["failed_d"] == ["ReworkDeltaD"]


def test_rework_delta_payload_allows_success_event(state_dir, transport):
    config = _config(
        verification=VerificationConfig(
            contract=ContractDConfig(rework_delta_required=True),
        ),
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="x",
        status="in_progress",
        assigned_to="dev",
        retry_count=1,
        contract=TaskContract(
            behavior="login refresh returns token",
            verification="true",
            scope=["src/auth.py"],
            acceptance="refresh endpoint returns a token",
        ),
    ))
    orch = Orchestrator(state_dir, config, transport)
    orch.event_log.append(ZfEvent(
        type="task.rework.requested",
        actor="zf-cli",
        task_id="T1",
        payload={"required_actions": ["add regression test"]},
    ))

    orch.run_once([
        ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="T1",
            payload={
                "artifact_refs": ["src/auth.py", "tests/test_auth.py"],
                "required_actions_completed": ["add regression test"],
            },
        ),
    ])

    assert store.get("T1").status == "review"


def test_rework_dirty_baseline_does_not_count_as_new_delta(state_dir, transport):
    from zf.runtime.git_capture import capture_git_diff_context

    project_root = state_dir.parent
    _git(project_root, "init", "-q")
    _git(project_root, "config", "user.email", "test@example.com")
    _git(project_root, "config", "user.name", "Test User")
    src_dir = project_root / "src"
    src_dir.mkdir()
    auth_path = src_dir / "auth.py"
    auth_path.write_text("token = 'old'\n", encoding="utf-8")
    _git(project_root, "add", "src/auth.py")
    _git(project_root, "commit", "-q", "-m", "init")
    base = _git(project_root, "rev-parse", "HEAD")
    auth_path.write_text("token = 'failed attempt'\n", encoding="utf-8")
    context = capture_git_diff_context(project_root, base_sha=base)

    config = _config(
        verification=VerificationConfig(
            contract=ContractDConfig(rework_delta_required=True),
        ),
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="x",
        status="in_progress",
        assigned_to="dev",
        retry_count=1,
    ))
    orch = Orchestrator(state_dir, config, transport)
    orch.event_log.append(ZfEvent(
        type="task.rework.requested",
        actor="zf-cli",
        task_id="T1",
        payload={
            "base_git_head": base,
            "base_files_touched": context.files_touched,
            "base_diff_hash": context.diff_hash,
        },
    ))

    decisions = orch.run_once([
        ZfEvent(type="dev.build.done", actor="dev", task_id="T1", payload={}),
    ])

    assert decisions and decisions[0].action == "block"
    assert store.get("T1").status == "in_progress"


def test_rework_dirty_delta_after_baseline_allows_success(state_dir, transport):
    from zf.runtime.git_capture import capture_git_diff_context

    project_root = state_dir.parent
    _git(project_root, "init", "-q")
    _git(project_root, "config", "user.email", "test@example.com")
    _git(project_root, "config", "user.name", "Test User")
    src_dir = project_root / "src"
    src_dir.mkdir()
    auth_path = src_dir / "auth.py"
    auth_path.write_text("token = 'old'\n", encoding="utf-8")
    _git(project_root, "add", "src/auth.py")
    _git(project_root, "commit", "-q", "-m", "init")
    base = _git(project_root, "rev-parse", "HEAD")
    auth_path.write_text("token = 'failed attempt'\n", encoding="utf-8")
    context = capture_git_diff_context(project_root, base_sha=base)
    auth_path.write_text("token = 'fixed attempt'\n", encoding="utf-8")

    config = _config(
        verification=VerificationConfig(
            contract=ContractDConfig(rework_delta_required=True),
        ),
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="x",
        status="in_progress",
        assigned_to="dev",
        retry_count=1,
    ))
    orch = Orchestrator(state_dir, config, transport)
    orch.event_log.append(ZfEvent(
        type="task.rework.requested",
        actor="zf-cli",
        task_id="T1",
        payload={
            "base_git_head": base,
            "base_files_touched": context.files_touched,
            "base_diff_hash": context.diff_hash,
        },
    ))

    orch.run_once([
        ZfEvent(type="dev.build.done", actor="dev", task_id="T1", payload={}),
    ])

    assert store.get("T1").status == "review"


def test_rework_required_actions_must_be_covered(state_dir, transport):
    config = _config(
        verification=VerificationConfig(
            contract=ContractDConfig(rework_delta_required=True),
        ),
    )
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="T1",
        title="x",
        status="in_progress",
        assigned_to="dev",
        retry_count=1,
        contract=TaskContract(
            behavior="login refresh returns token",
            verification="true",
            scope=["src/auth.py"],
            acceptance="refresh endpoint returns a token",
        ),
    ))
    orch = Orchestrator(state_dir, config, transport)
    orch.event_log.append(ZfEvent(
        type="task.rework.requested",
        actor="zf-cli",
        task_id="T1",
        payload={"required_actions": ["add regression test"]},
    ))

    decisions = orch.run_once([
        ZfEvent(
            type="dev.build.done",
            actor="dev",
            task_id="T1",
            payload={
                "artifact_refs": ["src/auth.py"],
                "required_actions_completed": ["refactor auth helper"],
            },
        ),
    ])

    assert decisions and decisions[0].action == "block"
    assert store.get("T1").status == "in_progress"
    events = orch.event_log.read_all()
    assert any(e.type == "task.rework.blocked" for e in events)


# -- Config loader round-trip --

def test_loader_reads_rework_routing_from_yaml(tmp_path):
    """zf.yaml workflow.rework_routing → WorkflowConfig.rework_routing."""
    from zf.core.config.loader import load_config

    yaml_path = tmp_path / "zf.yaml"
    yaml_path.write_text("""
version: "1.0"
project:
  name: rework-test
workflow:
  rework_routing:
    review.rejected: arch
    test.failed: dev
    critic.plan.rejected: arch
roles:
  - name: dev
    publishes: [dev.build.done]
  - name: arch
    publishes: [arch.proposal.done]
""")
    config = load_config(yaml_path)
    assert config.workflow.rework_routing == {
        "review.rejected": "arch",
        "test.failed": "dev",
        "critic.plan.rejected": "arch",
    }


def test_backward_compat_no_yaml_routing_uses_dev(tmp_path):
    """Existing YAMLs without rework_routing still default to dev."""
    from zf.core.config.loader import load_config

    yaml_path = tmp_path / "zf.yaml"
    yaml_path.write_text("""
version: "1.0"
project:
  name: legacy
roles:
  - name: dev
    publishes: [dev.build.done]
""")
    config = load_config(yaml_path)
    assert config.workflow.rework_routing == {}


def test_task_contract_rework_to_optional():
    """TaskContract.rework_to defaults to empty string (optional field)."""
    tc = TaskContract()
    assert tc.rework_to == ""
    tc2 = TaskContract(rework_to="arch")
    assert tc2.rework_to == "arch"
