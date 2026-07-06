"""r6-F7:完成事件诚实核验(虚报产物/头不符,幻觉完成家族)。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from zf.runtime.completion_honesty import unverified_completion_claims


def _git_workdir(tmp_path: Path) -> Path:
    wd = tmp_path / "wd"
    wd.mkdir()
    subprocess.run(["git", "-C", str(wd), "init", "-q"], check=True)
    (wd / "a.txt").write_text("x")
    subprocess.run(["git", "-C", str(wd), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(wd), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], check=True)
    return wd


def test_missing_claimed_artifact_flagged(tmp_path: Path) -> None:
    wd = _git_workdir(tmp_path)
    problems = unverified_completion_claims({
        "workdir": str(wd),
        "evidence_refs": ["docs/validation/P2-medium-scene.png", "a.txt"],
    })
    assert len(problems) == 1
    assert "P2-medium-scene.png" in problems[0]


def test_existing_artifacts_pass(tmp_path: Path) -> None:
    wd = _git_workdir(tmp_path)
    assert unverified_completion_claims({
        "workdir": str(wd), "evidence_refs": ["a.txt"],
    }) == []


def test_head_claim_mismatch_flagged(tmp_path: Path) -> None:
    wd = _git_workdir(tmp_path)
    problems = unverified_completion_claims({
        "workdir": str(wd),
        "head_commit": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    })
    assert problems and "head_commit" in problems[0]


def test_head_claim_match_passes(tmp_path: Path) -> None:
    wd = _git_workdir(tmp_path)
    head = subprocess.run(["git", "-C", str(wd), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert unverified_completion_claims({
        "workdir": str(wd), "head_commit": head,
    }) == []


def test_nonlocal_refs_skipped(tmp_path: Path) -> None:
    assert unverified_completion_claims({
        "workdir": str(tmp_path),
        "evidence_refs": ["git:abc123", "https://x/y", "/abs/path", ".zf/artifacts/x"],
    }) == []


def test_reactor_emits_unverified_event(tmp_path: Path) -> None:
    """接线核验:dev.build.done 带虚报 → dev.completion.claims_unverified。"""
    from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent
    from zf.core.state.session import SessionStore
    from zf.core.task.schema import Task
    from zf.core.task.store import TaskStore
    from zf.runtime.orchestrator import Orchestrator
    from zf.runtime.tmux import TmuxSession
    from zf.runtime.transport import TmuxTransport

    sd = tmp_path / ".zf"
    sd.mkdir(); (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(ZfEvent(type="loop.started", actor="zf-cli"))
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    store = TaskStore(sd / "kanban.json")
    store.add(Task(id="T1", title="x", status="in_progress", assigned_to="dev"))
    wd = _git_workdir(tmp_path)
    log = EventLog(sd / "events.jsonl")
    log.append(ZfEvent(type="dev.build.done", actor="dev", task_id="T1", payload={
        "status": "completed", "workdir": str(wd),
        "evidence_refs": ["docs/validation/missing.png"],
    }))
    config = ZfConfig(project=ProjectConfig(name="t"),
                      session=SessionConfig(tmux_session="t"),
                      roles=[RoleConfig(name="dev", backend="mock")])
    Orchestrator(sd, config, TmuxTransport(TmuxSession(session_name="t", dry_run=True))).run_once()
    types = [e.type for e in log.read_all()]
    assert "dev.completion.claims_unverified" in types
