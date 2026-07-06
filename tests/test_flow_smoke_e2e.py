"""E4(131-P4 最小版):三流运行时 smoke — kernel/workflow 改动后的固定回归。

r5 教训:r13 闲置看门狗上线未经三流回归直接进 5 小时长跑,首航 7 分钟
被击落。本文件 + test_controller_flow_smoke_matrix(inspection 级)
构成最小安全网;一条命令入口 scripts/run-flow-smoke.sh。

分层:refactor 流跑到 lane 全周期(dispatch→writer 完成→verify→
终局聚合);PRD 流跑 ingest→canonical 任务→首轮派发;issue 流为
inspection 级(与 PRD 共 common lane kernel,运行时形状由 refactor
smoke 覆盖)+ intake 合同断言。
"""

from __future__ import annotations

from pathlib import Path

from zf.core.task.store import TaskStore

from tests.test_lane_stage_streaming_runtime import (
    _child,
    _complete_verify,
    _complete_writer,
    _fanout_id,
    _manifest,
    _start,
    _state,
)


def test_refactor_lane_full_cycle_smoke(tmp_path: Path) -> None:
    """dispatch → writer done → per-lane verify → 终局 test.passed。"""
    state_dir, log, transport, orch = _state(tmp_path, task_count=1)
    _start(orch)
    impl_id = _fanout_id(log, "demo-impl")
    _complete_writer(
        orch,
        fanout_id=impl_id,
        child=_child(_manifest(state_dir, impl_id), "TASK-1"),
        task_id="TASK-1",
        file_name="task-1.txt",
    )
    verify_id = _fanout_id(log, "demo-verify")
    _complete_verify(orch, state_dir=state_dir, fanout_id=verify_id)

    events = [e.type for e in log.read_all()]
    assert "fanout.child.dispatched" in events
    assert "test.passed" in events, "终局聚合未达成"
    # E2 spine 投影在同一 run 上可解释(smoke 顺带验收 shadow spine)
    from zf.runtime.workflow_spine_projection import refresh_spine_projections

    stats = refresh_spine_projections(state_dir, log)
    assert stats["task_count"] >= 1 and stats["stage_count"] >= 1


def test_prd_flow_ingest_and_first_dispatch_smoke(tmp_path: Path) -> None:
    """PRD 侧:task_map ingest → canonical 任务 + 契约齐 → 可进派发。"""
    from zf.core.events.log import EventLog
    from zf.core.events.writer import EventWriter
    from zf.runtime.product_delivery import ingest_task_map_to_kanban

    from tests.test_product_delivery import _source_index, _task_map

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    result = ingest_task_map_to_kanban(
        state_dir,
        _task_map(),
        source_index=_source_index(),
        source_index_ref=".zf/artifacts/F-PROD/source-index.json",
        task_map_ref=".zf/artifacts/F-PROD/task-map.json",
        require_source_index=True,
        writer=writer,
        actor="zf-cli",
    )
    assert result.passed and result.created_task_ids == ["TASK-PROD-A", "TASK-PROD-B"]
    tasks = {t.id: t for t in TaskStore(state_dir / "kanban.json").list_all()}
    assert tasks["TASK-PROD-B"].blocked_by == ["TASK-PROD-A"]  # 依赖 gate 真相
    assert all(t.contract.verification for t in tasks.values())


def test_issue_flow_contract_smoke() -> None:
    """Issue 侧:profile 编译合同(运行时形状由 refactor smoke 代表)。"""
    from tests.test_controller_flow_smoke_matrix import _inspect

    report = _inspect("issue-fanout-v3.yaml")
    assert report["status"] in {"GO", "WARN"}
    assert report["generated"]["flow_metadata"]["flow_kind"] == "issue"
    # E3-2 后 quality_floor 的 judge 门首次可满足,合同仍应声明该 floor
    assert (
        report["generated"]["flow_metadata"].get("quality_floor")
        == "issue-regression"
    )
