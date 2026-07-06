"""r6-F6:pending_recycle 孤儿救援(回收任务重启即丢,r6 卡 40 分钟实弹)。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


def _write_heartbeat(state_dir: Path, instance: str, state: str, ts: str) -> None:
    (state_dir / "role_sessions.yaml").write_text(yaml.safe_dump({
        "instance_meta": {
            instance: {
                "backend": "codex",
                "last_heartbeat_at": ts,
                "last_heartbeat_payload": {"state": state, "instance_id": instance},
            },
        },
    }), encoding="utf-8")


def test_orphan_pending_recycle_rescued(state_dir: Path, tmp_path: Path) -> None:
    _write_heartbeat(state_dir, "dev-1", "pending_recycle", "2020-01-01T00:00:00+00:00")
    config = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", instance_id="dev-1", backend="mock")],
    )
    orch = Orchestrator(state_dir, config, TmuxTransport(TmuxSession(session_name="t", dry_run=True)))
    orch._rescue_orphan_pending_recycles()
    events = EventLog(state_dir / "events.jsonl").read_all()
    changed = [e for e in events if e.type == "worker.state.changed"
               and (e.payload or {}).get("to") == "recycling"]
    assert changed, "孤儿 pending_recycle 应被强制进入 recycling"
    assert "rescued" in str(changed[-1].payload.get("reason"))


def test_fresh_pending_recycle_not_rescued(state_dir: Path) -> None:
    from datetime import datetime, timezone
    _write_heartbeat(state_dir, "dev-1", "pending_recycle",
                     datetime.now(timezone.utc).isoformat())
    config = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", instance_id="dev-1", backend="mock")],
    )
    orch = Orchestrator(state_dir, config, TmuxTransport(TmuxSession(session_name="t", dry_run=True)))
    orch._rescue_orphan_pending_recycles()
    events = EventLog(state_dir / "events.jsonl").read_all()
    assert not [e for e in events if e.type == "worker.state.changed"]
