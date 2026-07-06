"""131-P3-3: strict profile 下 impl 完成必须带 evidence refs,缺失发观测事件。"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig,
    RoleConfig,
    SessionConfig,
    WorkflowConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
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


def _config(profile: str) -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
        workflow=WorkflowConfig(harness_profile=profile),
    )


def _run_build_done(state_dir: Path, profile: str, payload: dict) -> list[ZfEvent]:
    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1", payload=payload))
    transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
    Orchestrator(state_dir, _config(profile), transport).run_once()
    return log.read_all()


def test_strict_profile_flags_missing_impl_evidence(state_dir: Path) -> None:
    events = _run_build_done(state_dir, "strict", {"status": "completed"})
    gaps = [e for e in events if e.type == "impl.evidence.missing"]
    assert len(gaps) == 1
    assert gaps[0].task_id == "T1"
    # 观测不阻塞:任务照常进 review
    assert TaskStore(state_dir / "kanban.json").get("T1").status == "review"


def test_strict_profile_accepts_evidence_refs(state_dir: Path) -> None:
    events = _run_build_done(
        state_dir, "strict",
        {"status": "completed", "evidence_refs": ["artifacts/test-report.json"]},
    )
    assert not [e for e in events if e.type == "impl.evidence.missing"]


def test_baseline_profile_does_not_flag(state_dir: Path) -> None:
    events = _run_build_done(state_dir, "baseline", {"status": "completed"})
    assert not [e for e in events if e.type == "impl.evidence.missing"]


def test_graph_bridge_path_still_flags(state_dir: Path, monkeypatch) -> None:
    """r6-F1:graph-bridge 早退不得绕过观测门。"""
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.orchestrator_reactor import OrchestratorDecision
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import TmuxTransport

    store = TaskStore(state_dir / "kanban.json")
    store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))
    log = EventLog(state_dir / "events.jsonl")
    log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1",
                       payload={"status": "completed"}))
    transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
    orch = Orchestrator(state_dir, _config("strict"), transport)
    monkeypatch.setattr(
        orch, "_workflow_graph_reconcile_bridge",
        lambda event: OrchestratorDecision(action="move", task_id="T1", reason="graph"),
    )
    orch.run_once()
    assert "impl.evidence.missing" in [e.type for e in log.read_all()]
