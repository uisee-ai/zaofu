"""FIX-15②③(bizsim r4):判审收敛门。

r4 实锚:judge 五审里 2 审开在修复落地前(同 commit 必败审),第 5 审自述
workdir 停基线却无人收到。修复:同 commit 已驳回 → 抑制重开审;3 连驳 →
human.escalate(judge_nonconvergence)带驳回链;有 delta 时仍放行新审。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from zf.core.config.schema import (
    FanoutAggregateConfig,
    ProjectConfig,
    RoleConfig,
    WorkflowConfig,
    WorkflowStageConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.orchestrator import Orchestrator


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
        ["git", *args], cwd=root, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _init_repo(root: Path) -> str:
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("v1\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "initial")
    _git(root, "branch", "-M", "main")
    return _git(root, "rev-parse", "HEAD")


def _config() -> ZfConfig:
    return ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="judge", backend="mock", role_kind="reader")],
        workflow=WorkflowConfig(stages=[
            WorkflowStageConfig(
                id="final-judge",
                trigger="test.passed",
                topology="fanout_reader",
                roles=["judge"],
                target_ref="${target_ref}",
                retrigger_requires_delta=True,
                aggregate=FanoutAggregateConfig(
                    mode="wait_for_all",
                    success_event="judge.passed",
                    failure_event="judge.failed",
                ),
            ),
        ]),
    )


def _state(tmp_path: Path):
    head = _init_repo(tmp_path)
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(
        state_dir, _config(), _RecordingTransport(),  # type: ignore[arg-type]
        project_root=tmp_path,
    )
    return head, state_dir, log, orch


def _seed_failed_audit(log: EventLog, *, fanout_id: str, commit: str, reason: str) -> None:
    """按真实事件形状预置一轮已驳回的判审(started → dispatched(pinned) → failed)。"""
    writer = EventWriter(log)
    writer.append(ZfEvent(
        type="fanout.started", actor="zf-cli",
        payload={"fanout_id": fanout_id, "stage_id": "final-judge"},
    ))
    writer.append(ZfEvent(
        type="fanout.child.dispatched", actor="zf-cli",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "final-judge",
            "child_id": "judge-x",
            "payload": {"target_commit": commit},
        },
    ))
    writer.append(ZfEvent(
        type="judge.failed", actor="zf-cli",
        payload={"fanout_id": fanout_id, "status": "failed", "reason": reason},
    ))


def _trigger(orch, log, *, target_ref: str) -> None:
    event = ZfEvent(
        type="test.passed", actor="zf-cli", correlation_id="trace-1",
        payload={"status": "completed", "target_ref": target_ref},
    )
    EventWriter(log).append(event)
    orch.run_once(events=[event])


def test_same_commit_after_failure_is_suppressed(tmp_path: Path) -> None:
    head, _, log, orch = _state(tmp_path)
    _seed_failed_audit(log, fanout_id="fanout-final-judge-old1", commit=head, reason="gate failed")

    _trigger(orch, log, target_ref="main")

    events = log.read_all()
    suppressed = [e for e in events if e.type == "fanout.retrigger.suppressed"]
    assert suppressed, "同 commit 已驳回必须抑制重开审"
    assert suppressed[-1].payload["reason"] == "no_delta_since_failure"
    assert suppressed[-1].payload["target_commit"] == head
    assert not [
        e for e in events
        if e.type == "fanout.started" and e.payload.get("stage_id") == "final-judge"
        and e.payload.get("fanout_id") != "fanout-final-judge-old1"
    ]


def test_new_commit_after_failure_is_allowed(tmp_path: Path) -> None:
    head, _, log, orch = _state(tmp_path)
    _seed_failed_audit(log, fanout_id="fanout-final-judge-old1", commit=head, reason="gate failed")
    (tmp_path / "fix.txt").write_text("fix\n", encoding="utf-8")
    _git(tmp_path, "add", "fix.txt")
    _git(tmp_path, "commit", "-m", "fix")

    _trigger(orch, log, target_ref="main")

    events = log.read_all()
    assert not [e for e in events if e.type == "fanout.retrigger.suppressed"]
    fresh = [
        e for e in events
        if e.type == "fanout.started" and e.payload.get("stage_id") == "final-judge"
        and e.payload.get("fanout_id") != "fanout-final-judge-old1"
    ]
    assert fresh, "有 delta 必须放行新审"


def test_three_consecutive_failures_escalate_owner(tmp_path: Path) -> None:
    head, _, log, orch = _state(tmp_path)
    for i in range(3):
        _seed_failed_audit(
            log, fanout_id=f"fanout-final-judge-old{i}", commit=head,
            reason=f"failure {i}",
        )

    _trigger(orch, log, target_ref="main")

    events = log.read_all()
    escalates = [
        e for e in events
        if e.type == "human.escalate"
        and e.payload.get("reason") == "judge_nonconvergence"
    ]
    assert escalates, "3 连驳必须升级 owner"
    payload = escalates[-1].payload
    assert payload["failure_count"] == 3
    assert len(payload["failure_chain"]) == 3
    assert payload["failure_chain"][-1]["reason"] == "failure 2"

    # 幂等:同 count 再触发不重复升级
    _trigger(orch, log, target_ref="main")
    escalates2 = [
        e for e in log.read_all()
        if e.type == "human.escalate"
        and e.payload.get("reason") == "judge_nonconvergence"
    ]
    assert len(escalates2) == 1
