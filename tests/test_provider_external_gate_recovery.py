"""A live external provider gate must not become a destructive stuck retry."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from zf.core.state.role_sessions import RoleSessionRegistry
from tests.test_native_compaction_recycle import worker  # noqa: F401


QUOTA = "■ You’ve hit your usage limit. Try again later."


def gate(worker, monkeypatch):
    runtime, transport, _, recycled = worker
    monkeypatch.setattr(transport, "is_alive", lambda *a: True)
    monkeypatch.setattr(transport, "capture_log", lambda *a, **k: QUOTA)
    return runtime, transport, recycled


@pytest.mark.parametrize("entry", ["pane", "heartbeat"])
def test_external_gate_keeps_task_and_session_on_both_watchdogs(worker, monkeypatch, entry):
    runtime, _, recycled = gate(worker, monkeypatch)
    before = runtime.task_store.get("T1")
    if entry == "pane":
        runtime._stuck_detectors["dev"] = SimpleNamespace(
            update=lambda _: None, is_stuck=lambda: True, reset=lambda: None,
        )
        runtime._capture_logs()
    else:
        reg = RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml",
                                  project_root=str(runtime.project_root))
        reg.record_heartbeat("dev", {"state": "busy", "current_task_id": "T1"})
        reg._meta["dev"]["last_heartbeat_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        reg._save()
        runtime._run_heartbeat_sweep()
    assert runtime.task_store.get("T1") == before
    assert recycled == []
    assert not any(e.type in {"worker.stuck", "task.requeued", "worker.respawned"}
                   for e in runtime.event_log.read_all())


def test_quota_wait_does_not_reset_hard_cap_and_releases_after_prompt_clears(worker, monkeypatch):
    runtime, transport, recycled = gate(worker, monkeypatch)
    runtime._now = lambda: 200
    runtime._check_context_thresholds()
    assert recycled == []
    assert "dev" in runtime._hard_cap_exceeded
    monkeypatch.setattr(transport, "capture_log", lambda *a, **k: "◦ Working (1m • esc to interrupt)")
    runtime._check_context_thresholds()
    assert recycled == ["dev"]


def test_idle_drain_waits_for_external_prompt_without_state_churn(worker, monkeypatch):
    runtime, transport, recycled = gate(worker, monkeypatch)
    runtime.task_store.update("T1", status="done")
    runtime._check_pending_recycles()
    assert recycled == []
    assert runtime._instance_state["dev"] == "pending_recycle"
    monkeypatch.setattr(transport, "capture_log", lambda *a, **k: "ready")
    runtime._check_pending_recycles()
    assert recycled == ["dev"]


def test_restart_orphan_rescue_does_not_replace_a_live_quota_gate(worker, monkeypatch):
    runtime, _, recycled = gate(worker, monkeypatch)
    runtime._instance_state.clear()
    reg = RoleSessionRegistry(runtime.state_dir / "role_sessions.yaml",
                              project_root=str(runtime.project_root))
    reg.record_heartbeat("dev", {"state": "pending_recycle", "current_task_id": "T1"})
    reg._meta["dev"]["last_heartbeat_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat()
    reg._save()
    runtime._rescue_orphan_pending_recycles()
    assert recycled == []


@pytest.mark.parametrize("fault", ["dead", "unreadable", "ordinary"])
def test_external_guard_does_not_disable_real_recovery(worker, monkeypatch, fault):
    runtime, transport, _ = gate(worker, monkeypatch)
    if fault == "dead":
        monkeypatch.setattr(transport, "is_alive", lambda *a: False)
    elif fault == "unreadable":
        def fail(*a, **k):
            raise OSError("pane unavailable")
        monkeypatch.setattr(transport, "capture_log", fail)
    else:
        monkeypatch.setattr(transport, "capture_log", lambda *a, **k: "frozen output")
    runtime._report_stuck_worker(runtime.config.roles[0])
    assert any(e.type == "worker.stuck" for e in runtime.event_log.read_all())
