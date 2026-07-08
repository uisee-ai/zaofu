"""FIX-9/15①(bizsim r4 F9):reader 审计对象 pin-commit 与 fail-closed 派发。

r4 judge 五审实锚:invoke 路径 target_ref 渲染为空 → checkout 静默跳过 →
judge workdir 停基线,审了一棵无交付的树。修复后:声明了 target 的 stage
渲染为空/checkout 失败一律拒派发并留 fanout.child.workdir_mismatch。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    RuntimeConfig,
    WorkdirConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.workdirs import WorkdirManager


class _RecordingTransport:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        self.sent.append((role_name, briefing_path, prompt, context))

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> str:
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("test\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    _git(root, "branch", "-M", "main")
    return _git(root, "rev-parse", "HEAD")


def _config(*, workdirs: bool, target_ref: str = "${target_ref}") -> ZfConfig:
    runtime = (
        RuntimeConfig(workdirs=WorkdirConfig(enabled=True, mode="worktree"))
        if workdirs
        else RuntimeConfig()
    )
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="judge", backend="mock", role_kind="reader")],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="final-judge",
                trigger="test.passed",
                topology="fanout_reader",
                roles=["judge"],
                target_ref=target_ref,
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="judge.passed",
                    failure_event="judge.failed",
                ),
            ),
        ]),
        runtime=runtime,
    )


def _state(tmp_path: Path, *, workdirs: bool):
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    transport = _RecordingTransport()
    orch = Orchestrator(
        state_dir,
        _config(workdirs=workdirs),
        transport,  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    return head, state_dir, log, transport, orch


def _trigger(orch, log, *, target_ref: str | None) -> None:
    payload: dict = {"status": "completed"}
    if target_ref is not None:
        payload["target_ref"] = target_ref
    orch.run_once(events=[ZfEvent(
        type="test.passed",
        actor="zf-cli",
        correlation_id="trace-1",
        payload=payload,
    )])


def test_unresolved_target_ref_rejects_dispatch(tmp_path: Path) -> None:
    _, _, log, transport, orch = _state(tmp_path, workdirs=False)

    _trigger(orch, log, target_ref=None)  # ${target_ref} 渲染为空

    events = log.read_all()
    mismatch = [e for e in events if e.type == "fanout.child.workdir_mismatch"]
    assert mismatch, "声明 target 的 stage 渲染为空必须拒派发"
    assert mismatch[-1].payload["reason"] == "target_ref_unresolved"
    failed = [e for e in events if e.type == "fanout.child.failed"]
    assert failed
    assert failed[-1].payload["failure_class"] == "reader_workdir_mismatch"
    assert not [e for e in events if e.type == "fanout.child.dispatched"]
    assert not transport.sent


def test_bad_ref_rejects_dispatch_fail_closed(tmp_path: Path) -> None:
    _, _, log, transport, orch = _state(tmp_path, workdirs=True)

    _trigger(orch, log, target_ref="no-such-branch")

    events = log.read_all()
    mismatch = [e for e in events if e.type == "fanout.child.workdir_mismatch"]
    assert mismatch, "checkout 失败必须拒派发而非静默降级旧 HEAD"
    assert mismatch[-1].payload["reason"].startswith("pin_failed")
    failed = [e for e in events if e.type == "fanout.child.failed"]
    assert failed
    assert failed[-1].payload["failure_class"] == "reader_workdir_mismatch"
    assert not [e for e in events if e.type == "fanout.child.dispatched"]


def test_project_document_path_target_dispatches_without_git_pin(tmp_path: Path) -> None:
    _, _, log, transport, orch = _state(tmp_path, workdirs=True)
    doc = tmp_path / "docs" / "prd" / "tiny.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Tiny\n", encoding="utf-8")

    _trigger(orch, log, target_ref="docs/prd/tiny.md")

    events = log.read_all()
    assert not [e for e in events if e.type == "fanout.child.workdir_mismatch"]
    dispatched = [e for e in events if e.type == "fanout.child.dispatched"]
    assert dispatched
    assert dispatched[-1].payload["target_ref"] == "docs/prd/tiny.md"
    assert transport.sent


def test_rework_payload_target_ref_overrides_profile_default_path(
    tmp_path: Path,
) -> None:
    _, _, log, transport, orch = _state(tmp_path, workdirs=True)
    orch.config.workflow.stages[0].target_ref = "docs/issues/TODO.md"
    doc = tmp_path / "docs" / "issues" / "fix-list.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Fix list\n", encoding="utf-8")

    orch.run_once(events=[ZfEvent(
        type="test.passed",
        actor="zf-cli",
        causation_id="evt-failed",
        correlation_id="trace-1",
        payload={
            "target_ref": "docs/issues/fix-list.md",
            "rework_of": "evt-failed",
        },
    )])

    events = log.read_all()
    assert not [e for e in events if e.type == "fanout.child.workdir_mismatch"]
    dispatched = [e for e in events if e.type == "fanout.child.dispatched"]
    assert dispatched
    assert dispatched[-1].payload["target_ref"] == "docs/issues/fix-list.md"
    assert transport.sent


def test_valid_ref_pins_commit_into_child_payload(tmp_path: Path) -> None:
    head, state_dir, log, transport, orch = _state(tmp_path, workdirs=True)

    _trigger(orch, log, target_ref="main")

    events = log.read_all()
    dispatched = [e for e in events if e.type == "fanout.child.dispatched"]
    assert dispatched, "合法 ref 必须正常派发"
    child_payload = dispatched[-1].payload.get("payload") or {}
    assert child_payload.get("target_commit") == head
    project_path = state_dir / "workdirs" / "judge" / "project"
    assert _git(project_path, "rev-parse", "HEAD") == head


def test_pin_reader_target_verifies_head(tmp_path: Path) -> None:
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = _config(workdirs=True)
    manager = WorkdirManager(
        state_dir=state_dir, project_root=tmp_path, config=config,
    )

    pinned = manager.pin_reader_target(config.roles[0], "main")

    assert pinned == head
