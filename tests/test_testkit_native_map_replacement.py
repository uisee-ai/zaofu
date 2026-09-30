"""K550 observer contracts with real stores/scheduler and no Provider process."""

import json
import os
from types import SimpleNamespace

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import (
    _submit_generation_stage,
    blocking_runtime,  # noqa: F401
)
from tests.testkit import native_map_replacement as replacement
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.result_submit import provision_role_submit_credential
from zf.runtime.run_manager import run_manager_tick
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.workflow_operation import reduce_workflow_operations


def _observer(blocking_runtime, monkeypatch, *, old_stage="impl", tokens=None):
    runtime, transport, _ = blocking_runtime
    runtime._dispatch_ready()
    [old] = transport.contexts
    if old_stage == "verify":
        _submit_generation_stage(runtime, old, tokens["impl-1"], repair=True)
        runtime._react_to_events()
        runtime._dispatch_ready()
        old = transport.contexts[-1]
        assert old.task_pipeline_stage == "verify"
    registry = RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", str(runtime.project_root))
    sid = registry.task_stage_bindings()[old.task_stage_session_binding]["session_id"]
    transcript = runtime.project_root / "observer-transcript.jsonl"
    transcript.write_text('{"incomplete":', encoding="utf-8")
    monkeypatch.setattr(replacement, "claude_session_path", lambda *_: transcript)
    monkeypatch.setattr(replacement, "_owned_claude_pid", lambda *_: 12345)
    stopped = []
    monkeypatch.setattr(replacement, "_stop_owned_provider", stopped.append)
    tmux = SimpleNamespace(pane_process_probe=lambda *a, **kw: {
        "pane_pid": "11111", "processes": [{"pid": "12345"}],
    })
    properties = {}
    observer = replacement.NativeMapReplacement(runtime, tmux, properties.__setitem__, old_stage=old_stage)
    replacement.install_scheduler_observer(monkeypatch, observer)
    return observer, transcript, sid, stopped, properties


def test_map_pause_requires_real_assistant_and_live_scheduler_wait(blocking_runtime, monkeypatch):
    runtime, transport, _ = blocking_runtime
    observer, transcript, sid, stopped, properties = _observer(blocking_runtime, monkeypatch)
    before = runtime.event_log.read_all()
    store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    [old] = transport.contexts
    original_lease = store.get(old.attempt_id)["lease_expires_at"]
    observer.poll(transport.contexts, before)
    assert stopped == [] and observer.old is None
    assert runtime.event_log.read_all() == before
    transcript.write_text(json.dumps({"type": "assistant", "sessionId": "unrelated"}), encoding="utf-8")
    observer.poll(transport.contexts, before)
    assert stopped == []
    transcript.write_text(json.dumps({"type": "assistant", "sessionId": sid}) + "\n{", encoding="utf-8")
    observer.poll(transport.contexts, before)
    assert stopped == [12345] and observer.old == old
    assert store.get(old.attempt_id)["lease_expires_at"] == original_lease
    assert task_pipeline_generation_contexts(runtime.event_log.read_all())[old.task_id]["task_map_generation"] == replacement.NEW_MAP
    after_admission = runtime.event_log.read_all()
    added = [e.type for e in after_admission[len(before):]]
    assert "task.pipeline.generation.admitted" in added
    assert not any(kind.startswith(("worker.respawn.", "workflow.operation.redrive", "task.attempt."))
                   for kind in added)
    # Polling alone cannot manufacture an observed scheduler wait.
    observer.poll(transport.contexts, after_admission)
    assert observer.schedule is None and not observer.clock_injected
    assert store.get(old.attempt_id)["lease_expires_at"] == original_lease
    assert runtime._dispatch_ready() == []
    assert len(transport.contexts) == 1
    assert observer.schedule["task"]["blockers"] == ["no_compatible_idle_role"]
    assert store.get(old.attempt_id)["status"] == "sent"
    events = runtime.event_log.read_all()
    observer.poll(transport.contexts, events)
    assert observer.clock_injected
    assert store.get(old.attempt_id)["status"] == "sent"
    assert store.get(old.attempt_id)["lease_expires_at"] == replacement.EXPIRED_CLOCK
    assert runtime.event_log.read_all() == events
    # The observer accelerates a buffered heartbeat only, never writes terminal state.
    store.renew_lease(old.attempt_id, updated_at="2026-09-24T00:00:00+00:00",
                      lease_expires_at="2099-01-01T00:00:00+00:00")
    observer.poll(transport.contexts, events)
    assert store.get(old.attempt_id)["lease_expires_at"] == replacement.EXPIRED_CLOCK
    runtime.run_once()
    assert store.get(old.attempt_id)["status"] == "expired"
    assert reduce_workflow_operations(runtime.event_log.read_all())[old.operation_id]["status"] == "suspended"
    terminal = store.get(old.attempt_id)
    observer.poll(transport.contexts, runtime.event_log.read_all())
    assert store.get(old.attempt_id) == terminal and stopped == [12345]
    assert json.loads(properties["native_map_fault"])["old_session_id"] == sid
    assert "native_map_live_wait" in properties


def test_map_observer_rejects_missed_inflight_window(blocking_runtime, monkeypatch):
    runtime, transport, _ = blocking_runtime
    observer, transcript, sid, stopped, _ = _observer(blocking_runtime, monkeypatch)
    transcript.write_text(json.dumps({"type": "assistant", "sessionId": sid}), encoding="utf-8")
    store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    store.update(transport.contexts[0].attempt_id, status="succeeded", updated_at="2026-09-24T00:00:00+00:00")
    before = runtime.event_log.read_all()
    with pytest.raises(AssertionError, match="missed in-flight"):
        observer.poll(transport.contexts, before)
    assert stopped == [] and runtime.event_log.read_all() == before


def test_map_verifier_cannot_accept_plan_and_clock_only(blocking_runtime, monkeypatch):
    runtime, transport, _ = blocking_runtime
    observer, transcript, sid, _, _ = _observer(blocking_runtime, monkeypatch)
    transcript.write_text(json.dumps({"type": "assistant", "sessionId": sid}), encoding="utf-8")
    observer.poll(transport.contexts, runtime.event_log.read_all())
    runtime._dispatch_ready()
    observer.poll(transport.contexts, runtime.event_log.read_all())
    with pytest.raises(AssertionError):
        observer.verify(transport.contexts, runtime.event_log.read_all())


def test_verify_observer_does_not_pause_preceding_impl(blocking_runtime, monkeypatch):
    runtime, transport, _ = blocking_runtime
    impl_observer, transcript, sid, stopped, properties = _observer(blocking_runtime, monkeypatch)
    transcript.write_text(json.dumps({"type": "assistant", "sessionId": sid}), encoding="utf-8")
    observer = replacement.NativeMapReplacement(runtime, impl_observer.tmux,
                                                properties.__setitem__, old_stage="verify")
    before = runtime.event_log.read_all()
    observer.poll(transport.contexts, before)
    assert observer.old is None and stopped == [] and properties == {}
    assert runtime.event_log.read_all() == before


@pytest.mark.parametrize("reopen", [False, True], ids=["same-runtime", "reconstructed"])
@pytest.mark.parametrize("liveness", ["alive", "shell", "probe-error"])
@pytest.mark.parametrize("old_stage", ["impl", "verify"])
def test_map_observer_follows_real_recovery_and_candidate(
    blocking_runtime, monkeypatch, reopen, liveness, old_stage,
):
    runtime, transport, _ = blocking_runtime
    runtime.config.roles.append(RoleConfig(
        name="verify", instance_id="verify-1", backend="mock", role_kind="reader",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    ))
    task = runtime.task_store.get("TASK-BLOCKING")
    task.contract.scope = ["README.md"]
    task.contract.verification = "git cat-file -e HEAD:README.md"
    task.contract.validation = {"commands": [{
        "id": "readme", "command": task.contract.verification, "rolling_smoke": True,
        "tier": "runtime", "deterministic": True, "reusable": True,
    }]}
    runtime.task_store.update(task.id, contract=task.contract)
    tokens = {role: provision_role_submit_credential(runtime.state_dir, role)
              for role in ("impl-1", "verify-1")}
    observer, transcript, sid, stopped, properties = _observer(
        blocking_runtime, monkeypatch, old_stage=old_stage, tokens=tokens,
    )
    transcript.write_text(json.dumps({"type": "assistant", "sessionId": sid}), encoding="utf-8")
    [old_dispatch] = [e for e in runtime.event_log.read_all() if e.type == "task.pipeline.stage.dispatched"
                      and e.payload["stage_id"] == old_stage]
    old_role = f"{old_stage}-1"
    alive = True
    physical = []

    def terminate(instance):
        nonlocal alive
        physical.append(("terminate", instance))
        alive = False

    def reported_alive(instance):
        if instance != old_role:
            return False
        if alive and liveness == "probe-error":
            raise RuntimeError("injected liveness probe failure")
        return alive and liveness == "alive"

    monkeypatch.setattr(transport, "is_alive", reported_alive)
    monkeypatch.setattr(transport, "lifecycle_snapshot", lambda instance: SimpleNamespace(
        current_path=old_dispatch.payload["workdir"]
        if instance == old_role and alive and liveness == "alive" else ""), raising=False)
    monkeypatch.setattr(transport, "terminate", terminate, raising=False)
    monkeypatch.setattr(transport, "spawn", lambda role, argv, **kw: physical.append(
        ("spawn", role.instance_id)), raising=False)
    observer.poll(transport.contexts, runtime.event_log.read_all())
    # No actual PIDs in this offline test; OS exit proof belongs to explicit host tier.
    observer.old_pids = set()
    if old_stage == "verify":
        assert len(transport.contexts) == 2
        store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
        old_lease = store.get(observer.old.attempt_id)["lease_expires_at"]
        assert len(runtime._dispatch_ready()) == 1
        assert len(transport.contexts) == 3
        assert transport.contexts[-1].task_pipeline_stage == "impl"
        observer.poll(transport.contexts, runtime.event_log.read_all())
        assert observer.schedule is None and not observer.clock_injected
        assert store.get(observer.old.attempt_id)["lease_expires_at"] == old_lease
        _submit_generation_stage(runtime, transport.contexts[-1], tokens["impl-1"], repair=True)
        runtime._react_to_events()
    assert runtime._dispatch_ready() == []
    before_recovery = 1 if old_stage == "impl" else 3
    observer.poll(transport.contexts, runtime.event_log.read_all())
    runtime.run_once()
    assert len(transport.contexts) == before_recovery
    # Another scheduler wake can precede the Run Manager periodic tick.
    # A shell/dead/probe-error observation is not recovery authorization.
    assert runtime._dispatch_ready() == [], "cross-map dispatch bypassed recovery authority"
    assert len(transport.contexts) == before_recovery
    assert run_manager_tick(
        state_dir=runtime.state_dir, writer=runtime.event_writer, config=runtime.config,
        project_root=runtime.project_root, event_log=runtime.event_log,
        action_filter={"worker-lifecycle-recover"}, spawn_repairs=False,
    ).actions_applied == 1
    if reopen:
        runtime = Orchestrator(runtime.state_dir, runtime.config, transport, project_root=runtime.project_root)
    runtime.run_once()
    assert physical == [("terminate", old_role), ("spawn", old_role)]
    assert len(transport.contexts) == before_recovery + 1
    events = runtime.event_log.read_all()
    # An interruption/failure record is NOT successful recovery authorization.
    for missing in ("worker.respawn.requested", "worker.respawn.completed",
                    "workflow.operation.redrive_admitted"):
        with pytest.raises(AssertionError):
            observer._check_handover(transport.contexts[-1],
                                    [e for e in events if e.type != missing],
                                    TaskAttemptStore(runtime.state_dir / "task_attempts.json"))
        assert observer.handover is None
    # Real PID presence defeats an otherwise complete receipt chain.
    observer.old_pids = {str(os.getpid())}
    with pytest.raises(AssertionError, match="survived production handover"):
        observer.poll(transport.contexts, events)
    observer.old_pids = set()
    observer.poll(transport.contexts, events)
    assert observer.handover["late_result_code"] == "operation_not_running"
    assert observer.handover["old_attempt_status"] == "expired"
    for stage in (("impl", "verify") if old_stage == "impl" else ("verify",)):
        context = transport.contexts[-1]
        assert context.task_pipeline_stage == stage and context.task_map_generation == replacement.NEW_MAP
        _submit_generation_stage(runtime, context, tokens[context.instance_id], repair=stage == "impl")
        runtime.run_once()
    completed = observer.verify(transport.contexts, runtime.event_log.read_all())
    assert [c.task_pipeline_stage for c in completed] == ["impl", "verify"]
    assert runtime.task_store.get(task.id).status == "done"
    for _ in range(2):
        runtime = Orchestrator(runtime.state_dir, runtime.config, transport, project_root=runtime.project_root)
        runtime.run_once()
    assert len(transport.contexts) == observer.expected_contexts and stopped == [12345]
    assert "native_map_replacement" in properties
    if old_stage == "verify":
        assert "native_map_prior_impl" in properties
        prior_id = observer.prior_impl["operation_id"]
        with pytest.raises(AssertionError):
            observer._check_prior_impl(
                [e for e in runtime.event_log.read_all() if e.payload.get("operation_id") != prior_id],
                TaskAttemptStore(runtime.state_dir / "task_attempts.json"),
            )
        with monkeypatch.context() as patch:
            patch.setattr(replacement.subprocess, "check_output", lambda *a, **k: b"wrong-commit")
            with pytest.raises(AssertionError, match="pinned workspace"):
                observer._check_prior_impl(runtime.event_log.read_all(),
                                           TaskAttemptStore(runtime.state_dir / "task_attempts.json"))
