import json
import os
import time
from pathlib import Path

from zf.core.config.schema import RoleConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.session_tailer import CodexSessionTailer
from zf.runtime.spawn_coordinator import SpawnCoordinator
from tests.test_codex_session_tailer import _wait_for
from tests.test_spawn_coordinator import _RecordingTransport


def test_restarted_observer_waits_for_current_launch_rollout(tmp_path: Path, monkeypatch):
    state_dir = tmp_path / "custom-state"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    role = RoleConfig(name="dev", backend="codex")
    registry = RoleSessionRegistry(state_dir / "role_sessions.yaml", project_root=str(tmp_path))
    tailer = CodexSessionTailer(log)
    coordinator = SpawnCoordinator(state_dir=state_dir, registry=registry,
        transport=_RecordingTransport(), project_root=str(tmp_path),
        event_log=log, codex_session_tailer=tailer)
    folder = coordinator._codex_sessions_root(role) / "2026/10/26"
    folder.mkdir(parents=True)

    def rollout(suffix, text):
        sid = f"77777777-7777-7777-7777-{suffix:012d}"
        path = folder / f"rollout-2026-10-26T00-00-00-{sid}.jsonl"
        rows = [
            {"type": "session_meta", "payload": {"id": sid, "cwd": str(tmp_path)}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": text}]}},
            {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": text}},
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return path

    old = rollout(1, "retired history")
    os.utime(old, (time.time() - 60, time.time() - 60))
    log.append(ZfEvent(type="worker.launch_artifact.written", actor="zf-cli",
                      payload={"instance_id": "dev", "backend": "codex", "is_resume": False}))
    log.append(ZfEvent(type="worker.launch_artifact.written", actor="zf-cli",
                      payload={"instance_id": "other", "backend": "codex"},
                      ts="2099-01-01T00:00:00+00:00"))
    select = registry._glob_newest_since
    scans = []

    def scan(since_ts, **kwargs):
        selected = select(since_ts, **kwargs)
        scans.append(since_ts)
        if len(scans) == 1:
            rollout(2, "fresh completed turn")
        return selected

    monkeypatch.setattr(registry, "_glob_newest_since", scan)
    coordinator.notify_first_dispatch(role)
    try:
        assert _wait_for(lambda: any(e.type == "provider.turn.closed"
            and e.payload.get("turn_id") == "fresh completed turn" for e in log.read_all()))
        assert [e.payload["text"] for e in log.read_all() if e.type == "agent.text"] == ["fresh completed turn"]
        assert str(registry.get("dev")).endswith("000000000002")
        assert scans[0] > old.stat().st_mtime
    finally:
        tailer.stop()
