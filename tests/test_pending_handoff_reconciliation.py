from __future__ import annotations

import subprocess
from pathlib import Path

from zf.core.config.schema import (
    ContractDConfig,
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    SessionConfig,
    VerificationConfig,
    WorkdirConfig,
    WorkflowAffinityLaneConfig,
    WorkflowAffinityLaneProfileConfig,
    WorkflowConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator


class _StubTransport:
    def __init__(self) -> None:
        self.sends: list[str] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sends.append(role_name)

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""


def _make_orchestrator(tmp_path: Path) -> tuple[Orchestrator, TaskStore, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n")

    store = TaskStore(state_dir / "kanban.json")
    log = EventLog(state_dir / "events.jsonl")
    config = ZfConfig(
        project=ProjectConfig(name="x", state_dir=str(state_dir)),
        session=SessionConfig(tmux_session="zf-test"),
        roles=[
            RoleConfig(
                name="orchestrator",
                backend="mock",
                triggers=[
                    "user.message",
                    "dev.build.done",
                    "review.approved",
                    "test.passed",
                    "judge.passed",
                ],
            ),
            RoleConfig(
                name="dev",
                backend="mock",
                publishes=["dev.build.done"],
                triggers=["task.assigned"],
            ),
            RoleConfig(
                name="review",
                backend="mock",
                publishes=["review.approved", "review.rejected"],
                triggers=["dev.build.done"],
            ),
            RoleConfig(
                name="test",
                backend="mock",
                replicas=2,
                publishes=["test.passed", "test.failed"],
                triggers=["review.approved"],
            ),
            RoleConfig(
                name="judge",
                backend="mock",
                publishes=["judge.passed", "judge.failed"],
                triggers=["test.passed"],
            ),
        ],
        workflow=WorkflowConfig(),
        verification=VerificationConfig(
            contract=ContractDConfig(dispatch_token_required=True),
        ),
    )
    orch = Orchestrator(state_dir, config, _StubTransport())  # type: ignore[arg-type]
    return orch, store, log


def _events(log: EventLog) -> list[ZfEvent]:
    return log.read_all()


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _judge_payload(dispatch_id: str, *, tier: str) -> dict:
    return {
        "summary": "judge pass",
        "dispatch_id": dispatch_id,
        "checks": [
            {"command": "pnpm test", "exit_code": 0, "tier": tier},
        ],
        "scores": {
            "correctness": "pass",
            "completeness": "pass",
            "regression_risk": "pass",
            "evidence_quality": "pass",
        },
        "artifact_refs": ["src/example.ts"],
        "evidence_refs": ["pnpm test"],
    }


def _fanout_started(fanout_id: str, *, task_id: str = "T1") -> ZfEvent:
    return ZfEvent(
        type="fanout.started",
        actor="zf-cli",
        task_id=task_id,
        payload={
            "fanout_id": fanout_id,
            "stage_id": "review-candidate",
            "topology": "fanout_reader",
            "target_ref": "candidate/CJMIN-1",
            "pdd_id": "CJMIN-1",
            "expected_children": [{"child_id": "review-a"}],
        },
    )


def test_reconcile_routes_all_completed_dev_tasks_to_review(tmp_path: Path) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    for task_id, assignee in [
        ("T-MATH", "dev-1"),
        ("T-TEXT", "dev-2"),
        ("T-LIST", "dev-2"),
    ]:
        store.add(Task(id=task_id, title=task_id, status="in_progress",
                       assigned_to=assignee))
        log.append(ZfEvent(type="task.assigned", actor="zf-cli",
                           task_id=task_id,
                           payload={"assignee": "dev", "role": "dev"}))
        log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                           task_id=task_id,
                           payload={"assignee": assignee, "role": "dev"}))
        log.append(ZfEvent(type="dev.build.done", actor=assignee,
                           task_id=task_id))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert {d.task_id for d in decisions} == {"T-MATH", "T-TEXT", "T-LIST"}
    assert all(store.get(t).assigned_to == "review" for t in [  # type: ignore[union-attr]
        "T-MATH", "T-TEXT", "T-LIST",
    ])
    assigned_to_review = [
        e.task_id for e in _events(log)
        if e.type == "task.assigned"
        and e.payload.get("assignee") == "review"
    ]
    assert assigned_to_review == ["T-MATH", "T-TEXT", "T-LIST"]


def test_reconcile_routes_mixed_pending_build_and_review_events(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    for task_id, assignee in [
        ("T1", "dev-1"),
        ("T2", "dev-2"),
        ("T3", "dev-2"),
    ]:
        store.add(Task(id=task_id, title=task_id, status="in_progress",
                       assigned_to=assignee))
        log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                           task_id=task_id,
                           payload={"assignee": assignee, "role": "dev"}))
        log.append(ZfEvent(type="dev.build.done", actor=assignee,
                           task_id=task_id))

    log.append(ZfEvent(type="task.assigned", actor="zf-cli", task_id="T2",
                       payload={"assignee": "review", "role": "review"}))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T2",
                       payload={"assignee": "review", "role": "review"}))
    log.append(ZfEvent(type="review.approved", actor="review", task_id="T2"))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    by_task = {d.task_id: d.role for d in decisions}
    assert by_task == {"T1": "review", "T2": "test", "T3": "review"}
    assert store.get("T1").assigned_to == "review"  # type: ignore[union-attr]
    assert store.get("T2").assigned_to == "test"  # type: ignore[union-attr]
    assert store.get("T3").assigned_to == "review"  # type: ignore[union-attr]


def test_reconcile_routes_static_gate_passed_to_review(tmp_path: Path) -> None:
    """B-NEW-4 regression: P3 added static_gate as an independent DAG
    stage between dev and review. P4 yaml then moved
    ``review.triggers`` from ``[dev.build.done]`` to
    ``[static_gate.passed]``. Without adding static_gate.passed to
    ``_HANDOFF_SUCCESS_EVENTS``, the reconciler never picks up a
    static_gate.passed event and the task strands silently — exactly
    the cangjie r-next-4/r-next-5 hand-off we kept manually rescuing
    with ``zf kanban assign <task> review``.

    Pin: after dev.build.done → static_gate.passed sequence, the
    reconciler must auto-route to review.
    """
    orch, store, log = _make_orchestrator(tmp_path)
    # Simulate the P4 yaml setup: review now triggers on static_gate.passed
    # instead of dev.build.done.
    for role in orch.config.roles:
        if role.name == "review":
            role.triggers = ["static_gate.passed"]

    store.add(Task(id="T-GATE", title="T-GATE", status="in_progress",
                   assigned_to="dev-1"))
    log.append(ZfEvent(type="task.assigned", actor="zf-cli",
                       task_id="T-GATE",
                       payload={"assignee": "dev", "role": "dev"}))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T-GATE",
                       payload={"assignee": "dev-1", "role": "dev"}))
    log.append(ZfEvent(type="dev.build.done", actor="dev-1",
                       task_id="T-GATE"))
    log.append(ZfEvent(type="static_gate.passed", actor="zf-cli",
                       task_id="T-GATE",
                       payload={"role": "dev", "checks_passed": 3}))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert {d.task_id for d in decisions} == {"T-GATE"}
    assert store.get("T-GATE").assigned_to == "review"  # type: ignore[union-attr]
    new_review_assigns = [
        e for e in _events(log)
        if e.type == "task.assigned"
        and e.task_id == "T-GATE"
        and e.payload.get("assignee") == "review"
    ]
    assert new_review_assigns, (
        "expected reconciler to emit task.assigned → review after "
        "static_gate.passed (B-NEW-4 fix); got nothing"
    )


def test_reconcile_dispatches_task_ref_repair_request_to_owner_lane(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.roles.append(RoleConfig(
        name="dev-lane-0",
        backend="mock",
        role_kind="writer",
        publishes=["dev.build.done", "dev.failed"],
        triggers=["task.assigned"],
    ))
    store.add(Task(
        id="T-REF",
        title="task ref repair",
        status="in_progress",
        assigned_to="dev-lane-0",
        retry_count=1,
        active_dispatch_id="disp-dev",
        contract=TaskContract(
            behavior="repair writer task ref",
            owner_role="dev-lane-0",
            rework_to="dev-lane-0",
        ),
    ))
    dev_done = ZfEvent(
        type="dev.build.done",
        actor="dev-lane-0",
        task_id="T-REF",
        payload={"dispatch_id": "disp-dev"},
    )
    log.append(dev_done)
    rejected = ZfEvent(
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="T-REF",
        payload={
            "trigger_event_id": dev_done.id,
            "reason": "workdir has uncommitted changes",
        },
        causation_id=dev_done.id,
    )
    log.append(rejected)
    repair = ZfEvent(
        type="task.ref.repair.requested",
        actor="zf-cli",
        task_id="T-REF",
        payload={
            "source_event_id": dev_done.id,
            "blocking_event_id": rejected.id,
            "reason": "repair task ref handoff",
        },
        causation_id=dev_done.id,
    )
    log.append(repair)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    task = store.get("T-REF")
    events = _events(log)
    assert len(decisions) == 1
    assert decisions[0].action == "dispatch"
    assert decisions[0].role == "dev-lane-0"
    assert task is not None
    assert task.status == "in_progress"
    assert task.assigned_to == "dev-lane-0"
    assert any(
        event.type == "task.rework.requested"
        and event.payload.get("trigger_event_id") == repair.id
        for event in events
    )
    assert any(
        event.type == "task.dispatched"
        and event.payload.get("source") == "rework"
        and event.payload.get("trigger_event") == "task.ref.repair.requested"
        for event in events
    )
    briefing = (
        tmp_path / ".zf" / "briefings" / "dev-lane-0-T-REF-rework.md"
    ).read_text(encoding="utf-8")
    assert "## Task Ref Repair Handoff Contract" in briefing
    assert "`source_commit`, `source_branch`, `workdir`" in briefing
    assert "Do not put `git:<sha>`" in briefing
    assert "`.codex/hooks.json`" in briefing


def test_reconcile_emits_task_ref_repair_request_after_rejection(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.roles.append(RoleConfig(
        name="dev-lane-0",
        backend="mock",
        role_kind="writer",
        publishes=["dev.build.done", "dev.failed"],
        triggers=["task.assigned"],
    ))
    store.add(Task(
        id="T-REF",
        title="task ref repair",
        status="in_progress",
        assigned_to="dev-lane-0",
        retry_count=1,
        active_dispatch_id="disp-dev",
        contract=TaskContract(
            behavior="repair writer task ref",
            owner_role="dev-lane-0",
            rework_to="dev-lane-0",
        ),
    ))
    dev_done = ZfEvent(
        type="dev.build.done",
        actor="dev-lane-0",
        task_id="T-REF",
        payload={"dispatch_id": "disp-dev"},
    )
    log.append(dev_done)
    rejected = ZfEvent(
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="T-REF",
        payload={
            "trigger_event_id": dev_done.id,
            "reason": "workdir has uncommitted changes",
            "source_commit": "abc123",
            "source_branch": "task/T-REF",
            "workdir": str(tmp_path / ".zf" / "workdirs" / "dev-lane-0"),
            "dirty_files": ["packages/core/package.json"],
        },
        causation_id=dev_done.id,
    )
    log.append(rejected)

    first = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]
    second = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    events = _events(log)
    repair_events = [
        event for event in events
        if event.type == "task.ref.repair.requested"
    ]
    assert len(repair_events) == 1
    assert repair_events[0].payload["blocking_event_id"] == rejected.id
    assert repair_events[0].payload["dirty_files"] == ["packages/core/package.json"]
    assert repair_events[0].payload["expected_action"] == (
        "commit_or_revert_dirty_files_and_reemit_handoff"
    )
    assert len(first) == 1
    assert first[0].role == "dev-lane-0"
    assert second == []
    assert any(
        event.type == "task.dispatched"
        and event.payload.get("trigger_event") == "task.ref.repair.requested"
        for event in events
    )


def test_task_ref_scope_rejection_requests_source_scope_repair(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.roles.append(RoleConfig(
        name="dev-lane-0",
        backend="mock",
        role_kind="writer",
        publishes=["dev.build.done", "dev.failed"],
        triggers=["task.assigned"],
    ))
    store.add(Task(
        id="T-SCOPE",
        title="task ref source scope repair",
        status="in_progress",
        assigned_to="dev-lane-0",
        retry_count=1,
        active_dispatch_id="disp-dev",
        contract=TaskContract(
            behavior="repair source scope rejected handoff",
            owner_role="dev-lane-0",
            rework_to="dev-lane-0",
        ),
    ))
    dev_done = ZfEvent(
        type="dev.build.done",
        actor="dev-lane-0",
        task_id="T-SCOPE",
        payload={"dispatch_id": "disp-dev"},
    )
    log.append(dev_done)
    rejected = ZfEvent(
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="T-SCOPE",
        payload={
            "trigger_event_id": dev_done.id,
            "reason": "source_commit changes outside task contract scope",
            "source_commit": "bad-source-commit",
            "scope": ["packages/gateway/src/**"],
            "changed_files": [
                "packages/gateway/src/index.ts",
                "packages/web-adapter/src/index.ts",
            ],
            "out_of_scope_files": ["packages/web-adapter/src/index.ts"],
        },
        causation_id=dev_done.id,
    )
    log.append(rejected)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    events = _events(log)
    repair_events = [
        event for event in events
        if event.type == "task.ref.repair.requested"
        and event.task_id == "T-SCOPE"
    ]
    assert len(repair_events) == 1
    assert repair_events[0].payload["expected_action"] == (
        "split_or_rebase_source_commit_and_reemit_handoff"
    )
    assert repair_events[0].payload["out_of_scope_files"] == [
        "packages/web-adapter/src/index.ts"
    ]
    rework_events = [
        event for event in events
        if event.type == "task.rework.requested"
        and event.task_id == "T-SCOPE"
    ]
    assert len(rework_events) == 1
    assert any(
        "Produce a new source_commit" in item
        for item in rework_events[0].payload["required_actions"]
    )
    assert len(decisions) == 1
    assert decisions[0].action == "dispatch"
    briefing = (
        tmp_path / ".zf" / "briefings" / "dev-lane-0-T-SCOPE-rework.md"
    ).read_text(encoding="utf-8")
    assert "## Task Ref Source Scope Repair Contract" in briefing
    assert "Do not emit a metadata-only repair" in briefing
    assert "do not reuse the rejected `source_commit`" in briefing
    assert "packages/web-adapter/src/index.ts" in briefing


def test_task_ref_repair_uses_writer_lane_when_reader_currently_assigned(
    tmp_path: Path,
) -> None:
    """R34 regression: task-ref repair is a writer-lane repair.

    A rejected writer handoff may leave the task currently assigned to a reader
    role (critic/review/verify) or carry a lane-pipeline contract with
    ``rework_to: impl``. The repair must still dispatch to the writer lane that
    produced the rejected handoff, not to the reader and not to an abstract
    stage alias.
    """

    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.roles.extend([
        RoleConfig(
            name="dev-lane-1",
            backend="mock",
            role_kind="writer",
            publishes=["dev.build.done", "dev.failed"],
            triggers=["task.assigned"],
        ),
        RoleConfig(
            name="critic",
            backend="mock",
            role_kind="reader",
            publishes=["review.approved", "review.rejected"],
            triggers=["dev.build.done"],
        ),
    ])
    store.add(Task(
        id="T-REF-CRITIC",
        title="task ref repair from reader-held task",
        status="in_progress",
        assigned_to="critic",
        retry_count=1,
        active_dispatch_id="disp-reader",
        contract=TaskContract(
            behavior="repair rejected writer handoff",
            owner_role="impl",
            rework_to="impl",
        ),
    ))
    dev_done = ZfEvent(
        type="dev.build.done",
        actor="dev-lane-1",
        task_id="T-REF-CRITIC",
        payload={"dispatch_id": "disp-dev"},
    )
    log.append(dev_done)
    log.append(ZfEvent(
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="T-REF-CRITIC",
        payload={
            "trigger_event_id": dev_done.id,
            "reason": "workdir has uncommitted changes",
            "workdir": str(tmp_path / ".zf" / "workdirs" / "dev-lane-1" / "project"),
            "dirty_files": ["packages/core/package.json"],
        },
        causation_id=dev_done.id,
    ))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    events = _events(log)
    task = store.get("T-REF-CRITIC")
    assert [decision.role for decision in decisions] == ["dev-lane-1"]
    assert task is not None
    assert task.assigned_to == "dev-lane-1"
    repair_events = [
        event for event in events
        if event.type == "task.ref.repair.requested"
    ]
    assert len(repair_events) == 1
    assert repair_events[0].payload["target_assignee"] == "dev-lane-1"
    assert any(
        event.type == "task.rework.requested"
        and event.payload.get("assignee") == "dev-lane-1"
        and event.payload.get("role") == "dev-lane-1"
        for event in events
    )
    assert not [
        event for event in events
        if event.type == "task.rework.requested"
        and event.payload.get("assignee") == "critic"
    ]


def test_reconcile_dispatches_task_ref_repair_to_blocked_owner_lane(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.roles.append(RoleConfig(
        name="dev-lane-0",
        backend="mock",
        role_kind="writer",
        publishes=["dev.build.done", "dev.failed"],
        triggers=["task.assigned"],
    ))
    orch._last_worker_state["dev-lane-0"] = "blocked_human"  # type: ignore[attr-defined]
    store.add(Task(
        id="T-REF",
        title="task ref repair",
        status="in_progress",
        assigned_to="dev-lane-0",
        retry_count=1,
        active_dispatch_id="disp-dev",
        contract=TaskContract(
            behavior="repair writer task ref",
            owner_role="dev-lane-0",
            rework_to="dev-lane-0",
        ),
    ))
    dev_done = ZfEvent(
        type="dev.build.done",
        actor="dev-lane-0",
        task_id="T-REF",
        payload={"dispatch_id": "disp-dev"},
    )
    log.append(dev_done)
    rejected = ZfEvent(
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="T-REF",
        payload={
            "trigger_event_id": dev_done.id,
            "reason": "source_commit changes outside task contract scope",
        },
        causation_id=dev_done.id,
    )
    log.append(rejected)
    repair = ZfEvent(
        type="task.ref.repair.requested",
        actor="zf-cli",
        task_id="T-REF",
        payload={
            "source_event_id": dev_done.id,
            "blocking_event_id": rejected.id,
            "reason": "repair task ref handoff",
        },
        causation_id=dev_done.id,
    )
    log.append(repair)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    events = _events(log)
    assert len(decisions) == 1
    assert decisions[0].action == "dispatch"
    assert decisions[0].role == "dev-lane-0"
    assert any(
        event.type == "task.dispatched"
        and event.payload.get("source") == "rework"
        and event.payload.get("trigger_event") == "task.ref.repair.requested"
        for event in events
    )
    assert not [
        event for event in events
        if event.type == "orchestrator.dispatch_skipped"
        and event.payload.get("reason") == "rework_target_not_dispatchable"
    ]


def test_reconcile_routes_static_gate_passed_without_dispatch_id(
    tmp_path: Path,
) -> None:
    """B-NEW-9 regression: ``_progress_event_matches_active_dispatch_at``
    used to reject any kernel-emitted progress event whose payload lacked
    a ``dispatch_id`` (returned actual="" != expected="disp-..."), which
    caused the reconciler to silently skip ``static_gate.passed`` events
    in cangjie r-next-7. Two layers of fix:

      1. static_gate.py now inherits dispatch_id from trigger (preferred);
      2. ``_progress_event_matches_active_dispatch_at`` fails open for
         actor=zf-cli events that lack dispatch_id but have a matching
         backward task.dispatched (defense in depth).

    This test exercises layer 2: explicitly emit static_gate.passed with
    actor=zf-cli and no dispatch_id, verify reconciler still routes to
    review.
    """
    orch, store, log = _make_orchestrator(tmp_path)
    for role in orch.config.roles:
        if role.name == "review":
            role.triggers = ["static_gate.passed"]

    store.add(Task(id="T-KERNEL", title="T-KERNEL", status="in_progress",
                   assigned_to="dev-1"))
    log.append(ZfEvent(type="task.assigned", actor="zf-cli",
                       task_id="T-KERNEL",
                       payload={"assignee": "dev", "role": "dev"}))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T-KERNEL",
                       payload={"assignee": "dev-1", "role": "dev",
                                "dispatch_id": "disp-XYZ"}))
    log.append(ZfEvent(type="dev.build.done", actor="dev-1",
                       task_id="T-KERNEL",
                       payload={"dispatch_id": "disp-XYZ"}))
    # static_gate.passed deliberately WITHOUT dispatch_id (the bug scenario)
    log.append(ZfEvent(type="static_gate.passed", actor="zf-cli",
                       task_id="T-KERNEL",
                       payload={"passed": True, "check_count": 3}))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert {d.task_id for d in decisions} == {"T-KERNEL"}, (
        "expected reconciler to route static_gate.passed → review even when "
        "the event lacks a dispatch_id (kernel emit pattern); got "
        f"{[d.task_id for d in decisions]}"
    )


def test_reconcile_routes_static_gate_skipped_passed_to_review(
    tmp_path: Path,
) -> None:
    """Disabled/per-task-skipped static gate is still a passed gate.

    Cangjie TASK-A4A7FB emitted static_gate.skipped with
    ``passed=true, skipped=true`` because static checks were disabled for the
    spec-only baseline. Review subscribes to static_gate.passed, so the
    reconciler must treat that passed skip as an equivalent wake event.
    """
    orch, store, log = _make_orchestrator(tmp_path)
    for role in orch.config.roles:
        if role.name == "review":
            role.triggers = ["static_gate.passed"]

    store.add(Task(id="T-SKIP", title="T-SKIP", status="in_progress",
                   assigned_to="dev-1"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T-SKIP",
                       payload={"assignee": "dev-1", "role": "dev",
                                "dispatch_id": "disp-SKIP"}))
    log.append(ZfEvent(type="dev.build.done", actor="dev-1",
                       task_id="T-SKIP",
                       payload={"dispatch_id": "disp-SKIP"}))
    log.append(ZfEvent(type="static_gate.skipped", actor="zf-cli",
                       task_id="T-SKIP",
                       payload={"passed": True, "skipped": True,
                                "skip_reason": "quality_gates.static.enabled=False",
                                "dispatch_id": "disp-SKIP"}))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert {d.task_id for d in decisions} == {"T-SKIP"}
    assert store.get("T-SKIP").assigned_to == "review"  # type: ignore[union-attr]
    review_assigns = [
        e for e in _events(log)
        if e.type == "task.assigned"
        and e.task_id == "T-SKIP"
        and e.payload.get("assignee") == "review"
    ]
    assert review_assigns
    assert review_assigns[-1].payload["trigger_event"] == "static_gate.skipped"
    assert (
        review_assigns[-1].payload["effective_trigger_event"]
        == "static_gate.passed"
    )


def test_reconcile_ignores_static_gate_skipped_without_passed_payload(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    for role in orch.config.roles:
        if role.name == "review":
            role.triggers = ["static_gate.passed"]

    store.add(Task(id="T-BAD-SKIP", title="T-BAD-SKIP", status="in_progress",
                   assigned_to="dev-1"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T-BAD-SKIP",
                       payload={"assignee": "dev-1", "role": "dev",
                                "dispatch_id": "disp-BAD-SKIP"}))
    log.append(ZfEvent(type="static_gate.skipped", actor="zf-cli",
                       task_id="T-BAD-SKIP",
                       payload={"passed": False, "skipped": True,
                                "dispatch_id": "disp-BAD-SKIP"}))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert "T-BAD-SKIP" not in {d.task_id for d in decisions}
    assert store.get("T-BAD-SKIP").assigned_to == "dev-1"  # type: ignore[union-attr]


def test_reconcile_rejects_static_gate_passed_after_requeue(
    tmp_path: Path,
) -> None:
    """Belt: the fail-open for kernel-emitted events still respects
    task.requeued — if a requeue intervened between dispatch and
    progress event, the progress event is stale and must not route.
    """
    orch, store, log = _make_orchestrator(tmp_path)
    for role in orch.config.roles:
        if role.name == "review":
            role.triggers = ["static_gate.passed"]

    store.add(Task(id="T-REQ", title="T-REQ", status="in_progress",
                   assigned_to="dev-1"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T-REQ",
                       payload={"assignee": "dev-1", "role": "dev",
                                "dispatch_id": "disp-OLD"}))
    log.append(ZfEvent(type="task.requeued", actor="zf-cli",
                       task_id="T-REQ",
                       payload={"source": "worker_stuck_recovery"}))
    # Then a stale static_gate.passed without dispatch_id — must NOT route
    log.append(ZfEvent(type="static_gate.passed", actor="zf-cli",
                       task_id="T-REQ",
                       payload={"passed": True}))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert "T-REQ" not in {d.task_id for d in decisions}, (
        "kernel-emit fail-open must still respect task.requeued — stale "
        "static_gate.passed after a requeue should NOT route to review"
    )


def test_reconcile_ignores_stale_fanout_success_progress(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="review"))
    log.append(_fanout_started("fanout-review-old"))
    log.append(ZfEvent(
        type="review.approved",
        actor="zf-cli",
        task_id="T1",
        payload={
            "fanout_id": "fanout-review-old",
            "stage_id": "review-candidate",
            "target_ref": "candidate/CJMIN-1",
        },
    ))
    log.append(_fanout_started("fanout-review-new"))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert decisions == []
    assert store.get("T1").assigned_to == "review"  # type: ignore[union-attr]
    assert not [
        event for event in _events(log)
        if event.type == "task.assigned"
        and event.task_id == "T1"
        and event.payload.get("assignee") == "test"
    ]


def test_reconcile_ignores_stale_fanout_rework_trigger(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="review"))
    log.append(_fanout_started("fanout-review-old"))
    stale_reject = ZfEvent(
        type="review.rejected",
        actor="zf-cli",
        task_id="T1",
        payload={
            "fanout_id": "fanout-review-old",
            "stage_id": "review-candidate",
            "target_ref": "candidate/CJMIN-1",
        },
    )
    log.append(stale_reject)
    log.append(_fanout_started("fanout-review-new"))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert decisions == []
    assert store.get("T1").assigned_to == "review"  # type: ignore[union-attr]
    assert orch._latest_rework_trigger_event("T1") is None  # type: ignore[attr-defined]
    assert not [
        event for event in _events(log)
        if event.type == "task.rework.requested"
        and event.payload.get("trigger_event_id") == stale_reject.id
    ]


def test_reconcile_does_not_duplicate_existing_handoff(tmp_path: Path) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="review"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "dev-1", "role": "dev"}))
    log.append(ZfEvent(type="dev.build.done", actor="dev-1", task_id="T1"))
    log.append(ZfEvent(type="task.assigned", actor="zf-cli", task_id="T1",
                       payload={"assignee": "review", "role": "review"}))

    assert orch._reconcile_pending_handoffs() == []  # type: ignore[attr-defined]
    assigned_to_review = [
        e for e in _events(log)
        if e.type == "task.assigned"
        and e.payload.get("assignee") == "review"
    ]
    assert len(assigned_to_review) == 1


def test_reconcile_ignores_duplicate_success_for_same_dispatch(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="review"))
    payload = {"dispatch_id": "disp-abc"}
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "dev-1", "role": "dev",
                                "dispatch_id": "disp-abc"}))
    log.append(ZfEvent(type="dev.build.done", actor="dev-1",
                       task_id="T1", payload=payload))
    log.append(ZfEvent(type="task.assigned", actor="zf-cli", task_id="T1",
                       payload={"assignee": "review", "role": "review"}))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "review", "role": "review"}))
    log.append(ZfEvent(type="dev.build.done", actor="dev-1",
                       task_id="T1", payload=payload))

    assert orch._reconcile_pending_handoffs() == []  # type: ignore[attr-defined]
    assigned_to_review = [
        e for e in _events(log)
        if e.type == "task.assigned"
        and e.payload.get("assignee") == "review"
    ]
    assert len(assigned_to_review) == 1


def test_reconcile_skips_progress_event_already_delivered_to_layer2(
    tmp_path: Path,
) -> None:
    """B-NEW-1 (backlogs/2026-05-16-0052-...): the OLD skip rule was 'processed
    means delivered', which strands handoffs when orchestrator emits
    orchestrator.idle without dispatching. The corrected rule is 'processed
    AND a subsequent task.assigned/dispatched fired'. Seed both events here
    so the skip path is still tested for the legitimate case."""
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="dev"))
    progress = ZfEvent(type="dev.build.done", actor="dev", task_id="T1")
    log.append(progress)
    orch._processed_event_ids.add(progress.id)
    # Subsequent successful handoff to review (this is what the OLD test was
    # implicitly assuming had happened):
    log.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id="T1",
        payload={"role": "review", "assignee": "review"},
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
        payload={"role": "review", "assignee": "review"},
    ))

    assert orch._reconcile_pending_handoffs() == []  # type: ignore[attr-defined]
    assert store.get("T1").assigned_to == "dev"  # type: ignore[union-attr]


def test_reconcile_dispatches_when_processed_but_no_subsequent_assign(
    tmp_path: Path,
) -> None:
    """B-NEW-1 fix: when progress event was processed (e.g. orchestrator LLM
    emitted orchestrator.idle) but NO subsequent task.assigned/dispatched
    occurred, the reconciler MUST step in and dispatch the next role.

    Cangjie F-924216 observed: 15:26:47 test.passed → 15:27:24
    orchestrator.idle ("test.passed auto-routes to judge per topology;
    no orchestrator action required") → reconciler skipped (old buggy
    behavior) → judge stranded for 7 minutes until manual
    `zf kanban assign judge`.
    """
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="test"))
    progress = ZfEvent(type="test.passed", actor="test-1", task_id="T1")
    log.append(progress)
    # Simulate the cangjie scenario: orchestrator processed the event but
    # decided "no action required" — no task.assigned / task.dispatched
    # fired after.
    orch._processed_event_ids.add(progress.id)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]
    # Expect reconciler to dispatch judge.
    assert any(
        d.action == "assign" and d.role == "judge"
        for d in decisions
    ), f"expected judge dispatch via reconciler; got {decisions}"


def test_reconcile_recovers_after_layer2_handoff_dispatch_failed(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="review"))
    progress = ZfEvent(type="review.approved", actor="review", task_id="T1")
    log.append(progress)
    orch._processed_event_ids.add(progress.id)
    log.append(ZfEvent(
        type="orchestrator.dispatch_failed",
        actor="orchestrator",
        task_id="T1",
        payload={"reason": "bad JSON while handing off to test"},
    ))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].role == "test"
    assert store.get("T1").assigned_to == "test"  # type: ignore[union-attr]
    assert any(
        e.type == "task.assigned"
        and e.task_id == "T1"
        and e.payload.get("assignee") == "test"
        for e in _events(log)
    )


def test_reconcile_terminal_judge_passed_closes_task(tmp_path: Path) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="judge"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "judge", "role": "judge"}))
    log.append(ZfEvent(type="judge.passed", actor="judge", task_id="T1"))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "move"
    assert store.get("T1").status == "done"  # archived lookup
    assert any(
        e.type == "task.status_changed"
        and e.task_id == "T1"
        and e.payload.get("to") == "done"
        for e in _events(log)
    )


def test_reconcile_terminal_test_passed_closes_task_without_judge_role(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.roles = [
        role for role in orch.config.roles if role.name not in {"review", "test", "judge"}
    ] + [
        RoleConfig(
            name="qa",
            backend="mock",
            publishes=["test.passed", "test.failed"],
            triggers=["static_gate.passed"],
        )
    ]
    dispatch_id = "disp-qa"
    store.add(Task(
        id="T1",
        title="T1",
        status="in_progress",
        assigned_to="qa",
        active_dispatch_id=dispatch_id,
        contract=TaskContract(behavior="x", verification="true"),
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
        payload={"assignee": "qa", "role": "qa", "dispatch_id": dispatch_id},
    ))
    passed = ZfEvent(
        type="test.passed",
        actor="qa",
        task_id="T1",
        payload={
            "dispatch_id": dispatch_id,
            "summary": "qa ok",
            "changed_files": [],
            "tests_run": ["python3 -m pytest -q"],
            "evidence_refs": ["pytest"],
            "artifact_refs": ["tests"],
        },
    )
    log.append(passed)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert any(d.action == "move" and d.task_id == "T1" for d in decisions)
    assert store.get("T1").status == "done"
    done_events = [e for e in _events(log) if e.type == "task.done.evidence"]
    assert len(done_events) == 1
    assert done_events[0].payload["trigger_event_id"] == passed.id
    assert not [e for e in _events(log) if e.type == "task.invalid_transition"]


def test_reconcile_terminal_done_evidence_is_idempotent_for_same_event(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.roles = [
        role for role in orch.config.roles if role.name not in {"review", "test", "judge"}
    ] + [
        RoleConfig(
            name="qa",
            backend="mock",
            publishes=["test.passed", "test.failed"],
            triggers=["static_gate.passed"],
        )
    ]
    dispatch_id = "disp-qa"
    store.add(Task(
        id="T1",
        title="T1",
        status="in_progress",
        assigned_to="qa",
        active_dispatch_id=dispatch_id,
        contract=TaskContract(behavior="x", verification="true"),
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
        payload={"assignee": "qa", "role": "qa", "dispatch_id": dispatch_id},
    ))
    passed = ZfEvent(
        type="test.passed",
        actor="qa",
        task_id="T1",
        payload={
            "dispatch_id": dispatch_id,
            "summary": "qa ok",
            "changed_files": [],
            "tests_run": ["python3 -m pytest -q"],
            "evidence_refs": ["pytest"],
            "artifact_refs": ["tests"],
        },
    )
    log.append(passed)
    log.append(ZfEvent(
        type="task.done.evidence",
        actor="zf-cli",
        task_id="T1",
        payload={"trigger_event_id": passed.id},
    ))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert any(d.action == "move" and d.task_id == "T1" for d in decisions)
    assert store.get("T1").status == "done"
    assert len([e for e in _events(log) if e.type == "task.done.evidence"]) == 1


def test_reconcile_terminal_uses_latest_corrected_judge_payload(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.verification.contract.required = True
    dispatch_id = "disp-1"
    store.add(Task(
        id="T1",
        title="T1",
        status="in_progress",
        assigned_to="judge",
        active_dispatch_id=dispatch_id,
        contract=TaskContract(
            behavior="x",
            verification="true",
            verification_tiers=["runtime"],
        ),
    ))
    log.append(ZfEvent(type="review.approved", actor="review", task_id="T1"))
    log.append(ZfEvent(type="test.passed", actor="test-1", task_id="T1"))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
        payload={"assignee": "judge", "role": "judge", "dispatch_id": dispatch_id},
    ))
    log.append(ZfEvent(
        type="judge.passed",
        actor="judge",
        task_id="T1",
        payload=_judge_payload(dispatch_id, tier="static"),
    ))
    good = ZfEvent(
        type="judge.passed",
        actor="judge",
        task_id="T1",
        payload=_judge_payload(dispatch_id, tier="runtime"),
    )
    log.append(good)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert any(d.action == "move" and d.task_id == "T1" for d in decisions)
    assert store.get("T1").status == "done"  # archived lookup
    done_events = [e for e in _events(log) if e.type == "task.done.evidence"]
    assert done_events[-1].payload["trigger_event_id"] == good.id
    assert not [
        e for e in _events(log)
        if e.type == "task.done.blocked"
        and e.payload.get("trigger_event_id") == good.id
    ]


def test_reconcile_terminal_judge_passed_closes_last_feature_task(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    feature_store = FeatureStore(tmp_path / ".zf" / "feature_list.json")
    feature_store.add(Feature(
        id="F-ABC12345",
        title="Feature",
        status="active",
    ))
    store.add(Task(
        id="T1",
        title="T1",
        key="F-ABC12345-part-a",
        status="in_progress",
        assigned_to="judge",
    ))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "judge", "role": "judge"}))
    log.append(ZfEvent(type="judge.passed", actor="judge", task_id="T1"))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert store.get("T1").status == "done"  # archived lookup
    assert feature_store.get("F-ABC12345").status == "done"  # archived lookup
    assert any(
        e.type == "feature.status_changed"
        and e.task_id == "F-ABC12345"
        and e.payload.get("feature_id") == "F-ABC12345"
        and e.payload.get("trigger_task_id") == "T1"
        and e.payload.get("trigger_event") == "judge.passed"
        for e in _events(log)
    )


def test_reconcile_closes_feature_from_nested_contract_feature_id(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    feature_store = FeatureStore(tmp_path / ".zf" / "feature_list.json")
    feature_store.add(Feature(
        id="F-ABC12345",
        title="Feature",
        status="planning",
    ))
    store.add(Task(
        id="T1",
        title="T1",
        key="task-a",
        status="in_progress",
        assigned_to="judge",
    ))
    log.append(ZfEvent(
        type="task.contract.update",
        actor="zf-cli",
        task_id="T1",
        payload={"contract": {"feature_id": "F-ABC12345"}},
    ))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "judge", "role": "judge"}))
    log.append(ZfEvent(type="judge.passed", actor="judge", task_id="T1"))

    orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert store.get("T1").status == "done"  # archived lookup
    assert feature_store.get("F-ABC12345").status == "done"  # archived lookup


def test_reconcile_keeps_feature_active_when_sibling_task_remains(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    feature_store = FeatureStore(tmp_path / ".zf" / "feature_list.json")
    feature_store.add(Feature(
        id="F-ABC12345",
        title="Feature",
        status="active",
    ))
    store.add(Task(
        id="T1",
        title="T1",
        key="F-ABC12345:part-a",
        status="in_progress",
        assigned_to="judge",
    ))
    store.add(Task(
        id="T2",
        title="T2",
        key="F-ABC12345:part-b",
        status="backlog",
    ))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "judge", "role": "judge"}))
    log.append(ZfEvent(type="judge.passed", actor="judge", task_id="T1"))

    orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert store.get("T1").status == "done"  # archived lookup
    assert feature_store.get("F-ABC12345").status == "active"
    assert not any(e.type == "feature.status_changed" for e in _events(log))


def test_reconcile_projects_worker_state_for_completed_stage_actor(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch._last_worker_state["dev"] = "busy"
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="dev"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "dev", "role": "dev"}))
    log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1"))

    orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert any(
        e.type == "worker.state.changed"
        and e.actor == "dev"
        and e.payload.get("from") == "busy"
        and e.payload.get("to") == "awaiting_review"
        for e in _events(log)
    )


def test_replayed_progress_after_handoff_is_not_rejected(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="dev"))
    progress = ZfEvent(
        type="dev.build.done",
        actor="dev",
        task_id="T1",
        payload={"dispatch_id": "disp-dev"},
    )
    log.append(progress)
    log.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id="T1",
        payload={
            "role": "review",
            "assignee": "review",
            "source": "pending_handoff_reconcile",
            "trigger_event": "dev.build.done",
        },
        causation_id=progress.id,
    ))
    store.update("T1", assigned_to="review")

    decision = orch._reject_invalid_lifecycle_event(progress)  # type: ignore[attr-defined]

    assert decision is not None
    assert decision.action == "skip"
    assert not any(e.type == "runtime.action.rejected" for e in _events(log))


def test_reconcile_projects_terminal_worker_idle(tmp_path: Path) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch._last_worker_state["judge"] = "busy"
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="judge"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "judge", "role": "judge"}))
    log.append(ZfEvent(type="judge.passed", actor="judge", task_id="T1"))

    orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert any(
        e.type == "worker.state.changed"
        and e.actor == "judge"
        and e.payload.get("from") == "busy"
        and e.payload.get("to") == "idle"
        for e in _events(log)
    )


def test_dispatch_ready_ignores_archived_terminal_reassignments(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    transport = orch.transport
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="judge"))
    store.update("T1", status="done")
    log.append(ZfEvent(
        type="task.assigned",
        actor="zf-cli",
        task_id="T1",
        payload={"assignee": "judge", "role": "judge"},
    ))

    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]

    assert decisions == []
    assert transport.sends == []
    assert store.get("T1").status == "done"  # type: ignore[union-attr]


def test_reconcile_blocks_worktree_dev_handoff_without_task_ref(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.runtime = RuntimeConfig(
        workdirs=WorkdirConfig(enabled=True, mode="worktree"),
    )
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="dev-1"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "dev-1", "role": "dev",
                                "dispatch_id": "disp-1"}))
    progress = ZfEvent(type="dev.build.done", actor="dev-1",
                       task_id="T1", payload={"dispatch_id": "disp-1"})
    log.append(progress)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "block"
    assert store.get("T1").assigned_to == "dev-1"  # type: ignore[union-attr]
    assert not any(
        e.type == "task.assigned"
        and e.task_id == "T1"
        and e.payload.get("assignee") == "review"
        for e in _events(log)
    )
    assert any(
        e.type == "task.ref.rejected"
        and e.task_id == "T1"
        and e.payload.get("trigger_event_id") == progress.id
        and e.payload.get("source") == "pending_handoff_reconcile"
        for e in _events(log)
    )


def test_reconcile_routes_worktree_dev_handoff_with_task_ref(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.runtime = RuntimeConfig(
        workdirs=WorkdirConfig(enabled=True, mode="worktree"),
    )
    refs_dir = tmp_path / ".zf" / "refs"
    refs_dir.mkdir()
    (refs_dir / "task-index.json").write_text(
        '{"T1": {"task_ref": "task/T1", "source_commit": "abc"}}\n',
        encoding="utf-8",
    )
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="dev-1"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "dev-1", "role": "dev",
                                "dispatch_id": "disp-1"}))
    progress = ZfEvent(type="dev.build.done", actor="dev-1",
                       task_id="T1", payload={"dispatch_id": "disp-1"})
    log.append(progress)
    (refs_dir / "task-index.json").write_text(
        (
            '{"T1": {"task_ref": "task/T1", "source_commit": "abc", '
            '"trigger_event_id": "' + progress.id + '"}}\n'
        ),
        encoding="utf-8",
    )

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "assign", decisions
    assert decisions[0].role == "review"
    assert store.get("T1").assigned_to == "review"  # type: ignore[union-attr]


def test_reconcile_blocks_worktree_dev_handoff_with_stale_task_ref(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.runtime = RuntimeConfig(
        workdirs=WorkdirConfig(enabled=True, mode="worktree"),
    )
    refs_dir = tmp_path / ".zf" / "refs"
    refs_dir.mkdir()
    (refs_dir / "task-index.json").write_text(
        (
            '{"T1": {"task_ref": "task/T1", "source_commit": "abc", '
            '"trigger_event_id": "old-dev-build"}}\n'
        ),
        encoding="utf-8",
    )
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="dev-1"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "dev-1", "role": "dev",
                                "dispatch_id": "disp-2"}))
    progress = ZfEvent(type="dev.build.done", actor="dev-1",
                       task_id="T1", payload={"dispatch_id": "disp-2"})
    log.append(progress)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "block"
    assert store.get("T1").assigned_to == "dev-1"  # type: ignore[union-attr]
    assert not any(
        e.type == "task.assigned"
        and e.task_id == "T1"
        and e.payload.get("assignee") == "review"
        for e in _events(log)
    )
    assert any(
        e.type == "task.ref.rejected"
        and e.task_id == "T1"
        and e.payload.get("trigger_event_id") == progress.id
        for e in _events(log)
    )


def test_reconcile_replays_processed_dev_handoff_after_task_ref_update(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.runtime = RuntimeConfig(
        workdirs=WorkdirConfig(enabled=True, mode="worktree"),
    )
    refs_dir = tmp_path / ".zf" / "refs"
    refs_dir.mkdir()
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="dev-1"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "dev-1", "role": "dev",
                                "dispatch_id": "disp-1"}))
    progress = ZfEvent(type="dev.build.done", actor="dev-1",
                       task_id="T1", payload={"dispatch_id": "disp-1"})
    log.append(progress)
    orch._processed_event_ids.add(progress.id)  # type: ignore[attr-defined]
    log.append(ZfEvent(
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="T1",
        payload={"trigger_event_id": progress.id},
    ))
    (refs_dir / "task-index.json").write_text(
        (
            '{"T1": {"task_ref": "task/T1", "source_commit": "abc", '
            '"trigger_event_id": "' + progress.id + '"}}\n'
        ),
        encoding="utf-8",
    )
    log.append(ZfEvent(
        type="task.ref.updated",
        actor="zf-cli",
        task_id="T1",
        payload={"trigger_event_id": progress.id},
    ))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "assign", decisions
    assert decisions[0].role == "review"
    assert store.get("T1").assigned_to == "review"  # type: ignore[union-attr]


def test_reconcile_routes_arch_handoff_when_workdirs_disabled(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.runtime = RuntimeConfig(
        workdirs=WorkdirConfig(enabled=False, mode="dry-run"),
    )
    orch.config.roles.append(
        RoleConfig(
            name="arch",
            backend="mock",
            publishes=["arch.proposal.done"],
            triggers=["task.assigned"],
            role_kind="reader",
        )
    )
    orch.config.roles.append(
        RoleConfig(
            name="critic",
            backend="mock",
            publishes=["design.critique.done"],
            triggers=["arch.proposal.done"],
            role_kind="reader",
        )
    )
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="arch"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "arch", "role": "arch",
                                "dispatch_id": "disp-arch"}))
    progress = ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="T1",
        payload={
            "dispatch_id": "disp-arch",
            "artifact_refs": ["docs/plans/plan.md"],
            "file_plan": ["docs/plans/plan.md"],
        },
    )
    log.append(progress)

    orch._apply_housekeeping(progress)  # type: ignore[attr-defined]
    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "assign", decisions
    assert decisions[0].role == "critic"
    assert store.get("T1").assigned_to == "critic"  # type: ignore[union-attr]
    assert not [
        e for e in _events(log)
        if e.type == "task.ref.rejected"
        and e.payload.get("trigger_event_id") == progress.id
    ]


def test_reconcile_snapshots_arch_artifacts_before_critic_handoff(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.runtime = RuntimeConfig(
        workdirs=WorkdirConfig(enabled=True, mode="worktree"),
    )
    orch.config.roles.append(
        RoleConfig(
            name="arch",
            backend="mock",
            publishes=["arch.proposal.done"],
            triggers=["task.assigned"],
            role_kind="reader",
        )
    )
    orch.config.roles.append(
        RoleConfig(
            name="critic",
            backend="mock",
            publishes=["design.critique.done"],
            triggers=["arch.proposal.done"],
            role_kind="reader",
        )
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "base")
    arch_workdir = tmp_path / ".zf" / "workdirs" / "arch" / "project"
    arch_workdir.parent.mkdir(parents=True)
    _git(tmp_path, "worktree", "add", "--detach", str(arch_workdir), "HEAD")
    artifact = arch_workdir / "docs" / "plans" / "plan.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("arch plan\n", encoding="utf-8")
    store.add(Task(id="T1", title="T1", status="in_progress",
                   assigned_to="arch"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "arch", "role": "arch",
                                "dispatch_id": "disp-arch"}))
    progress = ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="T1",
        payload={
            "dispatch_id": "disp-arch",
            "artifact_refs": ["docs/plans/plan.md"],
            "file_plan": ["docs/plans/plan.md"],
        },
    )
    log.append(progress)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "assign"
    assert decisions[0].role == "critic"
    assert store.get("T1").assigned_to == "critic"  # type: ignore[union-attr]
    ref_event = next(
        e for e in _events(log)
        if e.type == "task.ref.updated"
        and e.payload.get("trigger_event_id") == progress.id
    )
    assert ref_event.payload["source"] == "pending_handoff_reconcile"
    commit = _git(tmp_path, "rev-parse", "refs/heads/task/T1")
    assert _git(tmp_path, "show", f"{commit}:docs/plans/plan.md") == "arch plan"


def test_reconcile_ignores_stale_design_handoff_for_lane_task(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.workflow = WorkflowConfig(affinity_lanes={
        "refactor-slot": WorkflowAffinityLaneProfileConfig(
            affinity_key="affinity_tag",
            lanes=[
                WorkflowAffinityLaneConfig(
                    id="lane0",
                    impl="dev-lane-0",
                    verify="verify-lane-0",
                ),
            ],
        ),
    })
    orch.config.roles.extend([
        RoleConfig(
            name="arch",
            backend="mock",
            publishes=["arch.proposal.done"],
            triggers=["task.assigned"],
            role_kind="reader",
        ),
        RoleConfig(
            name="critic",
            backend="mock",
            publishes=["design.critique.done"],
            triggers=["arch.proposal.done"],
            role_kind="reader",
        ),
        RoleConfig(
            name="dev-lane-0",
            backend="mock",
            publishes=["dev.build.done"],
            triggers=["task.assigned"],
            role_kind="writer",
        ),
    ])
    store.add(Task(
        id="CANGJIE-CORE-001",
        title="core",
        status="in_progress",
        assigned_to="arch",
        contract=TaskContract(
            owner_role="dev-core",
            evidence_contract={
                "source": "refactor_task_map",
                "affinity_tag": "core-foundation",
            },
        ),
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="CANGJIE-CORE-001",
        payload={
            "assignee": "arch",
            "role": "arch",
            "dispatch_id": "disp-arch",
        },
    ))
    progress = ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="CANGJIE-CORE-001",
        payload={
            "dispatch_id": "disp-arch",
            "artifact_refs": ["docs/plans/plan.md"],
            "file_plan": ["docs/plans/plan.md"],
        },
    )
    log.append(progress)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "wait"
    assert "ignored for lane task" in decisions[0].reason
    assert store.get("CANGJIE-CORE-001").assigned_to == "arch"  # type: ignore[union-attr]
    assert not [
        event for event in _events(log)
        if event.type == "task.assigned"
        and event.payload.get("source") == "pending_handoff_reconcile"
        and event.payload.get("assignee") == "critic"
    ]


def test_dispatch_allows_design_critic_before_contract_synthesis(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.roles.append(
        RoleConfig(
            name="arch",
            backend="mock",
            publishes=["arch.proposal.done"],
            triggers=["task.assigned"],
            role_kind="reader",
        )
    )
    orch.config.roles.append(
        RoleConfig(
            name="critic",
            backend="mock",
            publishes=["design.critique.done"],
            triggers=["arch.proposal.done"],
            role_kind="reader",
        )
    )
    store.add(Task(id="T1", title="Design plan", status="in_progress",
                   assigned_to="critic"))
    log.append(ZfEvent(type="task.dispatched", actor="orchestrator",
                       task_id="T1",
                       payload={"assignee": "arch", "role": "arch",
                                "dispatch_id": "disp-arch"}))
    log.append(ZfEvent(
        type="arch.proposal.done",
        actor="arch",
        task_id="T1",
        payload={
            "dispatch_id": "disp-arch",
            "summary": "设计提案",
            "file_plan": ["docs/plans/plan.md"],
            "test_plan": {"cases": ["frontmatter_schema_valid"]},
        },
    ))
    log.append(ZfEvent(type="task.assigned", actor="zf-cli",
                       task_id="T1",
                       payload={"assignee": "critic", "role": "critic",
                                "source": "pending_handoff_reconcile"}))

    decisions = orch._dispatch_ready()  # type: ignore[attr-defined]

    assert any(d.action == "dispatch" and d.role == "critic" for d in decisions)
    assert "critic" in orch.transport.sends  # type: ignore[attr-defined]
    events = _events(log)
    assert any(
        e.type == "task.dispatched"
        and e.task_id == "T1"
        and e.payload.get("role") == "critic"
        for e in events
    )
    assert not [
        e for e in events
        if e.type == "task.contract.invalid" and e.task_id == "T1"
    ]


def test_reconcile_does_not_treat_design_critique_as_terminal_done(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.roles.append(
        RoleConfig(
            name="arch",
            backend="mock",
            publishes=["arch.proposal.done"],
            triggers=["task.assigned"],
            role_kind="reader",
        )
    )
    orch.config.roles.append(
        RoleConfig(
            name="critic",
            backend="mock",
            publishes=["design.critique.done"],
            triggers=["arch.proposal.done"],
            role_kind="reader",
        )
    )
    orch.config.roles.append(
        RoleConfig(
            name="dev",
            backend="mock",
            publishes=["dev.build.done"],
            triggers=["task.assigned"],
            role_kind="writer",
        )
    )
    store.add(Task(
        id="T1",
        title="design passed",
        status="in_progress",
        assigned_to="critic",
        active_dispatch_id="disp-critic",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
        payload={
            "assignee": "critic",
            "role": "critic",
            "dispatch_id": "disp-critic",
        },
    ))
    log.append(ZfEvent(
        type="design.critique.done",
        actor="critic",
        task_id="T1",
        payload={
            "dispatch_id": "disp-critic",
            "verdict": "PASS_WITH_RISK",
        },
    ))

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert decisions == []
    assert store.get("T1").assigned_to == "critic"  # type: ignore[union-attr]
    assert not any(e.type == "task.done.blocked" for e in _events(log))


def test_reconcile_routes_missed_gate_failed_to_arch_rework(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.workflow = WorkflowConfig(rework_routing={"gate.failed": "arch"})
    orch.config.roles.append(
        RoleConfig(
            name="arch",
            backend="mock",
            publishes=["arch.proposal.done"],
            triggers=["task.assigned"],
            role_kind="reader",
        )
    )
    orch.config.roles.append(
        RoleConfig(
            name="critic",
            backend="mock",
            publishes=["gate.failed"],
            triggers=["arch.proposal.done"],
            role_kind="reader",
        )
    )
    orch._last_worker_state["critic"] = "busy"  # type: ignore[attr-defined]
    store.add(Task(
        id="T1",
        title="Cangjie design",
        status="in_progress",
        assigned_to="critic",
        active_dispatch_id="disp-critic",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T1",
        payload={
            "assignee": "critic",
            "role": "critic",
            "dispatch_id": "disp-critic",
        },
    ))
    failure = ZfEvent(
        type="gate.failed",
        actor="critic",
        task_id="T1",
        payload={
            "dispatch_id": "disp-critic",
            "summary": "Cangjie backlog contract cannot be dispatched",
            "must_fix": [
                "expand each backlog task with spec_ref and tdd_ref",
                "confirm package namespace before scaffold",
            ],
        },
    )
    log.append(failure)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "dispatch"
    assert decisions[0].role == "arch"
    task = store.get("T1")
    assert task is not None
    assert task.assigned_to == "arch"
    assert task.retry_count == 1
    assert task.active_dispatch_id != "disp-critic"
    events = _events(log)
    rework = next(e for e in events if e.type == "task.rework.requested")
    assert rework.payload["trigger_event_id"] == failure.id
    assert rework.payload["required_actions"] == [
        "expand each backlog task with spec_ref and tdd_ref",
        "confirm package namespace before scaffold",
    ]
    assert any(
        e.type == "worker.state.changed"
        and e.actor == "critic"
        and e.payload.get("to") == "idle"
        for e in events
    )
    assert any(
        e.type == "task.dispatched"
        and e.payload.get("assignee") == "arch"
        and e.payload.get("source") == "rework"
        for e in events
    )
    briefing = (
        tmp_path / ".zf" / "briefings" / "arch-T1-rework.md"
    ).read_text(encoding="utf-8")
    assert "## Required Rework Items" in briefing
    assert "expand each backlog task with spec_ref and tdd_ref" in briefing
    assert "confirm package namespace before scaffold" in briefing


def test_reconcile_routes_review_child_failed_to_owner_affinity_lane(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.workflow = WorkflowConfig(
        rework_routing={"review.child.failed": "dev"},
    )
    orch.config.roles.append(RoleConfig(
        name="dev-lane-1",
        backend="mock",
        role_kind="writer",
        publishes=["dev.build.done", "dev.failed"],
        triggers=["task.assigned"],
    ))
    orch.config.roles.append(RoleConfig(
        name="review-lane-1",
        backend="mock",
        role_kind="reader",
        publishes=["review.child.completed", "review.child.failed"],
        triggers=["candidate.ready"],
    ))
    orch._last_worker_state["review-lane-1"] = "busy"  # type: ignore[attr-defined]
    store.add(Task(
        id="T-LANE",
        title="Gateway slice",
        status="in_progress",
        assigned_to="review-lane-1",
        active_dispatch_id="disp-review",
        contract=TaskContract(
            behavior="gateway behavior",
            owner_role="dev-lane-1",
        ),
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="T-LANE",
        payload={
            "role": "review-lane-1",
            "assignee": "review-lane-1",
            "dispatch_id": "disp-review",
        },
    ))
    failure = ZfEvent(
        type="review.child.failed",
        actor="review-lane-1",
        task_id="T-LANE",
        payload={
            "dispatch_id": "disp-review",
            "status": "failed",
            "findings": [{
                "severity": "high",
                "summary": "provider parity missing",
                "required_action": "implement real provider parity checks",
            }],
        },
    )
    log.append(failure)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "dispatch"
    assert decisions[0].role == "dev-lane-1"
    task = store.get("T-LANE")
    assert task is not None
    assert task.assigned_to == "dev-lane-1"
    assert task.retry_count == 1
    events = _events(log)
    rework = next(e for e in events if e.type == "task.rework.requested")
    assert rework.payload["trigger_event_id"] == failure.id
    assert rework.payload["assignee"] == "dev-lane-1"
    assert any(
        e.type == "worker.state.changed"
        and e.actor == "review-lane-1"
        and e.payload.get("to") == "idle"
        for e in events
    )
    assert any(
        e.type == "task.dispatched"
        and e.task_id == "T-LANE"
        and e.payload.get("assignee") == "dev-lane-1"
        and e.payload.get("source") == "rework"
        for e in events
    )


def test_dispatch_skipped_is_coalesced_for_same_reason(
    tmp_path: Path,
) -> None:
    orch, _, log = _make_orchestrator(tmp_path)
    task = Task(id="T1", title="T1", status="backlog", assigned_to="dev")
    role = next(r for r in orch.config.roles if r.instance_id == "dev")

    orch._emit_dispatch_skipped(  # type: ignore[attr-defined]
        task=task,
        role=role,
        reason="wave_blocked:waiting_for=T0,waiting_wave=1,wave=2",
    )
    orch._emit_dispatch_skipped(  # type: ignore[attr-defined]
        task=task,
        role=role,
        reason="wave_blocked:waiting_for=T0,waiting_wave=1,wave=2",
    )

    assert sum(
        event.type == "orchestrator.dispatch_skipped"
        for event in _events(log)
    ) == 1


def test_rework_role_prefers_contract_owner_instance_for_replica_pool(
    tmp_path: Path,
) -> None:
    orch, _, _ = _make_orchestrator(tmp_path)
    orch.config.roles = [
        role for role in orch.config.roles
        if role.name != "dev"
    ] + [
        RoleConfig(name="dev", backend="mock", instance_id="dev-1"),
        RoleConfig(name="dev", backend="mock", instance_id="dev-2"),
    ]
    task = Task(
        id="T1",
        title="T1",
        contract=TaskContract(
            owner_instance="dev-2",
            rework_to="dev",
        ),
    )

    role = orch._resolve_rework_role(  # type: ignore[attr-defined]
        task,
        ZfEvent(type="review.rejected", task_id="T1"),
    )

    assert role is not None
    assert role.instance_id == "dev-2"


def test_rework_dispatch_skips_when_target_worker_has_other_inflight_task(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    store.add(Task(
        id="TASK-BUSY",
        title="busy task",
        status="in_progress",
        assigned_to="dev",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-BUSY",
        payload={"role": "dev", "assignee": "dev"},
    ))
    store.add(Task(
        id="TASK-REWORK",
        title="needs rework",
        status="in_progress",
        assigned_to="review",
        retry_count=1,
    ))
    trigger = ZfEvent(
        type="review.rejected",
        actor="review",
        task_id="TASK-REWORK",
        payload={"reason": "needs fix"},
    )

    result = orch._dispatch_rework(  # type: ignore[attr-defined]
        store.get("TASK-REWORK"),
        trigger,
    )

    assert result is None
    events = _events(log)
    assert not any(
        event.type == "task.rework.requested"
        and event.task_id == "TASK-REWORK"
        for event in events
    )
    skipped = [
        event for event in events
        if event.type == "orchestrator.dispatch_skipped"
        and event.task_id == "TASK-REWORK"
    ]
    assert skipped
    assert skipped[0].payload["reason"] == "rework_target_busy:TASK-BUSY"


def test_task_ref_repair_waits_when_owner_lane_is_busy(
    tmp_path: Path,
) -> None:
    """A busy lane is a pending repair, not a terminal block.

    R37 exposed this with lane reuse: gateway completed, its task-ref handoff
    was rejected later, and the original dev lane had already picked up
    state-config. The repair must remain pending until that lane is free.
    """
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.roles = [
        role for role in orch.config.roles
        if role.name not in {"dev", "review", "test", "judge"}
    ] + [
        RoleConfig(
            name="dev-lane-2",
            backend="mock",
            role_kind="writer",
            publishes=["dev.build.done", "dev.failed"],
            triggers=["task.assigned"],
        )
    ]
    store.add(Task(
        id="TASK-BUSY",
        title="busy task",
        status="in_progress",
        assigned_to="dev-lane-2",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-BUSY",
        payload={"role": "dev-lane-2", "assignee": "dev-lane-2"},
    ))
    store.add(Task(
        id="TASK-REPAIR",
        title="repair task ref",
        status="in_progress",
        assigned_to="dev-lane-2",
        retry_count=1,
        active_dispatch_id="disp-dev",
        contract=TaskContract(
            behavior="repair writer task ref",
            owner_role="dev-lane-2",
            owner_instance="dev-lane-2",
            rework_to="dev-lane-2",
        ),
    ))
    dev_done = ZfEvent(
        id="evt-dev-done",
        type="dev.build.done",
        actor="dev-lane-2",
        task_id="TASK-REPAIR",
        payload={"dispatch_id": "disp-dev"},
    )
    rejected = ZfEvent(
        id="evt-ref-rejected",
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="TASK-REPAIR",
        payload={
            "trigger_event_id": dev_done.id,
            "reason": "source_commit changes outside task contract scope",
        },
        causation_id=dev_done.id,
    )
    repair = ZfEvent(
        id="evt-ref-repair",
        type="task.ref.repair.requested",
        actor="zf-cli",
        task_id="TASK-REPAIR",
        payload={
            "source_event_id": dev_done.id,
            "blocking_event_id": rejected.id,
            "reason": "repair task ref handoff",
            "target_assignee": "dev-lane-2",
        },
        causation_id=rejected.id,
    )
    log.append(dev_done)
    log.append(rejected)
    log.append(repair)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "wait"
    assert "rework_target_busy:TASK-BUSY" in decisions[0].reason
    events = _events(log)
    assert not [
        event for event in events
        if event.type == "task.rework.requested"
        and event.task_id == "TASK-REPAIR"
    ]
    assert not [
        event for event in events
        if event.type == "task.dispatched"
        and event.task_id == "TASK-REPAIR"
    ]
    assert [
        event for event in events
        if event.type == "orchestrator.dispatch_skipped"
        and event.task_id == "TASK-REPAIR"
        and event.payload.get("reason") == "rework_target_busy:TASK-BUSY"
    ]


def test_task_ref_repair_ignores_terminal_prior_dispatch_on_same_lane(
    tmp_path: Path,
) -> None:
    orch, store, log = _make_orchestrator(tmp_path)
    orch.config.roles = [
        role for role in orch.config.roles
        if role.name not in {"dev", "review", "test", "judge"}
    ] + [
        RoleConfig(
            name="dev-lane-2",
            backend="mock",
            role_kind="writer",
            publishes=["dev.build.done", "dev.failed"],
            triggers=["task.assigned"],
        )
    ]
    store.add(Task(
        id="TASK-OLD",
        title="old task still projected in progress",
        status="in_progress",
        assigned_to="dev-lane-2",
    ))
    log.append(ZfEvent(
        type="task.dispatched",
        actor="orchestrator",
        task_id="TASK-OLD",
        payload={
            "role": "dev-lane-2",
            "assignee": "dev-lane-2",
            "dispatch_id": "disp-old",
        },
    ))
    log.append(ZfEvent(
        type="dev.build.done",
        actor="dev-lane-2",
        task_id="TASK-OLD",
        payload={"dispatch_id": "disp-old"},
    ))
    store.add(Task(
        id="TASK-REPAIR",
        title="repair task ref",
        status="in_progress",
        assigned_to="dev-lane-2",
        retry_count=1,
        active_dispatch_id="disp-dev",
        contract=TaskContract(
            behavior="repair writer task ref",
            owner_role="dev-lane-2",
            owner_instance="dev-lane-2",
            rework_to="dev-lane-2",
        ),
    ))
    dev_done = ZfEvent(
        id="evt-dev-done",
        type="dev.build.done",
        actor="dev-lane-2",
        task_id="TASK-REPAIR",
        payload={"dispatch_id": "disp-dev"},
    )
    rejected = ZfEvent(
        id="evt-ref-rejected",
        type="task.ref.rejected",
        actor="zf-cli",
        task_id="TASK-REPAIR",
        payload={
            "trigger_event_id": dev_done.id,
            "reason": "source_commit changes outside task contract scope",
        },
        causation_id=dev_done.id,
    )
    repair = ZfEvent(
        id="evt-ref-repair",
        type="task.ref.repair.requested",
        actor="zf-cli",
        task_id="TASK-REPAIR",
        payload={
            "source_event_id": dev_done.id,
            "blocking_event_id": rejected.id,
            "reason": "repair task ref handoff",
            "target_assignee": "dev-lane-2",
        },
        causation_id=rejected.id,
    )
    log.append(dev_done)
    log.append(rejected)
    log.append(repair)

    decisions = orch._reconcile_pending_handoffs()  # type: ignore[attr-defined]

    assert len(decisions) == 1
    assert decisions[0].action == "dispatch"
    assert decisions[0].role == "dev-lane-2"
    events = _events(log)
    assert [
        event for event in events
        if event.type == "task.rework.requested"
        and event.task_id == "TASK-REPAIR"
    ]
    assert [
        event for event in events
        if event.type == "task.dispatched"
        and event.task_id == "TASK-REPAIR"
        and event.payload.get("source") == "rework"
    ]
