from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_types import OrchestratorDecision


class _RecordingTransport:
    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        return None

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _state(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", instance_id="dev")],
    )
    orch = Orchestrator(state_dir, config, _RecordingTransport())  # type: ignore[arg-type]
    return state_dir, log, orch


def _request(request_id: str = "req-1") -> ZfEvent:
    return ZfEvent(
        id=request_id,
        type="worker.respawn.requested",
        actor="operator",
        correlation_id="trace-1",
        payload={"instance_id": "dev", "reason": "operator"},
    )


def test_worker_respawn_request_replay_is_applied_once(tmp_path: Path):
    _state_dir, log, orch = _state(tmp_path)
    req = _request()
    log.append(req)
    calls: list[str] = []

    def _respawn(role):  # noqa: ANN001
        calls.append(role.instance_id)
        return OrchestratorDecision(
            action="respawn",
            role=role.instance_id,
            reason="respawned",
        )

    orch._respawn_instance = _respawn  # type: ignore[method-assign]

    orch.run_once(events=[])
    orch.run_once(events=[])

    assert calls == ["dev"]
    events = log.read_all()
    assert [event.type for event in events].count("worker.respawn.completed") == 1


def test_worker_respawn_request_completed_marker_is_not_rebuilt(tmp_path: Path):
    state_dir, log, _orch = _state(tmp_path)
    req = _request()
    log.append(req)
    log.append(ZfEvent(
        type="worker.respawn.completed",
        actor="dev",
        causation_id=req.id,
        correlation_id=req.correlation_id,
        payload={"instance_id": "dev"},
    ))
    pending_path = state_dir / "actions" / "pending.json"
    if pending_path.exists():
        pending_path.unlink()
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock", instance_id="dev")],
    )
    orch = Orchestrator(state_dir, config, _RecordingTransport())  # type: ignore[arg-type]
    calls: list[str] = []
    orch._respawn_instance = lambda role: calls.append(role.instance_id)  # type: ignore[method-assign]

    orch.run_once(events=[])

    assert calls == []


def test_worker_respawn_request_permanent_failure_after_retry_cap(tmp_path: Path):
    _state_dir, log, orch = _state(tmp_path)
    store = orch._pending_actions_store()
    store.max_retries = 2
    req = _request()
    log.append(req)
    store.upsert_pending(
        request_id=req.id,
        type=req.type,
        instance_id="dev",
        payload=req.payload,
        correlation_id=req.correlation_id,
    )
    store.max_retries = 2

    def _raise(_role):  # noqa: ANN001
        raise RuntimeError("respawn failed")

    orch._respawn_instance = _raise  # type: ignore[method-assign]

    orch.run_once(events=[])
    orch.run_once(events=[])

    events = log.read_all()
    permanent = [
        event for event in events
        if event.type == "worker.action.permanently_failed"
    ]
    assert len(permanent) == 1
    assert permanent[0].causation_id == req.id
    assert permanent[0].payload["retries"] == 2
    assert "respawn failed" in permanent[0].payload["last_error"]
