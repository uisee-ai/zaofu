"""avbs-r4 F12: escalate 节流 / cap 记账去重 / rescan checkpoint 坍缩。

r4 终局:'rework cap exceeded' escalate 以 8-10 秒/发刷 21 条迫使停机;
任务级 rework cap 4/3 全部来自 echo 重放记账;重启 rescan 对同 pdd
4 连发 resume batch 靠 supersede 兜底。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.escalation import EscalationManager
from zf.runtime.housekeeping import apply_rework_failure_event
from zf.runtime.workflow_resume import WorkflowBatchResumeCheckpoint
from zf.runtime.workflow_resume_apply import _collapse_batch_checkpoints


def _manager(tmp_path: Path) -> EscalationManager:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    return EscalationManager(state_dir)


def test_escalate_throttles_same_signature_within_window(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.escalate("task T-1: rework cap (4/3) exceeded", task_id="T-1")
    # 数字归一化:5/3 与 4/3 是同一件事在刷屏
    mgr.escalate("task T-1: rework cap (5/3) exceeded", task_id="T-1")
    mgr.escalate("task T-1: rework cap (4/3) exceeded", task_id="T-1")
    escalates = [e for e in mgr.event_log.read_all() if e.type == "human.escalate"]
    assert len(escalates) == 1


def test_escalate_allows_different_task_or_reason(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.escalate("task T-1: rework cap (4/3) exceeded", task_id="T-1")
    mgr.escalate("task T-2: rework cap (4/3) exceeded", task_id="T-2")
    mgr.escalate("candidate rework exhausted; findings unresolved", task_id="T-1")
    escalates = [e for e in mgr.event_log.read_all() if e.type == "human.escalate"]
    assert len(escalates) == 3


def test_escalate_fires_again_after_window(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    stale_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=999)
    ).isoformat()
    mgr.event_log.append(ZfEvent(
        type="human.escalate", actor="orchestrator", task_id="T-1",
        ts=stale_ts, payload={"reason": "task T-1: rework cap (4/3) exceeded"},
    ))
    mgr.escalate("task T-1: rework cap (4/3) exceeded", task_id="T-1")
    escalates = [e for e in mgr.event_log.read_all() if e.type == "human.escalate"]
    assert len(escalates) == 2


def _seed_task(tmp_path: Path) -> TaskStore:
    store = TaskStore(tmp_path / "kanban.json")
    store.add(Task(id="T-1", title="T-1", status="in_progress",
                   contract=TaskContract(feature_id="F-1")))
    return store


def _failure(fanout_id: str, eid: str) -> ZfEvent:
    return ZfEvent(
        type="review.rejected", id=eid, task_id="T-1",
        payload={"fanout_id": fanout_id, "status": "failed"},
    )


def test_rework_bump_dedupes_same_fanout_replay(tmp_path: Path) -> None:
    store = _seed_task(tmp_path)
    first = _failure("fanout-a", "e1")
    replay = _failure("fanout-a", "e2")
    window = [first, replay]
    apply_rework_failure_event(store, first, events=window)
    apply_rework_failure_event(store, replay, events=window)
    assert store.get("T-1").retry_count == 1


def test_rework_bump_counts_distinct_fanouts(tmp_path: Path) -> None:
    store = _seed_task(tmp_path)
    a = _failure("fanout-a", "e1")
    b = _failure("fanout-b", "e2")
    window = [a, b]
    apply_rework_failure_event(store, a, events=window)
    apply_rework_failure_event(store, b, events=window)
    assert store.get("T-1").retry_count == 2


def test_rework_bump_without_window_keeps_legacy_behavior(tmp_path: Path) -> None:
    store = _seed_task(tmp_path)
    apply_rework_failure_event(store, _failure("fanout-a", "e1"))
    apply_rework_failure_event(store, _failure("fanout-a", "e2"))
    assert store.get("T-1").retry_count == 2


def _cp(cp_id: str, pdd: str, action: str = "repair_failed_children"):
    return WorkflowBatchResumeCheckpoint(
        checkpoint_id=cp_id, source_event_id=cp_id, source_event_type="review.rejected",
        blocking_event_id=cp_id, safe_resume_action=action, pdd_id=pdd,
    )


def test_collapse_keeps_latest_per_pdd_action() -> None:
    cps = [_cp("c1", "PDD-A"), _cp("c2", "PDD-A"), _cp("c3", "PDD-B"), _cp("c4", "PDD-A")]
    kept, collapsed = _collapse_batch_checkpoints(cps)
    assert [c.checkpoint_id for c in kept] == ["c3", "c4"]
    assert [c.checkpoint_id for c in collapsed] == ["c1", "c2"]


def test_collapse_keeps_distinct_actions() -> None:
    cps = [_cp("c1", "PDD-A", "repair_failed_children"), _cp("c2", "PDD-A", "reissue_candidate_ready")]
    kept, collapsed = _collapse_batch_checkpoints(cps)
    assert len(kept) == 2 and not collapsed


def test_resolve_emits_escalated_acked(tmp_path: Path) -> None:
    # P1-11(审计 D5):S3 no-dead-end 的 ack 事件此前全仓无发射器。
    mgr = _manager(tmp_path)
    mgr.escalate("task T-1: blocked", task_id="T-1")
    mgr.resolve("已处理:改契约后重派")
    types = [e.type for e in mgr.event_log.read_all()]
    assert "human.resolved" in types
    assert "remediation.escalated_acked" in types
