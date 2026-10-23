from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import (
    ExecutionConfig,
    ExecutionRouteConfig,
    ProjectConfig,
    ProviderSessionConfig,
    RoleConfig,
    RuntimeConfig,
    RuntimeExecutionRoutingConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.session import SessionStore, WorkerState
from zf.core.task.schema import Task, TaskExecutionBinding
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.execution_policy_routing import (
    EXECUTION_ROUTE_APPLIED_EVENT,
    EXECUTION_ROUTE_SWITCH_ACTION,
    ExecutionRouteError,
    classify_execution_route_trigger,
    enrich_execution_route_action,
    execution_route_action_preflight,
    execution_route_policy_digest,
    pending_execution_route_actions,
)
from zf.runtime.execution_route_state import (
    ExecutionRouteStore,
    route_policy_for_spawn,
)
from zf.runtime.run_manager_rework_triage import (
    TRIAGE_RECORDED,
    TRIAGE_REQUESTED,
    pending_rework_triage_actions,
)
from zf.runtime.run_manager import _resident_action_focus_prompt, run_manager_tick
from zf.runtime.run_manager_router import decide_action_policy
from zf.runtime.spawn_coordinator import SpawnCoordinator


def _role() -> RoleConfig:
    return RoleConfig(
        name="dev",
        instance_id="dev-lane-0",
        backend="claude-code",
        flow_kind="prd",
        permission_mode="allowlist",
        allowed_tools=["Read", "Edit"],
        execution=ExecutionConfig(
            default_profile="direct-v1",
            profile_allowlist=["direct-v1"],
        ),
    )


def _route(
    *,
    route_id: str = "codex-fallback",
    trigger: str = "provider_rate_limited",
) -> ExecutionRouteConfig:
    return ExecutionRouteConfig(
        id=route_id,
        roles=["dev"],
        flow_kinds=["prd"],
        backend="codex",
        model="gpt-5.4",
        model_reasoning_effort="high",
        execution_profile="direct-v1",
        provider_session=ProviderSessionConfig(
            effort="ultra",
            max_parallel_agents=2,
        ),
        automatic_triggers=[trigger],
    )


@pytest.mark.parametrize("native", [None, False, True])
def test_execution_route_keeps_native_delegation_restriction(native):
    from dataclasses import replace
    from zf.runtime.execution_policy_routing import apply_execution_route, ExecutionRouteError

    role = replace(_role(), provider_session=ProviderSessionConfig(native_delegation=False))
    route = replace(_route(), provider_session=ProviderSessionConfig(native_delegation=native))
    if native is True:
        with pytest.raises(ExecutionRouteError, match="cannot enable native delegation"):
            apply_execution_route(role, route)
    else:
        assert apply_execution_route(role, route).provider_session.native_delegation is False


def _config(*, route: ExecutionRouteConfig | None = None) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="execution-routing-test"),
        roles=[_role()],
        runtime=RuntimeConfig(
            execution_routing=RuntimeExecutionRoutingConfig(
                enabled=True,
                max_switches_per_task=1,
                semantic_triage_attempt=3,
                routes=[route or _route()],
            ),
        ),
    )


def _state(tmp_path: Path) -> tuple[Path, EventLog, EventWriter, str]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    session = SessionStore(state_dir / "session.yaml").create(str(tmp_path))
    SessionStore(state_dir / "session.yaml").upsert_worker(WorkerState(
        role="dev-lane-0",
        state="working",
        last_dispatch="TASK-1",
    ))
    TaskStore(state_dir / "kanban.json").add(Task(
        id="TASK-1",
        title="Implement provider fallback fixture",
        status="in_progress",
        assigned_to="dev-lane-0",
        active_dispatch_id="dispatch-1",
        execution_binding=TaskExecutionBinding(
            owner="workflow",
            request_id="request-1",
            workflow_run_id="workflow-run-1",
        ),
    ))
    log = EventLog(state_dir / "events.jsonl")
    return state_dir, log, EventWriter(log), session.session_id


def _loader_body() -> dict:
    return {
        "version": "1.0",
        "project": {"name": "route-loader", "state_dir": ".zf"},
        "roles": [{
            "name": "dev",
            "backend": "claude-code",
            "flow_kind": "prd",
            "permission_mode": "allowlist",
            "allowed_tools": ["Read", "Edit"],
            "execution": {
                "default_profile": "direct-v1",
                "profile_allowlist": ["direct-v1"],
            },
        }],
        "runtime": {
            "execution_routing": {
                "enabled": True,
                "max_switches_per_task": 1,
                "semantic_triage_attempt": 3,
                "routes": [{
                    "id": "codex-fallback",
                    "roles": ["dev"],
                    "flow_kinds": ["prd"],
                    "backend": "codex",
                    "model": "gpt-5.4",
                    "model_reasoning_effort": "high",
                    "execution_profile": "direct-v1",
                    "provider_session": {
                        "effort": "ultra",
                        "max_parallel_agents": 2,
                    },
                    "automatic_triggers": ["provider_rate_limited"],
                }],
            },
        },
    }


def test_loader_accepts_static_route_and_rejects_unknown_policy_key(
    tmp_path: Path,
) -> None:
    body = _loader_body()
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")

    config = load_config(path)

    policy = config.runtime.execution_routing
    assert policy.enabled is True
    assert policy.max_switches_per_task == 1
    assert policy.routes[0].provider_session == ProviderSessionConfig(
        effort="ultra",
        max_parallel_agents=2,
    )

    body["runtime"]["execution_routing"]["guess_route"] = True
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown key.*guess_route"):
        load_config(path)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda body: body["runtime"]["execution_routing"]["routes"].append(
                deepcopy(body["runtime"]["execution_routing"]["routes"][0])
            ),
            "duplicates id",
        ),
        (
            lambda body: body["runtime"]["execution_routing"]["routes"][0].update(
                roles=["missing-role"]
            ),
            "unknown role",
        ),
        (
            lambda body: body["runtime"]["execution_routing"]["routes"][0].update(
                execution_profile="missing-profile"
            ),
            "unknown profile",
        ),
        (
            lambda body: body["runtime"]["execution_routing"]["routes"][0].update(
                automatic_triggers=["test_failed"]
            ),
            "unsupported value",
        ),
        (
            lambda body: body["runtime"]["execution_routing"]["routes"][0].update(
                backend="python"
            ),
            "backend must be one of",
        ),
        (
            lambda body: body["runtime"]["execution_routing"]["routes"][0][
                "provider_session"
            ].update(max_parallel_agents=7),
            "must be <= 6",
        ),
    ],
)
def test_loader_rejects_unsafe_or_ambiguous_routes(
    tmp_path: Path,
    mutator,
    message: str,
) -> None:
    body = _loader_body()
    mutator(body)
    path = tmp_path / "zf.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_trigger_classifier_requires_structured_provider_or_triage_fact() -> None:
    assert classify_execution_route_trigger(ZfEvent(
        type="dev.failed",
        actor="dev",
        task_id="TASK-1",
        payload={
            "reason": "provider rate limited",
            "execution_route_trigger": "provider_rate_limited",
        },
    )) == ""
    assert classify_execution_route_trigger(ZfEvent(
        type="provider.stop.recovery",
        actor="provider-monitor",
        task_id="TASK-1",
        payload={"provider_stop_reason": "rate_limited"},
    )) == "provider_rate_limited"
    assert classify_execution_route_trigger(ZfEvent(
        type=TRIAGE_RECORDED,
        actor="orchestrator",
        task_id="TASK-1",
        payload={"execution_route_trigger": "provider_capability_mismatch"},
    )) == "provider_capability_mismatch"


def test_immediate_switch_is_task_bound_idempotent_and_used_at_spawn(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, session_id = _state(tmp_path)
    config = _config()
    source = writer.emit(
        "provider.stop.recovery",
        actor="provider-monitor",
        task_id="TASK-1",
        correlation_id="workflow-run-1",
        payload={
            "provider_stop_reason": "rate_limited",
            "failure_fingerprint": "provider-rate-limited",
        },
    )

    actions = pending_execution_route_actions(
        state_dir,
        config=config,
        events=log.read_all(),
    )

    assert len(actions) == 1
    action = actions[0]
    assert action["workflow_run_id"] == "workflow-run-1"
    assert action["workflow_run_id"] != session_id
    assert action["source_event_id"] == source.id
    assert execution_route_action_preflight(action)["status"] == "passed"
    decision = decide_action_policy(
        action=EXECUTION_ROUTE_SWITCH_ACTION,
        payload=action,
    )
    assert decision["decision"] == "auto_decide"

    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=tmp_path,
        actor="run-manager",
        source="run-manager",
        surface="run-manager",
    )
    requested = writer.emit(
        "control.action.requested",
        actor="run-manager",
        task_id="TASK-1",
        correlation_id="workflow-run-1",
        payload={"action": EXECUTION_ROUTE_SWITCH_ACTION, **action},
    )
    first = service._execute_action(
        requested=requested,
        action=EXECUTION_ROUTE_SWITCH_ACTION,
        requested_action=EXECUTION_ROUTE_SWITCH_ACTION,
        payload=action,
    )
    replay_request = writer.emit(
        "control.action.requested",
        actor="run-manager",
        task_id="TASK-1",
        correlation_id="workflow-run-1",
        payload={"action": EXECUTION_ROUTE_SWITCH_ACTION, **action},
    )
    replay = service._execute_action(
        requested=replay_request,
        action=EXECUTION_ROUTE_SWITCH_ACTION,
        requested_action=EXECUTION_ROUTE_SWITCH_ACTION,
        payload=action,
    )

    assert first["status"] == "applied"
    assert replay["status"] == "already_applied"
    events = log.read_all()
    assert sum(event.type == EXECUTION_ROUTE_APPLIED_EVENT for event in events) == 1
    assert sum(event.type == "worker.respawn.requested" for event in events) == 1
    receipt_ref = str(first["receipt_ref"]["ref"])
    assert replay["receipt_ref"]["ref"] == receipt_ref
    assert len(list((state_dir / "artifacts" / "execution-routing" / "receipts").glob(
        "*.json"
    ))) == 1
    receipt = json.loads((state_dir / receipt_ref).read_text(encoding="utf-8"))
    assert receipt["workflow_run_id"] == "workflow-run-1"
    assert receipt["effective_route"]["id"] == "codex-fallback"

    # Re-dispatch changes its CAS identity. The already-authorized task route
    # must survive so provider-session preparation can resume the same task.
    TaskStore(state_dir / "kanban.json").update(
        "TASK-1",
        active_dispatch_id="dispatch-2",
    )
    original = config.roles[0]
    routed, plan = route_policy_for_spawn(
        state_dir=state_dir,
        config=config,
        role=original,
    )
    assert plan["applies"] is True
    assert routed.backend == "codex"
    assert routed.provider_session == ProviderSessionConfig(
        effort="ultra",
        max_parallel_agents=2,
    )
    assert routed.permission_mode == original.permission_mode
    assert routed.allowed_tools == original.allowed_tools

    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    coordinator = SpawnCoordinator(
        state_dir=state_dir,
        registry=registry,
        transport=object(),
        project_root=str(tmp_path),
        event_log=log,
        config=config,
    )
    effective, provider_config, _ = coordinator.prepare_provider_session(original)
    assert effective.backend == "codex"
    assert provider_config.snapshot["provider"]["backend"] == "codex"
    assert provider_config.snapshot["resolved"]["effort"]["value"] == "ultra"


def test_switch_cap_rejects_second_route_for_same_task(tmp_path: Path) -> None:
    state_dir, _, _, _ = _state(tmp_path)
    store = ExecutionRouteStore(state_dir)
    first_route = _route()
    receipt = {"ref": "artifacts/route-1.json", "sha256": "a" * 64}
    first = store.activate(
        task_id="TASK-1",
        workflow_run_id="workflow-run-1",
        role="dev",
        instance_id="dev-lane-0",
        dispatch_id="dispatch-1",
        route=first_route,
        trigger_class="provider_rate_limited",
        source_event_id="source-1",
        source_event_type="provider.stop.recovery",
        source_event_ids=["source-1"],
        checkpoint_id="checkpoint-1",
        action_id="action-1",
        policy_digest="digest-1",
        receipt=receipt,
        max_switches=1,
    )
    replay = store.activate(
        task_id="TASK-1",
        workflow_run_id="workflow-run-1",
        role="dev",
        instance_id="dev-lane-0",
        dispatch_id="dispatch-1",
        route=first_route,
        trigger_class="provider_rate_limited",
        source_event_id="source-1",
        source_event_type="provider.stop.recovery",
        source_event_ids=["source-1"],
        checkpoint_id="checkpoint-1",
        action_id="action-1",
        policy_digest="digest-1",
        receipt=receipt,
        max_switches=1,
    )
    assert first["applied"] is True
    assert replay["applied"] is False

    with pytest.raises(ExecutionRouteError, match="already used"):
        store.activate(
            task_id="TASK-1",
            workflow_run_id="workflow-run-1",
            role="dev",
            instance_id="dev-lane-0",
            dispatch_id="dispatch-2",
            route=first_route,
            trigger_class="provider_rate_limited",
            source_event_id="source-2",
            source_event_type="provider.stop.recovery",
            source_event_ids=["source-2"],
            checkpoint_id="checkpoint-2",
            action_id="action-2",
            policy_digest="digest-1",
            receipt={"ref": "artifacts/route-revisit.json", "sha256": "c" * 64},
            max_switches=2,
        )

    second_route = _route(
        route_id="codex-context-fallback",
        trigger="provider_context_exhausted",
    )
    with pytest.raises(ExecutionRouteError, match="switch cap exhausted"):
        store.activate(
            task_id="TASK-1",
            workflow_run_id="workflow-run-1",
            role="dev",
            instance_id="dev-lane-0",
            dispatch_id="dispatch-2",
            route=second_route,
            trigger_class="provider_context_exhausted",
            source_event_id="source-2",
            source_event_type="worker.context.critical",
            source_event_ids=["source-2"],
            checkpoint_id="checkpoint-2",
            action_id="action-2",
            policy_digest="digest-1",
            receipt={"ref": "artifacts/route-2.json", "sha256": "b" * 64},
            max_switches=1,
        )


def test_route_history_is_scoped_to_workflow_run(tmp_path: Path) -> None:
    state_dir, _, _, _ = _state(tmp_path)
    store = ExecutionRouteStore(state_dir)
    route = _route()
    first = store.activate(
        task_id="TASK-1",
        workflow_run_id="workflow-run-1",
        role="dev",
        instance_id="dev-lane-0",
        dispatch_id="dispatch-1",
        route=route,
        trigger_class="provider_rate_limited",
        source_event_id="source-1",
        source_event_type="provider.stop.recovery",
        source_event_ids=["source-1"],
        checkpoint_id="checkpoint-1",
        action_id="action-1",
        policy_digest="digest-1",
        receipt={"ref": "artifacts/route-1.json", "sha256": "a" * 64},
        max_switches=1,
    )
    second = store.activate(
        task_id="TASK-1",
        workflow_run_id="workflow-run-2",
        role="dev",
        instance_id="dev-lane-0",
        dispatch_id="dispatch-2",
        route=route,
        trigger_class="provider_rate_limited",
        source_event_id="source-2",
        source_event_type="provider.stop.recovery",
        source_event_ids=["source-2"],
        checkpoint_id="checkpoint-2",
        action_id="action-2",
        policy_digest="digest-1",
        receipt={"ref": "artifacts/route-2.json", "sha256": "b" * 64},
        max_switches=1,
    )

    assert first["record"]["switch_count"] == 1
    assert second["applied"] is True
    assert second["record"]["workflow_run_id"] == "workflow-run-2"
    assert second["record"]["switch_count"] == 1
    assert store.task_record(
        "TASK-1", workflow_run_id="workflow-run-1"
    ) is None


def test_stale_source_event_cannot_switch_current_workflow_run(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, _ = _state(tmp_path)
    config = _config()
    source = writer.emit(
        "provider.stop.recovery",
        actor="provider-monitor",
        task_id="TASK-1",
        correlation_id="workflow-run-old",
        payload={
            "provider_stop_reason": "rate_limited",
            "failure_fingerprint": "provider-rate-limited",
        },
    )

    assert pending_execution_route_actions(
        state_dir,
        config=config,
        events=log.read_all(),
    ) == []

    action = {
        "action": EXECUTION_ROUTE_SWITCH_ACTION,
        "safe_resume_action": "switch_execution_route",
        "checkpoint_id": "checkpoint-stale-source",
        "action_id": "action-stale-source",
        "workflow_run_id": "workflow-run-1",
        "task_id": "TASK-1",
        "role": "dev",
        "instance_id": "dev-lane-0",
        "dispatch_id": "dispatch-1",
        "flow_kind": "prd",
        "route_id": "codex-fallback",
        "trigger_class": "provider_rate_limited",
        "source_event_id": source.id,
        "source_event_ids": [source.id],
        "policy_digest": execution_route_policy_digest(config),
    }
    service = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=tmp_path,
        actor="run-manager",
        source="run-manager",
        surface="run-manager",
    )
    requested = writer.emit(
        "control.action.requested",
        actor="run-manager",
        task_id="TASK-1",
        correlation_id="workflow-run-1",
        payload=action,
    )
    result = service._execute_action(
        requested=requested,
        action=EXECUTION_ROUTE_SWITCH_ACTION,
        requested_action=EXECUTION_ROUTE_SWITCH_ACTION,
        payload=action,
    )

    assert result["status"] == "stale"
    assert not (state_dir / "execution-routing" / "receipts").exists()


def test_third_same_fingerprint_triage_can_select_declared_route(
    tmp_path: Path,
) -> None:
    state_dir, _, _, _ = _state(tmp_path)
    config = _config(route=_route(trigger="provider_capability_mismatch"))
    capped = ZfEvent(
        id="cap-1",
        type="task.rework.capped",
        actor="orchestrator",
        task_id="TASK-1",
        correlation_id="workflow-run-1",
        payload={
            "semantic_triage_required": True,
            "failure_count": 3,
            "failure_fingerprint": "provider-capability-mismatch",
            "failure_event_ids": ["failure-1", "failure-2", "failure-3"],
            "role": "dev-lane-0",
            "flow_kind": "prd",
            "workflow_run_id": "workflow-run-1",
            "dispatch_id": "dispatch-1",
        },
    )
    request_action = pending_rework_triage_actions(
        [capped],
        threshold=3,
        stale_seconds=300,
    )[0]
    request_id = str(request_action["request_id"])
    requested = ZfEvent(
        id="triage-request-1",
        type=TRIAGE_REQUESTED,
        actor="run-manager",
        task_id="TASK-1",
        correlation_id="workflow-run-1",
        payload={"request_id": request_id},
    )
    recorded = ZfEvent(
        id="triage-recorded-1",
        type=TRIAGE_RECORDED,
        actor="orchestrator",
        task_id="TASK-1",
        correlation_id="workflow-run-1",
        payload={
            "request_id": request_id,
            "recommended_action": "switch_execution_route",
            "execution_route_id": "codex-fallback",
            "execution_route_trigger": "provider_capability_mismatch",
            "evidence_event_ids": ["failure-1", "failure-2", "failure-3"],
        },
    )

    action = pending_rework_triage_actions(
        [capped, requested, recorded],
        threshold=3,
        stale_seconds=300,
    )[0]
    action = enrich_execution_route_action(
        action,
        state_dir=state_dir,
        config=config,
    )

    assert action["action"] == EXECUTION_ROUTE_SWITCH_ACTION
    assert action["route_id"] == "codex-fallback"
    assert action["trigger_class"] == "provider_capability_mismatch"
    assert action["workflow_run_id"] == "workflow-run-1"
    assert action["policy_digest"] == execution_route_policy_digest(config)
    assert execution_route_action_preflight(action)["status"] == "passed"


def test_resident_run_manager_triage_receives_same_route_catalog(
    tmp_path: Path,
) -> None:
    state_dir, _, _, _ = _state(tmp_path)
    config = _config(route=_route(trigger="provider_capability_mismatch"))
    capped = ZfEvent(
        id="cap-resident-route",
        type="task.rework.capped",
        actor="orchestrator",
        task_id="TASK-1",
        payload={
            "semantic_triage_required": True,
            "failure_count": 3,
            "failure_fingerprint": "provider-capability-mismatch",
            "failure_event_ids": ["failure-1", "failure-2", "failure-3"],
            "role": "dev-lane-0",
        },
    )
    action = pending_rework_triage_actions(
        [capped],
        threshold=3,
        stale_seconds=300,
        advisor_available=False,
        resident_advisor={
            "status": "running",
            "tmux_session": "zf-run-manager",
            "briefing_path": "/tmp/run-manager-briefing.md",
            "instance_id": "run-manager",
        },
    )[0]

    action = enrich_execution_route_action(
        action,
        state_dir=state_dir,
        config=config,
    )
    focus = _resident_action_focus_prompt(action)

    assert action["execution_route_catalog"][0]["id"] == "codex-fallback"
    assert "switch_execution_route" in action["recommended_actions"]
    assert "switch_execution_route" in focus
    assert "codex-fallback" in focus
    assert "execution_route_trigger" in focus


@pytest.mark.parametrize("stale_kind", ["reassigned", "run_drift", "terminal"])
def test_selected_route_does_not_leak_outside_current_task_owner(
    tmp_path: Path,
    stale_kind: str,
) -> None:
    state_dir, _, _, _ = _state(tmp_path)
    config = _config()
    role = config.roles[0]
    ExecutionRouteStore(state_dir).activate(
        task_id="TASK-1",
        workflow_run_id="workflow-run-1",
        role=role.name,
        instance_id=role.instance_id,
        dispatch_id="dispatch-1",
        route=config.runtime.execution_routing.routes[0],
        trigger_class="provider_rate_limited",
        source_event_id="source-1",
        source_event_type="provider.stop.recovery",
        source_event_ids=["source-1"],
        checkpoint_id="checkpoint-1",
        action_id="action-1",
        policy_digest=execution_route_policy_digest(config),
        receipt={"ref": "artifacts/route.json", "sha256": "a" * 64},
        max_switches=1,
    )
    tasks = TaskStore(state_dir / "kanban.json")
    if stale_kind == "reassigned":
        tasks.update("TASK-1", assigned_to="other-worker")
    elif stale_kind == "run_drift":
        tasks.update(
            "TASK-1",
            execution_binding=TaskExecutionBinding(
                owner="workflow",
                request_id="request-2",
                workflow_run_id="workflow-run-2",
            ),
        )
    else:
        tasks.update("TASK-1", status="done")

    effective, plan = route_policy_for_spawn(
        state_dir=state_dir,
        config=config,
        role=role,
    )

    assert effective == role
    assert plan == {"applies": False, "reason": "no_current_task_route"}


def test_run_manager_mock_e2e_applies_one_switch_and_settles_replay(
    tmp_path: Path,
) -> None:
    state_dir, log, writer, _ = _state(tmp_path)
    config = _config()
    writer.emit(
        "provider.stop.recovery",
        actor="provider-monitor",
        task_id="TASK-1",
        correlation_id="workflow-run-1",
        payload={
            "provider_stop_reason": "rate_limited",
            "failure_fingerprint": "provider-rate-limited",
        },
    )

    first = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        project_root=tmp_path,
        event_log=log,
        auto_execute=True,
        spawn_repairs=False,
    )
    second = run_manager_tick(
        state_dir=state_dir,
        writer=writer,
        config=config,
        project_root=tmp_path,
        event_log=log,
        auto_execute=True,
        spawn_repairs=False,
    )

    events = log.read_all()
    assert first.actions_applied == 1
    assert first.actions_blocked == 0
    assert first.actions_failed == 0
    assert second.actions_applied == 0
    assert sum(event.type == EXECUTION_ROUTE_APPLIED_EVENT for event in events) == 1
    assert sum(event.type == "worker.respawn.requested" for event in events) == 1
    assert sum(event.type == "run.manager.action.verify.passed" for event in events) == 1
