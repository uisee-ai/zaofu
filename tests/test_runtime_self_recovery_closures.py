from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.autoresearch_invocation import (
    build_invocation_request_from_run_manager_event,
)
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def _state_dir(tmp_path: Path) -> Path:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    (state_dir / "logs").mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    EventLog(state_dir / "events.jsonl").append(ZfEvent(
        type="session.started",
        actor="zf-cli",
    ))
    return state_dir


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[
            RoleConfig(
                name="dev",
                backend="mock",
                stages=["implement"],
                publishes=["dev.build.done"],
            ),
        ],
    )


def _config_layer2() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        session=SessionConfig(tmux_session="test-zf"),
        roles=[
            RoleConfig(
                name="orchestrator",
                backend="mock",
                stages=["meta"],
                triggers=["autoresearch.invocation.requested"],
            publishes=[
                    "autoresearch.invocation.accepted",
                    "autoresearch.trigger.accepted",
                    "autoresearch.loop.requested",
                    "autoresearch.bug_candidate.created",
                    "automation.proposal.created",
                ],
            ),
        ],
    )


def _orchestrator(state_dir: Path) -> Orchestrator:
    return Orchestrator(
        state_dir,
        _config(),
        TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True)),
    )


def _orchestrator_layer2(state_dir: Path) -> Orchestrator:
    return Orchestrator(
        state_dir,
        _config_layer2(),
        TmuxTransport(TmuxSession(session_name="test-zf", dry_run=True)),
    )


def test_run_manager_autoresearch_ignores_inflight_fanout_pending() -> None:
    event = ZfEvent(
        type="run.manager.autoresearch.requested",
        actor="run-manager",
        payload={
            "request_id": "rm-pending",
            "fingerprint": "failure:fanout_child_pending:fanout-1:child-1",
            "summary": "Fanout child dispatched without a terminal child event",
            "severity": "high",
        },
    )

    invocation = build_invocation_request_from_run_manager_event(
        event,
        events=[event],
    )

    assert invocation is None


def test_run_manager_autoresearch_keeps_timed_out_fanout_request() -> None:
    event = ZfEvent(
        type="run.manager.autoresearch.requested",
        actor="run-manager",
        payload={
            "request_id": "rm-timeout",
            "fingerprint": "failure:fanout_child_pending:fanout-1:child-1",
            "summary": "Fanout child timed out without a terminal child event",
            "severity": "high",
        },
    )

    invocation = build_invocation_request_from_run_manager_event(
        event,
        events=[event],
    )

    assert invocation is not None
    assert invocation.type == "autoresearch.invocation.requested"


def test_completion_schedule_requeues_task_once(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-CONT",
        title="needs continuation",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-1",
    ))
    event = ZfEvent(
        type="task.continuation_scheduled",
        actor="zf-cli",
        task_id="TASK-CONT",
        payload={
            "dispatch_id": "disp-1",
            "route": "continuation",
            "reason": "provider completed before evidence",
            "next_required_event": "dev.build.done",
        },
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(event)
    orch = _orchestrator(state_dir)

    first = orch._on_completion_scheduled(event)
    second = orch._on_completion_scheduled(event)

    task = store.get("TASK-CONT")
    requeues = [item for item in log.read_all() if item.type == "task.requeued"]
    assert first is not None and first.action == "move"
    assert second is not None and second.action == "skip"
    assert task is not None
    assert task.status == "backlog"
    assert task.assigned_to == "dev"
    assert task.active_dispatch_id == ""
    assert len(requeues) == 1
    assert requeues[0].payload["schedule_event_id"] == event.id
    assert requeues[0].payload["next_required_event"] == "dev.build.done"


def test_completion_schedule_ignores_stale_dispatch_id(tmp_path: Path) -> None:
    state_dir = _state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(
        id="TASK-RETRY",
        title="retry from old dispatch",
        status="in_progress",
        assigned_to="dev",
        active_dispatch_id="disp-current",
    ))
    event = ZfEvent(
        type="task.retry_scheduled",
        actor="zf-cli",
        task_id="TASK-RETRY",
        payload={"dispatch_id": "disp-old", "route": "retry"},
    )
    log = EventLog(state_dir / "events.jsonl")
    log.append(event)

    decision = _orchestrator(state_dir)._on_completion_scheduled(event)

    task = store.get("TASK-RETRY")
    stale_events = [
        item for item in log.read_all()
        if item.type == "task.retry.stale_ignored"
    ]
    assert decision is not None and decision.action == "skip"
    assert task is not None
    assert task.status == "in_progress"
    assert task.active_dispatch_id == "disp-current"
    assert len(stale_events) == 1
    assert stale_events[0].payload["schedule_event_id"] == event.id


def test_autoresearch_trigger_accepted_creates_single_maintenance_proposal(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-BUG", title="runtime diagnosis", status="in_progress"))
    log = EventLog(state_dir / "events.jsonl")
    event = ZfEvent(
        type="autoresearch.trigger.accepted",
        actor="zf-autoresearch",
        task_id="TASK-BUG",
        payload={
            "trigger_id": "ar-001",
            "severity": "critical",
            "reason": "zaofu bug reproduced",
            "fingerprint": "bug:dispatch-loop",
            "evidence_paths": ["records/ar-001.md"],
        },
    )
    log.append(event)
    orch = _orchestrator(state_dir)

    first = orch._on_autoresearch_trigger_accepted(event)
    second = orch._on_autoresearch_trigger_accepted(event)

    proposals = [
        item for item in log.read_all()
        if item.type == "automation.proposal.created"
    ]
    loop_requests = [
        item for item in log.read_all()
        if item.type == "autoresearch.loop.requested"
    ]
    candidates = [
        item for item in log.read_all()
        if item.type == "autoresearch.bug_candidate.created"
    ]
    assert first is not None and first.action == "notify"
    assert second is not None and second.action == "skip"
    assert len(proposals) == 1
    assert len(loop_requests) == 1
    assert len(candidates) == 1
    payload = proposals[0].payload
    assert payload["automation_id"] == "autoresearch-self-repair"
    assert payload["action"] == "maintenance-prepare"
    assert payload["action_proposal"]["payload"]["trigger_id"] == "ar-001"
    assert payload["repair_task_proposal"]["action"] == "create-task"
    assert payload["repair_task_proposal"]["payload"]["contract"]["phase"] == "zaofu_self_repair"
    assert Path(payload["candidate_path"]).exists()
    assert loop_requests[0].payload["apply_policy"] == "proposal_only"
    assert loop_requests[0].payload["scenarios"] == ["controlled-stuck-recovery"]


def test_autoresearch_invocation_request_accepts_l1_and_bridges_to_trigger(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    event = ZfEvent(
        type="autoresearch.invocation.requested",
        actor="zf-supervisor",
        task_id="TASK-BUG",
        payload={
            "invocation_id": "arinv-001",
            "level": "diagnose",
            "apply_policy": "proposal_only",
            "severity": "critical",
            "trigger_reason": "pane mismatch and bug signature",
            "fingerprint": "bug:dispatch-loop",
            "evidence_paths": ["records/ar-001.md"],
        },
    )
    log.append(event)
    orch = _orchestrator(state_dir)

    first = orch._on_autoresearch_invocation_requested(event)
    second = orch._on_autoresearch_invocation_requested(event)

    events = log.read_all()
    accepted = [item for item in events if item.type == "autoresearch.invocation.accepted"]
    rejected = [item for item in events if item.type == "autoresearch.invocation.rejected"]
    triggers = [item for item in events if item.type == "autoresearch.trigger.accepted"]
    loop_requests = [item for item in events if item.type == "autoresearch.loop.requested"]
    assert first is not None and first.action == "notify"
    assert second is not None and second.action == "skip"
    assert len(accepted) == 1
    assert rejected == []
    assert len(triggers) == 1
    assert len(loop_requests) == 0
    assert triggers[0].payload["invocation_id"] == "arinv-001"
    assert triggers[0].payload["apply_policy"] == "proposal_only"


def test_autoresearch_invocation_is_kernel_owned_with_layer2_active(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    event = ZfEvent(
        type="autoresearch.invocation.requested",
        actor="zf-supervisor",
        task_id="TASK-BUG",
        payload={
            "invocation_id": "arinv-layer2",
            "level": "diagnose",
            "apply_policy": "proposal_only",
            "severity": "critical",
            "trigger_reason": "runtime attention escalation",
            "fingerprint": "bug:layer2-kernel-owned",
        },
    )
    log.append(event)

    decisions = _orchestrator_layer2(state_dir).run_once(events=[event])

    events = log.read_all()
    accepted = [
        item for item in events
        if item.type == "autoresearch.invocation.accepted"
    ]
    triggers = [
        item for item in events
        if item.type == "autoresearch.trigger.accepted"
    ]
    dispatch_failures = [
        item for item in events
        if item.type == "orchestrator.dispatch_failed"
    ]
    assert [decision.action for decision in decisions] == ["notify"]
    assert len(accepted) == 1
    assert len(triggers) == 1
    assert triggers[0].payload["invocation_id"] == "arinv-layer2"
    assert dispatch_failures == []


def test_run_manager_autoresearch_request_bridges_to_loop_request(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="TASK-BUG", title="runtime diagnosis", status="in_progress"))
    log = EventLog(state_dir / "events.jsonl")
    event = ZfEvent(
        type="run.manager.autoresearch.requested",
        actor="run-manager",
        task_id="TASK-BUG",
        correlation_id="rmar-bridge",
        payload={
            "request_id": "rmar-bridge",
            "fingerprint": "runtime:dispatch.silent_stall:TASK-BUG",
            "failure_class": "dispatch_silent_stall",
            "owner_route": "run_manager",
            "action_policy": "needs_diagnosis",
            "apply_policy": "proposal_only",
            "context_ref": "projections/run_manager.json#run_context_bundle",
            "expected_output": [
                "diagnosis_report",
                "reproduction_steps",
                "patch_or_resume_proposal",
            ],
        },
    )
    log.append(event)
    orch = _orchestrator_layer2(state_dir)

    bridge_decisions = orch.run_once(events=[event])

    events = log.read_all()
    invocations = [
        item for item in events
        if item.type == "autoresearch.invocation.requested"
    ]
    assert [decision.action for decision in bridge_decisions] == ["notify"]
    assert len(invocations) == 1
    assert invocations[0].payload["source"] == "run_manager"
    assert invocations[0].payload["request_id"] == "rmar-bridge"
    assert invocations[0].payload["loop_request_id"] == "rmar-bridge"

    orch.run_once(events=[invocations[0]])
    events = log.read_all()
    triggers = [
        item for item in events
        if item.type == "autoresearch.trigger.accepted"
    ]
    assert len(triggers) == 1
    assert triggers[0].payload["run_manager_request_id"] == "rmar-bridge"

    orch.run_once(events=[triggers[0]])
    events = log.read_all()
    loop_requests = [
        item for item in events
        if item.type == "autoresearch.loop.requested"
    ]
    dispatch_failures = [
        item for item in events
        if item.type == "orchestrator.dispatch_failed"
    ]
    assert len(loop_requests) == 1
    assert loop_requests[0].payload["loop_request_id"] == "rmar-bridge"
    assert loop_requests[0].payload["expected_output"] == [
        "diagnosis_report",
        "reproduction_steps",
        "patch_or_resume_proposal",
    ]
    assert dispatch_failures == []


def test_taskless_autoresearch_trigger_is_not_rejected_as_lifecycle_event(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    event = ZfEvent(
        type="autoresearch.trigger.accepted",
        actor="zf-autoresearch",
        payload={
            "trigger_id": "arinv-taskless",
            "invocation_id": "arinv-taskless",
            "source": "autoresearch.invocation.accepted",
            "mode": "supervised_diagnose",
            "apply_policy": "proposal_only",
            "severity": "critical",
            "reason": "runtime-level zaofu bug",
            "fingerprint": "zaofu_bug:taskless",
        },
    )
    log.append(event)

    decisions = _orchestrator_layer2(state_dir).run_once(events=[event])

    events = log.read_all()
    malformed = [item for item in events if item.type == "event.malformed"]
    candidates = [
        item for item in events
        if item.type == "autoresearch.bug_candidate.created"
    ]
    proposals = [
        item for item in events
        if item.type == "automation.proposal.created"
    ]
    assert [decision.action for decision in decisions] == ["notify"]
    assert malformed == []
    assert len(candidates) == 1
    assert len(proposals) == 1
    assert proposals[0].payload["action"] == "maintenance-prepare"


def test_autoresearch_invocation_rejects_direct_apply(
    tmp_path: Path,
) -> None:
    state_dir = _state_dir(tmp_path)
    log = EventLog(state_dir / "events.jsonl")
    event = ZfEvent(
        type="autoresearch.invocation.requested",
        actor="zf-supervisor",
        payload={
            "invocation_id": "arinv-direct",
            "level": "L3",
            "apply_policy": "direct_apply",
            "severity": "critical",
            "fingerprint": "bug:dangerous",
        },
    )
    log.append(event)

    decision = _orchestrator(state_dir)._on_autoresearch_invocation_requested(event)

    events = log.read_all()
    rejected = [item for item in events if item.type == "autoresearch.invocation.rejected"]
    triggers = [item for item in events if item.type == "autoresearch.trigger.accepted"]
    assert decision is not None and decision.action == "notify"
    assert len(rejected) == 1
    assert "only L1 diagnose" in rejected[0].payload["reason"]
    assert triggers == []
