"""A completed recovery handoff must not be sent again by the loss sweep."""

from pathlib import Path

import pytest

from tests.test_reader_fanout_runtime import _start_fanout, _state
from zf.core.events import ZfEvent
from zf.runtime.orchestrator import Orchestrator


def _dispatched(tmp_path: Path, synth: bool):
    state, log, transport, runtime = _state(tmp_path, synth=synth)
    _start_fanout(runtime)
    if synth:
        started = next(row for row in log.read_all() if row.type == "fanout.started")
        fanout = started.payload["fanout_id"]
        for child in ("review-a", "review-b"):
            runtime.run_once(events=[ZfEvent(
                type="review.approved", actor=child, correlation_id="trace-1",
                payload={"fanout_id": fanout, "child_id": child,
                         "run_id": f"run-{fanout}-{child}", "status": "approved"},
            )])
    kind = "fanout.synth.dispatched" if synth else "fanout.child.dispatched"
    dispatch = next(row for row in log.read_all() if row.type == kind)
    role = next(role for role in runtime.config.roles
                if role.instance_id == dispatch.payload["role_instance"])
    return state, log, transport, runtime, dispatch, role


def _launch(runtime, role):
    return runtime.event_writer.append(ZfEvent(
        type="worker.launch_artifact.written", actor="zf-cli",
        payload={"instance_id": role.instance_id, "role": role.name,
                 "backend": "mock", "launch_attempt": 2, "is_resume": False},
    ))


@pytest.mark.parametrize("synth", [False, True], ids=["reader", "synth"])
@pytest.mark.parametrize("reopen", [False, True], ids=["same-runtime", "reopen"])
def test_successful_recovery_handoff_is_not_redispatched(tmp_path: Path, synth: bool, reopen: bool):
    state, log, transport, runtime, dispatch, role = _dispatched(tmp_path, synth)
    _launch(runtime, role)
    active = runtime._active_fanout_child_for_instance(role.instance_id)
    assert active is not None
    before = len(transport.sent)
    assert runtime._inject_fanout_recovery_briefing(
        role, active, recovery_reason="active_fanout_after_watchdog",
    )
    assert len(transport.sent) == before + 1
    assert transport.sent[-1][1] == Path(dispatch.payload["briefing_path"])
    [injected] = [row for row in log.read_all() if row.type == "worker.recovery.injected"]
    assert injected.payload["run_id"] == dispatch.payload["run_id"]
    if reopen:
        runtime = Orchestrator(state, runtime.config, transport)
    for _ in range(2):
        runtime.run_once(events=[])
    assert not [row for row in log.read_all() if row.type == "fanout.child.dispatch_lost"]
    assert len(transport.sent) == before + 1
    assert [row.id for row in log.read_all() if row.type == dispatch.type
            and row.payload["role_instance"] == role.instance_id] == [dispatch.id]


@pytest.mark.parametrize("synth", [False, True], ids=["reader", "synth"])
@pytest.mark.parametrize("mismatch", [
    "fanout_id", "child_id", "run_id", "stage_id", "briefing_path", "actor", "trace",
    "missing_run_id", "before_launch", "later_launch", "closed_turn", "send_failed",
])
def test_unrelated_or_failed_handoff_does_not_hide_dispatch_loss(
    tmp_path: Path, monkeypatch, synth: bool, mismatch: str,
):
    _, log, transport, runtime, dispatch, role = _dispatched(tmp_path, synth)
    payload = {key: dispatch.payload[key] for key in (
        "fanout_id", "run_id", "stage_id", "briefing_path",
    )}
    # Legacy synth dispatches omit child_id; recovery preserves that empty
    # value rather than inventing a child identity.
    payload["child_id"] = dispatch.payload.get("child_id", "")
    actor, trace = role.instance_id, dispatch.correlation_id
    if mismatch in payload:
        payload[mismatch] = "unrelated"
    elif mismatch == "missing_run_id":
        payload.pop("run_id")
    elif mismatch == "actor":
        actor = "unrelated"
    elif mismatch == "trace":
        trace = "unrelated"
    if mismatch != "before_launch":
        _launch(runtime, role)
    if mismatch == "send_failed":
        active = runtime._active_fanout_child_for_instance(role.instance_id)
        with monkeypatch.context() as broken:
            def fail_send(*_args, **_kwargs):
                raise OSError("controlled send failure")
            broken.setattr(transport, "send_task", fail_send)
            assert not runtime._inject_fanout_recovery_briefing(role, active)
        assert not any(row.type == "worker.recovery.injected" for row in log.read_all())
    else:
        runtime.event_writer.append(ZfEvent(
            type="worker.recovery.injected", actor=actor,
            payload=payload, correlation_id=trace,
        ))
    if mismatch in {"before_launch", "later_launch"}:
        _launch(runtime, role)
    elif mismatch == "closed_turn":
        runtime.event_writer.append(ZfEvent(
            type="provider.turn.closed", actor=role.instance_id,
            payload={"backend": "codex", "turn_id": "closed-after-handoff"},
        ))
    before = len(transport.sent)
    runtime.run_once(events=[])
    [lost] = [row for row in log.read_all() if row.type == "fanout.child.dispatch_lost"]
    assert lost.payload["run_id"] == dispatch.payload["run_id"]
    assert len(transport.sent) == before + 1
    assert len([row for row in log.read_all() if row.type == dispatch.type
                and row.payload["role_instance"] == role.instance_id]) == 2
