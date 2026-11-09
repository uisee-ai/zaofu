"""Operator restarts must continue an active reader dispatch without false loss."""

from types import SimpleNamespace

import pytest

from tests.test_reader_fanout_runtime import _task_map_synth_orchestrator
from zf.cli.restart import _restart_role
from zf.core.events.model import ZfEvent
from zf.runtime.workflow_operation import reduce_workflow_operations
from zf.runtime.reader_respawn_continuation import reader_respawn_in_progress
from zf.runtime.session_mutex import SessionLock


def _running_recovered_synth(tmp_path, *, kind="synth"):
    state, log, runtime, fanout = _task_map_synth_orchestrator(
        tmp_path,
        complete_child=kind == "synth",
    )
    name = "task-map-synth" if kind == "synth" else "source-reader"
    role = next(r for r in runtime.config.roles if r.name == name)
    dispatch = next(e for e in log.read_all() if e.type == f"fanout.{kind}.dispatched")
    identity = dispatch.payload
    before = reduce_workflow_operations(log.read_all()).get(
        identity.get("operation_id"), {}
    )
    log.append(
        ZfEvent(
            type="fanout.child.dispatch_lost",
            actor="zf-cli",
            payload={
                "fanout_id": fanout,
                "child_id": identity.get("child_id") or "synth",
                "run_id": identity["run_id"],
            },
        )
    )
    dispatch = ZfEvent(
        type=dispatch.type,
        actor="zf-cli",
        correlation_id=dispatch.correlation_id,
        payload=dict(identity),
    )
    log.append(dispatch)
    return state, log, runtime, role, dispatch, before


@pytest.mark.parametrize("during_restart", [True, False])
@pytest.mark.parametrize("kind", ["synth", "child"])
def test_cli_restart_does_not_exhaust_recovered_synth(
    tmp_path, monkeypatch, during_restart, kind
):
    monkeypatch.setenv("ZF_CLI_CMD", "/current/runtime/python -E -m zf.cli.main")
    state, log, runtime, role, dispatch, before = _running_recovered_synth(
        tmp_path, kind=kind
    )
    identity = dispatch.payload
    recover = (
        runtime._recover_lost_fanout_synth_dispatches
        if kind == "synth"
        else runtime._recover_lost_reader_fanout_dispatches
    )
    terminal = "fanout.synth.completed" if kind == "synth" else "fanout.child.failed"
    alive = [True]
    monkeypatch.setattr(
        runtime.transport,
        "terminate",
        lambda _role: alive.__setitem__(0, False),
        raising=False,
    )
    monkeypatch.setattr(runtime.transport, "is_alive", lambda _role: alive[0])
    monkeypatch.setattr(runtime, "_wait_role_ready", lambda _role: True)
    monkeypatch.setattr(
        "zf.cli.restart.WorkflowRuntimeCoordinator", lambda *a, **kw: runtime
    )

    def spawn(_role, **_kwargs):
        alive[0] = True
        log.append(
            ZfEvent(
                type="worker.launch_artifact.written",
                actor="zf-cli",
                payload={
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "backend": "mock",
                    "launch_attempt": 2,
                    "is_resume": True,
                },
            )
        )
        if during_restart:
            recover(log.read_all())

    monkeypatch.setattr(
        runtime, "_get_spawn_coordinator", lambda: SimpleNamespace(spawn=spawn)
    )
    assert (
        _restart_role(
            role.name, runtime.config, runtime.transport, log, state, tmp_path, False
        )
        == 0
    )
    recover(log.read_all())

    assert not [e for e in log.read_all() if e.type == terminal]
    if before:
        after = reduce_workflow_operations(log.read_all())[identity["operation_id"]]
        assert after["status"] == "running"
        for key in ("request_hash", "request_ref", "active_attempt_id"):
            assert after[key] == before[key]
    recovered = [e for e in log.read_all() if e.type == "worker.recovery.injected"]
    assert len(recovered) == 1
    assert recovered[0].payload["run_id"] == identity["run_id"]
    assert recovered[0].payload["recovered_dispatch_event_id"] == dispatch.id
    assert recovered[0].payload["request_hash"] == identity.get("request_hash", "")
    assert "/current/runtime/python -E -m zf.cli.main" in runtime.transport.sent[-1][2]
    assert runtime.transport.sent[-1][3].operation_id == identity.get(
        "operation_id", ""
    )

    # A later provider stop is a new loss, even after successful delivery.
    log.append(
        ZfEvent(
            type="provider.turn.closed",
            actor=role.instance_id,
            payload={"backend": "codex", "turn_id": "new-turn-without-result"},
        )
    )
    assert recover(log.read_all())
    failure = next(e for e in log.read_all() if e.type == terminal)
    assert (
        failure.payload["reason"]
        == f"provider_turn_closed_without_{kind}_result_recovery_exhausted"
    )


@pytest.mark.parametrize(
    "wrong_key",
    [
        "recovered_dispatch_event_id",
        "request_hash",
        "operation_id",
        "run_id",
        "actor",
    ],
)
def test_unbound_recovery_does_not_hide_a_lost_synth(tmp_path, wrong_key):
    _state, log, runtime, role, dispatch, _before = _running_recovered_synth(tmp_path)
    log.append(
        ZfEvent(
            type="worker.launch_artifact.written",
            actor="zf-cli",
            payload={"instance_id": role.instance_id, "launch_attempt": 2},
        )
    )
    payload = {
        k: dispatch.payload[k]
        for k in (
            "fanout_id",
            "run_id",
            "request_hash",
            "operation_id",
        )
    }
    payload["recovered_dispatch_event_id"] = dispatch.id
    actor = role.instance_id
    if wrong_key == "actor":
        actor = "another-reader"
    else:
        payload[wrong_key] = "wrong-identity"
    log.append(ZfEvent(type="worker.recovery.injected", actor=actor, payload=payload))
    assert runtime._recover_lost_fanout_synth_dispatches(log.read_all())
    assert any(e.type == "fanout.synth.completed" for e in log.read_all())


def test_only_a_held_lock_for_the_same_role_defers_recovery(tmp_path):
    lock_dir = tmp_path / "locks" / "respawns"
    with SessionLock(lock_dir, "reviewer"):
        assert reader_respawn_in_progress(tmp_path, "reviewer")
        assert not reader_respawn_in_progress(tmp_path, "other-reviewer")
    stale = lock_dir / "reviewer.lock"
    stale.write_text("stale pid\n")
    assert not reader_respawn_in_progress(tmp_path, "reviewer")
    assert stale.read_text() == "stale pid\n"
