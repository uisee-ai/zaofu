"""Real SDK/subprocess interruption matrix; offline CLI, no provider network."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

import pytest

from tests.test_role_lifecycle_runtime import _runtime
from zf.core.task.schema import Task
from zf.runtime.session_mutex import SessionLock, SessionLockBusy
from zf.runtime.runtime_logs import read_runtime_logs
from zf.runtime.transport import DispatchContext
from zf.runtime.transport_stream_json import StreamJsonTransport


pytestmark = [pytest.mark.host, pytest.mark.serial]


class StreamCompletionTruthBug(AssertionError):
    """An incomplete or error provider result was reported as healthy."""


class StreamShutdownTruthBug(AssertionError):
    """Transport stop returned while its owned SDK process was still running."""


class StreamProcessTreeBug(AssertionError):
    """Transport stopped the SDK parent but left an owned child process alive."""


@pytest.fixture
def stream_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip(
        "claude_code_sdk", reason="requires the explicit stream-json extra"
    )
    from claude_code_sdk._internal.transport.subprocess_cli import (
        SubprocessCLITransport,
    )

    home = tmp_path / "provider-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    script = Path(__file__).parents[1] / "testkit/fixtures/stream_interruption_cli.py"
    # Replace only CLI discovery. query(), SDK parsing, pipe I/O, process close,
    # ZaoFu background dispatch/lock/metrics and kernel event draining stay real.
    monkeypatch.setattr(SubprocessCLITransport, "_find_cli", lambda self: str(script))
    processes = []
    from zf.runtime.stream_owned_process import OwnedSubprocessCLITransport

    original_connect = OwnedSubprocessCLITransport.connect

    async def observe_connect(sdk_transport):
        await original_connect(sdk_transport)
        processes.append(sdk_transport._process)

    monkeypatch.setattr(OwnedSubprocessCLITransport, "connect", observe_connect)
    runtime, role, _ = _runtime(tmp_path)
    transport = StreamJsonTransport(
        runtime.state_dir,
        runtime._role_lifecycle_registry(),
        cwd=tmp_path,
        background_dispatch=True,
        timeout_s=5,
    )
    runtime.transport = transport
    audit = tmp_path / "process-audit.json"
    child_audit = tmp_path / "child-audit.json"
    transport.spawn(
        role,
        [
            "env",
            f"ZF_STREAM_AUDIT={audit}",
            f"ZF_STREAM_CHILD_AUDIT={child_audit}",
        ],
        cwd=tmp_path,
    )
    runtime.task_store.add(
        Task(
            id="TASK-STREAM",
            title="stream boundary",
            status="in_progress",
            assigned_to=role.instance_id,
        )
    )
    try:
        yield runtime, transport, role, audit, processes
    finally:
        # A test must prove cleanup before teardown; watchdog/join is a fallback.
        for thread in tuple(transport._dispatch_threads.values()):
            thread.join(10)
            assert not thread.is_alive(), "owned stream worker did not finish"
        transport.shutdown()
        assert all(process.returncode is not None for process in processes)
        if audit.exists():
            pid = json.loads(audit.read_text())["pid"]
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)


@pytest.fixture
def reap_stream_child(stream_runtime):
    """Adopt this fixture's orphan on Linux; never leave cleanup to host init."""
    if sys.platform != "linux":
        pytest.skip("owned orphan cleanup requires Linux PR_SET_CHILD_SUBREAPER")
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    assert libc.prctl(37, ctypes.byref(previous), 0, 0, 0) == 0
    assert libc.prctl(36, 1, 0, 0, 0) == 0
    _, transport, _, audit, processes = stream_runtime
    try:
        yield
    finally:
        try:
            # Fixture watchdog is 8s; joining ensures the child has been adopted.
            for thread in tuple(transport._dispatch_threads.values()):
                thread.join(10)
                assert not thread.is_alive()
            child_audit = audit.with_name("child-audit.json")
            if child_audit.exists():
                child = json.loads(child_audit.read_text())
                assert child["parent_pid"] in {p.pid for p in processes}
                pid = child["pid"]
                try:
                    waited, _ = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    # Parent may have reaped it before adoption.
                    with pytest.raises(ProcessLookupError):
                        os.kill(pid, 0)
                else:
                    if waited == 0:
                        os.kill(pid, signal.SIGKILL)
                        deadline = time.monotonic() + 3
                        while waited == 0 and time.monotonic() < deadline:
                            waited, _ = os.waitpid(pid, os.WNOHANG)
                            if waited == 0:
                                time.sleep(0.01)
                        assert waited == pid, "owned child not reaped"
                    with pytest.raises(ProcessLookupError):
                        os.kill(pid, 0)
        finally:
            assert libc.prctl(36, previous.value, 0, 0, 0) == 0


def _run_turn(runtime, transport, role, audit, scenario: str, attempt: str):
    context = DispatchContext(
        trace_id="TRACE-STREAM",
        run_id="RUN-STREAM",
        task_id="TASK-STREAM",
        role_name=role.name,
        instance_id=role.instance_id,
        backend="claude-code",
        briefing_path=runtime.project_root / "briefing.md",
        dispatch_id=f"dispatch-{attempt}",
        operation_id="operation-stream",
        attempt_id=attempt,
        lease_id=f"lease-{attempt}",
        task_pipeline_stage="impl",
        operation_generation=1,
        task_map_generation="map-stream",
        workspace_generation=1,
        placement_epoch=1,
        task_stage_session_binding="fixture-context-only",
    )
    # Full context is Given, not a claim that this test admitted a Task Pipeline operation.
    before = {e.id for e in runtime.event_log.read_all()}
    transport.send_task(
        role.instance_id, context.briefing_path, scenario, context=context
    )
    with transport._dispatch_lock:
        thread = transport._dispatch_threads.get(role.instance_id)
    if thread is not None:
        assert isinstance(thread, threading.Thread)
        thread.join(10)
        assert not thread.is_alive(), "stream worker exceeded 10s deadline"
    observed = json.loads(audit.read_text())
    assert observed["scenario"] == scenario
    assert observed["cwd"] == str(runtime.project_root)
    with pytest.raises(ProcessLookupError):
        os.kill(observed["pid"], 0)
    sid = str(transport.registry.get(role.instance_id))
    assert observed["session_id"] == sid
    with SessionLock(transport.lock_dir, sid):
        pass  # Exception and EOF paths must release the real session mutex.
    runtime._drain_transport_events()
    events = [e for e in runtime.event_log.read_all() if e.id not in before]
    for event in events:
        if event.type.startswith("agent."):
            assert event.task_id == context.task_id
            assert event.correlation_id == context.trace_id
            for key, value in context.to_payload().items():
                assert event.payload[key] == value
    assert runtime.task_store.get("TASK-STREAM").status == "in_progress"
    assert not any(
        e.type in {"task.done", "run.goal.completed", "candidate.ready"} for e in events
    )
    recorded_ids = [e.id for e in runtime.event_log.read_all()]
    runtime._drain_transport_events()
    assert [e.id for e in runtime.event_log.read_all()] == recorded_ids
    return events, observed


@pytest.mark.parametrize("action", ["terminate", "shutdown", "kernel-cancel"])
@pytest.mark.parametrize("scenario", ["await-release", "spawn-child-ignore-term"])
def test_stop_cancels_active_sdk_subprocess_and_releases_worker(
    stream_runtime, reap_stream_child, action, scenario, record_property
):
    runtime, transport, role, audit, processes = stream_runtime
    transport._timeout_s = 30
    transport.send_task(
        role.instance_id,
        runtime.project_root / "briefing.md",
        scenario,
        context=DispatchContext(
            trace_id="TRACE-STOP",
            run_id="RUN-STOP",
            task_id="TASK-STREAM",
            role_name=role.name,
            instance_id=role.instance_id,
            backend="claude-code",
            briefing_path=runtime.project_root / "briefing.md",
            dispatch_id=f"dispatch-{action}",
            operation_id="operation-stop",
            attempt_id=f"attempt-{action}",
        ),
    )
    with transport._dispatch_lock:
        thread = transport._dispatch_threads[role.instance_id]
    try:
        for _ in range(150):
            if audit.with_suffix(".ready").exists() and processes:
                break
            time.sleep(0.02)
        assert audit.with_suffix(".ready").exists() and processes, "CLI not ready"
        [process] = processes
        observed = json.loads(audit.read_text())
        sid = str(transport.registry.get(role.instance_id))
        assert observed["pid"] == process.pid
        assert observed["session_id"] == sid
        assert observed["cwd"] == str(runtime.project_root)
        assert process.returncode is None and thread.is_alive()
        os.kill(process.pid, 0)
        with pytest.raises(SessionLockBusy), SessionLock(transport.lock_dir, sid):
            pass
        if action == "terminate":
            transport.terminate(role.instance_id)
        elif action == "kernel-cancel":
            decision = runtime._cancel_instance(role)
            assert decision.action == "cancel"
            assert runtime._last_worker_state[role.instance_id] == "blocked_human"
        else:
            transport.shutdown()
        # Allow asynchronous cancellation to settle, then capture facts before
        # the test releases the provider. The decisive witness is a late success,
        # not merely failure to meet an invented sub-second shutdown SLA.
        thread.join(0.5)
        stopped = {
            "process_returncode": process.returncode,
            "thread_alive": thread.is_alive(),
            "reported_alive": transport.is_alive(role.instance_id),
        }
        if scenario == "spawn-child-ignore-term":
            child = json.loads(audit.with_name("child-audit.json").read_text())
            assert child["parent_pgid"] == child["child_pgid"] == process.pid
            waited, status = os.waitpid(child["pid"], os.WNOHANG)
            assert waited == child["pid"], "owned child still running after stop"
            assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
            record_property("stopped_child", json.dumps({**child, "wait_status": status}))
        try:
            with SessionLock(transport.lock_dir, sid):
                stopped["lock_busy"] = False
        except SessionLockBusy:
            stopped["lock_busy"] = True
    finally:
        # This releases only our offline fixture, not a provider or canonical
        # runtime state. Never present test cleanup as production cancellation.
        audit.with_suffix(".release").touch()
        thread.join(10)
        assert not thread.is_alive(), "owned stream worker did not finish"
    assert process.returncode is not None
    assert process.returncode != 42, "offline release watchdog expired"
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)
    with SessionLock(transport.lock_dir, sid):
        pass
    runtime._drain_transport_events()
    events = [
        e
        for e in runtime.event_log.read_all()
        if e.payload.get("dispatch_id") == f"dispatch-{action}"
    ]
    assert all(e.task_id == "TASK-STREAM" for e in events)
    assert all(e.correlation_id == "TRACE-STOP" for e in events)
    assert runtime.task_store.get("TASK-STREAM").status == "in_progress"
    assert not any(
        e.type in {"task.done", "candidate.ready", "run.goal.completed"}
        for e in runtime.event_log.read_all()
    )
    ids = [e.id for e in runtime.event_log.read_all()]
    runtime._drain_transport_events()
    assert [e.id for e in runtime.event_log.read_all()] == ids
    diagnostics = [
        row
        for row in read_runtime_logs(runtime.state_dir)
        if row.get("component") == "stream-json"
    ]
    late_success = any(
        e.type == "agent.usage" and e.payload.get("is_error") is False for e in events
    )
    revived = transport.is_alive(role.instance_id)
    record_property(
        "stop_observation",
        json.dumps(
            {
                "action": action,
                "process": observed,
                "before_release": stopped,
                "exit_code": process.returncode,
                "alive_after_release": revived,
                "late_success": late_success,
                "runtime_logs": diagnostics,
                "events": [{"type": e.type, "payload": e.payload} for e in events],
            }
        ),
    )
    if (
        process.returncode == 0
        or late_success
        or revived
        or any(r["status"] == "completed" for r in diagnostics)
    ):
        raise StreamShutdownTruthBug(
            f"{action}: before_release={stopped}, exit={process.returncode}, "
            f"late_success={late_success}, revived={revived}"
        )
    assert stopped == {
        "process_returncode": -signal.SIGTERM,
        "thread_alive": False,
        "reported_alive": False,
        "lock_busy": False,
    }
    [blocked] = [event for event in events if event.type == "agent.api_blocked"]
    assert blocked.payload["drain_status"] == "cancelled"
    assert blocked.payload["provider_stop_reason"] == "manual_interrupt"
    assert {row["status"] for row in diagnostics} == {"failed"}
    # A fresh prepared dispatch, not the cancelled old result, may restore health.
    transport.init()
    transport.spawn(role, ["env", f"ZF_STREAM_AUDIT={audit}"], cwd=runtime.project_root)
    retried, retry_process = _run_turn(
        runtime, transport, role, audit, "success", f"attempt-after-{action}",
    )
    assert retry_process["pid"] != observed["pid"]
    assert retry_process["session_id"] == observed["session_id"]
    assert [p.returncode for p in processes] == [-signal.SIGTERM, 0]
    assert transport.is_alive(role.instance_id)
    assert [e.type for e in retried if e.type == "agent.usage"] == ["agent.usage"]
    assert not any(e.type == "agent.api_blocked" for e in retried)
    record_property("stop_prepared_retry", json.dumps(retry_process))


@pytest.mark.parametrize("scenario", ["hang-silent", "hang-partial"])
def test_sdk_timeout_reaps_process_and_allows_explicit_retry(
    stream_runtime, scenario, record_property
):
    runtime, transport, role, audit, processes = stream_runtime
    transport._timeout_s = 1.0
    timed_out, first = _run_turn(
        runtime, transport, role, audit, scenario, "attempt-timeout"
    )
    # A naturally expired fixture exits 42, never -SIGTERM: this checks actual
    # SDK cancellation, not a self-exiting process or fixture teardown kill.
    assert [p.returncode for p in processes] == [-signal.SIGTERM]
    [timeout] = [event for event in timed_out if event.type == "agent.timeout"]
    assert timeout.payload["timeout_s"] == 1.0
    assert timeout.payload["partial_messages"] == (
        0 if scenario == "hang-silent" else 2
    )
    assert not any(event.type == "agent.usage" for event in timed_out)
    assert not transport.is_alive(role.instance_id)
    transport._timeout_s = 5
    succeeded, second = _run_turn(
        runtime, transport, role, audit, "success", "attempt-retry"
    )
    assert first["session_id"] == second["session_id"]
    assert first["pid"] != second["pid"]
    assert not first["resume"] and not second["resume"]
    assert [p.returncode for p in processes] == [-signal.SIGTERM, 0]
    [usage] = [event for event in succeeded if event.type == "agent.usage"]
    assert usage.payload["is_error"] is False
    assert not any(
        event.type in {"agent.timeout", "agent.api_blocked"} for event in succeeded
    )
    assert transport.is_alive(role.instance_id)
    diagnostics = [
        row
        for row in read_runtime_logs(runtime.state_dir)
        if row.get("component") == "stream-json"
    ]
    assert {row["attempt_id"]: row["status"] for row in diagnostics} == {
        "attempt-timeout": "failed",
        "attempt-retry": "completed",
    }
    assert len(diagnostics) == 2
    record_property(
        "timeout_retry",
        json.dumps(
            {
                "processes": [first, second],
                "returncodes": [p.returncode for p in processes],
                "timeout": timeout.payload,
                "runtime_logs": diagnostics,
            }
        ),
    )


@pytest.mark.parametrize("scenario", ["rate-limit-before", "rate-limit-after"])
def test_real_sdk_rate_limit_preserves_before_and_after_assistant_contract(
    stream_runtime, scenario, record_property
):
    runtime, transport, role, audit, processes = stream_runtime
    events, observed = _run_turn(
        runtime, transport, role, audit, scenario, f"attempt-{scenario}"
    )
    [process] = processes
    # The SDK parser raises on the unknown rate_limit_event and its finally
    # closes the CLI transport with SIGTERM; this is distinct from the
    # provider's semantic rate-limit status handled by ZaoFu.
    assert process.returncode == -signal.SIGTERM
    [diagnostic] = [
        row
        for row in read_runtime_logs(runtime.state_dir)
        if row.get("component") == "stream-json"
    ]
    types = [event.type for event in events]
    if scenario == "rate-limit-before":
        assert "agent.api_blocked" in types
        assert "agent.text" not in types
        assert transport.is_alive(role.instance_id) is False
        assert diagnostic["status"] == "failed"
        blocked = next(event for event in events if event.type == "agent.api_blocked")
        assert blocked.payload["provider_stop_reason"] == "rate_limited"
    else:
        assert "agent.text" in types
        assert "agent.api_blocked" in types
        assert transport.is_alive(role.instance_id) is False
        assert diagnostic["status"] == "failed"
    assert runtime.task_store.get("TASK-STREAM").status == "in_progress"
    assert not any(
        event.type in {"task.done", "candidate.ready", "run.goal.completed"}
        for event in events
    )
    record_property(
        "rate_limit_observation",
        json.dumps(
            {
                "scenario": scenario,
                "process": observed,
                "exit_code": process.returncode,
                "events": [{"type": e.type, "payload": e.payload} for e in events],
                "runtime_log": diagnostic,
                "alive": transport.is_alive(role.instance_id),
            }
        ),
    )


@pytest.mark.parametrize(
    "scenario", ["spawn-child", "spawn-child-ignore-term", "spawn-child-success"]
)
def test_sdk_timeout_reaps_owned_process_tree(
    stream_runtime, reap_stream_child, record_property, scenario
):
    runtime, transport, role, audit, processes = stream_runtime
    transport._timeout_s = 1.0
    events, observed = _run_turn(
        runtime, transport, role, audit, scenario, f"attempt-{scenario}"
    )
    child_audit = audit.with_name("child-audit.json")
    assert child_audit.exists()
    child = json.loads(child_audit.read_text())
    child_pid = child["pid"]
    assert child["parent_pid"] == observed["pid"]
    assert child["parent_pgid"] == child["child_pgid"]
    assert child["parent_pgid"] == observed["pid"]
    assert child["parent_pgid"] != os.getpgrp()
    # /proc distinguishes a running/sleeping child from an already-dead zombie;
    # cleanup then waitpid() reaps only this validated, adopted descendant.
    status_path = Path(f"/proc/{child_pid}/status")
    status = status_path.read_text() if status_path.exists() else ""
    child_alive = bool(status) and "State:\tZ" not in status
    [parent] = processes
    if scenario == "spawn-child-success":
        assert parent.returncode == 0
        assert transport.is_alive(role.instance_id)
        assert not any(event.type == "agent.timeout" for event in events)
    else:
        assert parent.returncode == -signal.SIGTERM
        assert not transport.is_alive(role.instance_id)
        assert len([event for event in events if event.type == "agent.timeout"]) == 1
    record_property(
        "process_tree_observation",
        json.dumps(
            {
                "parent": observed,
                "parent_exit": parent.returncode,
                "child_pid": child_pid,
                "child": child,
                "child_status": status,
                "child_alive_after_parent_stop": child_alive,
                "scenario": scenario,
                "events": [{"type": event.type, "payload": event.payload} for event in events],
            }
        ),
    )
    if child_alive:
        raise StreamProcessTreeBug(
            f"parent exit={parent.returncode}, child pid={child_pid} still alive"
        )
    waited, status = os.waitpid(child_pid, os.WNOHANG)
    assert waited == child_pid
    expected_signal = signal.SIGKILL if scenario == "spawn-child-ignore-term" else signal.SIGTERM
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == expected_signal
    record_property("child_wait_status", status)


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "exit-41",
        "truncated",
        "no-result",
        "error-result",
    ],
)
def test_sdk_subprocess_requires_complete_successful_result(
    stream_runtime, scenario, record_property
):
    runtime, transport, role, audit, processes = stream_runtime
    events, observed = _run_turn(runtime, transport, role, audit, scenario, "attempt-1")
    assert len(processes) == 1
    assert processes[0].returncode == (41 if scenario == "exit-41" else 0)
    [diagnostic] = [
        row
        for row in read_runtime_logs(runtime.state_dir)
        if row.get("component") == "stream-json"
    ]
    assert diagnostic["attempt_id"] == "attempt-1"
    record_property(
        "stream_observation",
        json.dumps(
            {
                "process": observed,
                "events": [{"type": e.type, "payload": e.payload} for e in events],
                "alive": transport.is_alive(role.instance_id),
                "exit_code": processes[0].returncode,
                "runtime_log": diagnostic,
            }
        ),
    )
    if scenario == "success":
        [usage] = [e for e in events if e.type == "agent.usage"]
        assert usage.payload["is_error"] is False
        assert transport.is_alive(role.instance_id) is True
        assert diagnostic["status"] == "completed"
    else:
        if transport.is_alive(role.instance_id) or diagnostic["status"] != "failed":
            raise StreamCompletionTruthBug(
                f"{scenario}: alive={transport.is_alive(role.instance_id)}, "
                f"runtime status={diagnostic['status']} for incomplete/failed result"
            )
        assert any(e.type in {"agent.api_blocked", "agent.timeout"} for e in events), (
            scenario
        )
        if scenario != "exit-41":
            [blocked] = [e for e in events if e.type == "agent.api_blocked"]
            assert blocked.payload["drain_status"] == (
                "result_error" if scenario == "error-result" else "incomplete_result"
            )
            assert blocked.payload["provider_stop_reason"] == "transport_error"


@pytest.mark.parametrize("scenario", ["exit-41", "truncated", "no-result", "error-result"])
def test_sdk_process_failure_then_explicit_retry_releases_lock_and_keeps_identity(
    stream_runtime, scenario,
):
    runtime, transport, role, audit, processes = stream_runtime
    failed, first = _run_turn(runtime, transport, role, audit, scenario, "attempt-1")
    [blocked] = [e for e in failed if e.type == "agent.api_blocked"]
    if scenario == "exit-41":
        assert "41" in blocked.payload["transport_error"]
    else:
        assert blocked.payload["provider_stop_reason"] == "transport_error"
    assert not transport.is_alive(role.instance_id)
    assert transport.poll_events() == []
    succeeded, second = _run_turn(
        runtime, transport, role, audit, "success", "attempt-2"
    )
    assert first["session_id"] == second["session_id"]
    assert first["pid"] != second["pid"]
    assert [process.returncode for process in processes] == [
        41 if scenario == "exit-41" else 0, 0,
    ]
    assert (
        not first["resume"] and not second["resume"]
    )  # Fixture does not create transcripts.
    assert transport.is_alive(role.instance_id)
    assert not any(e.type == "agent.api_blocked" for e in succeeded)
    assert sum(e.type == "agent.usage" for e in succeeded) == 1
