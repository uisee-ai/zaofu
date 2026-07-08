"""U1(缝上传值/顺序绑定):集成前 task ref 与最新有效完成对齐。

r6.1 断点续跑实弹:集成读滞后的 ref 索引 → candidate 永远慢一拍 →
空 diff 3 轮 + 慢一拍 2 轮(含触发停机的第 12 轮伪拒)。修法:集成前
以事件流最新 worker 完成事件同步驱动 TaskRefManager(复用全部校验)。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.candidates import CandidateRebuilder, CandidateTask


@pytest.fixture
def env(tmp_path: Path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "events.jsonl").write_text("", encoding="utf-8")
    log = EventLog(state_dir / "events.jsonl")
    rebuilder = CandidateRebuilder(
        state_dir=state_dir,
        project_root=tmp_path,
        config=SimpleNamespace(),  # sync helper 不触配置;TaskRefManager 被 monkeypatch
        event_log=log,
    )
    return state_dir, log, rebuilder


def _task(commit: str = "old0000000") -> CandidateTask:
    return CandidateTask(
        task_id="T-1",
        task_ref="zf/task/T-1",
        source_commit=commit,
        approval_event_id="evt-a",
        approval_event_type="task.ref.updated",
    )


def _completion(commit: str, *, actor: str = "dev-metrics") -> ZfEvent:
    return ZfEvent(
        type="dev.build.done",
        actor=actor,
        task_id="T-1",
        payload={"source_commit": commit, "source_branch": "zf/worker/dev"},
    )


class _Manager:
    calls: list[ZfEvent] = []
    result_status = "updated"

    def __init__(self, **kwargs):
        pass

    def process_dev_build_done(self, event):
        _Manager.calls.append(event)
        if _Manager.result_status == "updated":
            return SimpleNamespace(
                status="updated",
                payload={"source_commit": event.payload["source_commit"],
                         "task_id": event.task_id},
            )
        return SimpleNamespace(status="rejected", payload={"reason": "dirty"})


@pytest.fixture(autouse=True)
def patch_manager(monkeypatch):
    _Manager.calls = []
    _Manager.result_status = "updated"
    import zf.runtime.task_refs as task_refs

    monkeypatch.setattr(task_refs, "TaskRefManager", _Manager)
    yield


def test_newer_worker_completion_syncs_ref_and_source_commit(env):
    state_dir, log, rebuilder = env
    log.append(_completion("new1111111"))
    out = rebuilder._sync_tasks_with_latest_completions(
        [_task()], event_writer=EventWriter(log),
    )
    assert out[0].source_commit == "new1111111"
    assert len(_Manager.calls) == 1
    updated = [e for e in log.read_all() if e.type == "task.ref.updated"]
    assert len(updated) == 1
    assert updated[0].payload["source"] == "candidate_integration_sync"


def test_kernel_echo_events_are_ignored(env):
    # r6.1 实弹:kernel 回声(actor=zf-cli)携带 manifest 旧值,不得驱动同步
    state_dir, log, rebuilder = env
    log.append(_completion("new1111111"))
    log.append(_completion("stale00000", actor="zf-cli"))  # 回声更晚到
    out = rebuilder._sync_tasks_with_latest_completions([_task()], event_writer=None)
    assert out[0].source_commit == "new1111111"
    assert _Manager.calls[0].payload["source_commit"] == "new1111111"


def test_rejected_handoff_keeps_index_value(env):
    # 校验失败(脏工作区等)不绕过:维持索引值,交由 review 兜底
    state_dir, log, rebuilder = env
    _Manager.result_status = "rejected"
    log.append(_completion("new1111111"))
    out = rebuilder._sync_tasks_with_latest_completions([_task()], event_writer=None)
    assert out[0].source_commit == "old0000000"


def test_no_completion_or_same_commit_is_noop(env):
    state_dir, log, rebuilder = env
    out = rebuilder._sync_tasks_with_latest_completions([_task()], event_writer=None)
    assert out[0].source_commit == "old0000000"
    assert not _Manager.calls
    log.append(_completion("old0000000"))
    out = rebuilder._sync_tasks_with_latest_completions([_task()], event_writer=None)
    assert out[0].source_commit == "old0000000"
    assert not _Manager.calls
