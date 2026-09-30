"""Fault observer proves an active response before any signal; no provider runs."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conformance.test_zaofu_testkit_s3_blocking_dispatch import blocking_runtime  # noqa: F401
from tests.testkit import native_pipeline_recovery as recovery
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore


@pytest.mark.host
@pytest.mark.serial
def test_pidfd_libc_fallback_kills_only_owned_child(monkeypatch):
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    monkeypatch.delattr(signal, "pidfd_send_signal", raising=False)
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"]) as child:
        try:
            recovery._kill_owned_provider(child.pid)
            assert child.wait(timeout=3) == -signal.SIGKILL
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)


@pytest.mark.host
@pytest.mark.serial
def test_pidfd_stop_confirms_owned_child_is_stopped(monkeypatch):
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    monkeypatch.delattr(signal, "pidfd_send_signal", raising=False)
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"]) as child:
        try:
            recovery._stop_owned_provider(child.pid)
            assert child.poll() is None
            status = Path(f"/proc/{child.pid}/status").read_text()
            assert next(line.split()[1] for line in status.splitlines()
                        if line.startswith("State:")) == "T"
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)


def test_fault_requires_assistant_and_is_injected_only_once(blocking_runtime, monkeypatch):
    runtime, transport, _ = blocking_runtime
    runtime._dispatch_ready()
    context = transport.contexts[0]
    events = runtime.event_log.read_all()
    registry = RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", str(runtime.project_root))
    sid = registry.task_stage_bindings()[context.task_stage_session_binding]["session_id"]
    transcript = runtime.project_root / "fake-transcript.jsonl"
    transcript.write_text('{"incomplete":', encoding="utf-8")
    monkeypatch.setattr(recovery, "claude_session_path", lambda *_: transcript)
    killed = []
    monkeypatch.setattr(recovery, "_owned_claude_pid", lambda *_: 12345)
    monkeypatch.setattr(recovery, "_kill_owned_provider", killed.append)
    tmux = SimpleNamespace(pane_process_probe=lambda *a, **kw: {
        "pane_pid": "11111", "processes": [{"pid": "12345"}],
    })
    properties = {}
    observer = recovery.NativePipelineRecovery(runtime, tmux, "impl", properties.__setitem__)
    observer.poll(transport.contexts, events)
    assert killed == [] and observer.old is None
    transcript.write_text(json.dumps({"type": "assistant", "sessionId": sid}) + "\n{", encoding="utf-8")
    observer.poll(transport.contexts, events)
    # A buffered pre-kill usage heartbeat must not restore a 900-second wait.
    store = TaskAttemptStore(runtime.state_dir / "task_attempts.json")
    store.renew_lease(context.attempt_id, updated_at="2026-09-23T00:00:00+00:00",
                      lease_expires_at="2099-01-01T00:00:00+00:00")
    observer.poll(transport.contexts, events)
    assert killed == [12345]
    assert observer.old == context
    attempt = TaskAttemptStore(runtime.state_dir / "task_attempts.json").get(context.attempt_id)
    assert attempt["status"] == "sent"  # The observer never authors recovery status/events.
    assert attempt["lease_expires_at"] == "2000-01-01T00:00:00+00:00"
    assert [e.id for e in runtime.event_log.read_all()] == [e.id for e in events]
    assert json.loads(properties["native_interruption"])["assistant_count"] == 1
    store.update(context.attempt_id, status="superseded", updated_at="2026-09-23T00:00:00+00:00")
    terminal = store.get(context.attempt_id)
    observer.poll(transport.contexts, events)
    assert store.get(context.attempt_id) == terminal
    assert killed == [12345]


def test_fault_refuses_a_settled_operation(blocking_runtime, monkeypatch):
    runtime, transport, _ = blocking_runtime
    runtime._dispatch_ready()
    context = transport.contexts[0]
    registry = RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml", str(runtime.project_root))
    sid = registry.task_stage_bindings()[context.task_stage_session_binding]["session_id"]
    transcript = runtime.project_root / "fake-transcript.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "sessionId": sid}), encoding="utf-8")
    monkeypatch.setattr(recovery, "claude_session_path", lambda *_: transcript)
    monkeypatch.setattr(recovery, "_kill_owned_provider", lambda *_: pytest.fail("unsafe kill"))
    observer = recovery.NativePipelineRecovery(runtime, None, "impl", lambda *_: None)
    events = [*runtime.event_log.read_all(), ZfEvent(
        type="workflow.operation.settled", payload={"operation_id": context.operation_id},
    )]
    with pytest.raises(AssertionError):
        observer.poll(transport.contexts, events)
