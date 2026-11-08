"""Heartbeat mistakes cannot overwrite another TaskAttempt's liveness."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.orchestrator import Orchestrator


class _NoopTransport:
    def terminate(self, role_name):
        pass

    def is_alive(self, role_name):
        return False


def _prepare(state: Path):
    return TaskAttemptStore(state / "task_attempts.json").ensure_for_dispatch(
        run_id="RUN-1", task_id="FMS-DB-READINESS-01", dispatch_id="dispatch-1",
        role="dev", instance_id="dev-lane-1", operation_id="operation-1",
        briefing_ref="briefings/dev.md", created_at="2026-11-08T19:39:07+00:00",
        lease_expires_at="2026-11-08T21:00:00+00:00", max_attempts=3,
    ).attempt


def _payload(task="FMS-DB-READINESS-01"):
    return {"instance_id": "dev-lane-1", "current_task_id": task,
            "state": "busy", "last_action_ts": "2026-11-08T19:57:10Z"}


def test_cli_rejects_misspelled_active_task(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: hb\n  state_dir: runtime-state\n')
    assert main(["init"]) == 0
    state = tmp_path / "runtime-state"
    _prepare(state)
    result = main(["emit", "worker.heartbeat", "--task", "FMS-READINESS-01",
                   "--actor", "dev-lane-1", "--payload", json.dumps(_payload("FMS-READINESS-01"))])
    assert result == 2
    assert "heartbeat" in capsys.readouterr().err.lower()
    assert not any(e.type == "worker.heartbeat" for e in EventLog(state / "events.jsonl").read_all())


@pytest.mark.parametrize("task,actor,delta", [
    ("FMS-READINESS-01", "dev-lane-1", {"current_task_id": "FMS-READINESS-01"}),
    (None, "dev-lane-1", {}),
    ("FMS-DB-READINESS-01", "dev-lane-1", {"current_task_id": "OTHER"}),
    ("FMS-DB-READINESS-01", "dev-lane-2", {}),
    ("FMS-DB-READINESS-01", "dev-lane-1", {"instance_id": "dev-lane-2"}),
    ("FMS-DB-READINESS-01", "dev-lane-1", {"attempt_id": "old-attempt"}),
    ("FMS-DB-READINESS-01", "dev-lane-1", {"dispatch_id": "old-dispatch"}),
    ("FMS-DB-READINESS-01", "dev-lane-1", {"operation_id": "old-operation"}),
    ("FMS-DB-READINESS-01", "dev-lane-1", {"lease_id": "old-lease"}),
    ("FMS-DB-READINESS-01", "dev-lane-1", {"workflow_run_id": "OLD-RUN"}),
    ("FMS-DB-READINESS-01", "dev-lane-1", {"run_id": "OLD-RUN"}),
])
def test_runtime_wrong_binding_cannot_replace_liveness(tmp_path, task, actor, delta):
    state = tmp_path / "runtime-state"
    state.mkdir()
    config = ZfConfig(project=ProjectConfig(name="hb", state_dir="runtime-state"),
                     roles=[RoleConfig(name="dev", instance_id="dev-lane-1", backend="mock")])
    runtime = Orchestrator(state, config, _NoopTransport())
    _prepare(state)
    reg = RoleSessionRegistry(state / "role_sessions.yaml", str(tmp_path))
    reg.record_heartbeat("dev-lane-1", _payload())
    before = reg.path.read_bytes()
    attempts_before = (state / "task_attempts.json").read_bytes()
    runtime._sweep_signal_last_emit_at[("dev-lane-1", "worker.stuck")] = 42
    event = ZfEvent(type="worker.heartbeat", actor=actor, task_id=task,
                    payload=_payload() | delta)
    runtime._apply_housekeeping(event)
    # Replayed observations keep one rejection and never extend liveness.
    runtime._apply_housekeeping(event)
    assert reg.path.read_bytes() == before
    assert (state / "task_attempts.json").read_bytes() == attempts_before
    assert runtime._sweep_signal_last_emit_at[("dev-lane-1", "worker.stuck")] == 42
    audits = [e for e in runtime.event_log.read_all() if e.type == "task.attempt.result_rejected"]
    assert len(audits) == 1 and audits[0].causation_id == event.id


def test_correct_cli_binding_renews_lease_and_liveness(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: hb\n  state_dir: runtime-state\n')
    assert main(["init"]) == 0
    state = tmp_path / "runtime-state"
    row = _prepare(state)
    result = main(["emit", "worker.heartbeat", "--task", "FMS-DB-READINESS-01",
                   "--actor", "dev-lane-1", "--payload", json.dumps(_payload())])
    assert result == 0
    event = next(e for e in EventLog(state / "events.jsonl").read_all() if e.type == "worker.heartbeat")
    assert event.payload["attempt_id"] == row["attempt_id"]
    assert event.payload["dispatch_id"] == row["dispatch_id"]
    config = ZfConfig(project=ProjectConfig(name="hb", state_dir="runtime-state"),
                     roles=[RoleConfig(name="dev", instance_id="dev-lane-1", backend="mock")])
    runtime = Orchestrator(state, config, _NoopTransport())
    runtime._apply_housekeeping(event)
    reg = RoleSessionRegistry(state / "role_sessions.yaml", str(tmp_path))
    assert reg.get_last_heartbeat("dev-lane-1")[1] == event.payload
    assert any(e.type == "task.attempt.heartbeat" for e in runtime.event_log.read_all())
    assert not any(e.type == "task.attempt.result_rejected" for e in runtime.event_log.read_all())


def test_unbound_legacy_role_heartbeat_still_works(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject:\n  name: hb\n  state_dir: runtime-state\n')
    assert main(["init"]) == 0
    state = tmp_path / "runtime-state"
    _prepare(state)
    assert main(["emit", "worker.heartbeat", "--actor", "legacy-worker",
                 "--payload", '{"state":"idle"}']) == 0
    event = next(e for e in EventLog(state / "events.jsonl").read_all() if e.type == "worker.heartbeat")
    runtime = Orchestrator(state, ZfConfig(project=ProjectConfig(name="hb")), _NoopTransport())
    runtime._apply_housekeeping(event)
    assert RoleSessionRegistry(state / "role_sessions.yaml", str(tmp_path)).get_last_heartbeat("legacy-worker")[1] == {"state": "idle"}
