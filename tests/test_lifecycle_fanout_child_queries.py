"""_active_fanout_child_for_instance:fanout 级终局必须使 child 失效。

ZF-STOP-TAIL-01 邻居(07-16 实弹):被 supersede 取消的 fanout 其 child
被当 active,respawn recovery 反复给死 child 重注简报,worker 完成申报
在 flow 层永远无人承接(任务真相 review / 流程真相无此工序)。
"""
from __future__ import annotations

from pathlib import Path
import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.fanout_evidence_queries import FanoutEvidenceQueriesMixin
from zf.runtime.lifecycle_evidence_queries import LifecycleEvidenceQueriesMixin


class _Host(LifecycleEvidenceQueriesMixin, FanoutEvidenceQueriesMixin):
    def __init__(self, path: Path) -> None:
        self.event_log = EventLog(path)


@pytest.mark.parametrize('lost_run,expected', [('dispatch-old', None), ('', 'dispatch-old')])
def test_lost_dispatch_releases_only_its_execution(
    tmp_path: Path, lost_run: str, expected: str | None,
) -> None:
    host = _Host(tmp_path / 'events.jsonl')
    host.event_log.append(_dispatched('fanout-1', 'reader', 'verify', run_id='dispatch-old'))
    host.event_log.append(ZfEvent(
        type='fanout.child.dispatch_lost', actor='zf-cli', payload={
            'fanout_id': 'fanout-1', 'child_id': 'reader', 'run_id': lost_run,
            'role_instance': 'verify', 'reason': 'suspended_operation_replaced_by_controlled_rerun',
        },
    ))
    child = host._active_fanout_child_for_instance('verify')
    assert (child['run_id'] if child else None) == expected


def test_late_old_dispatch_loss_does_not_release_fresh_reader(tmp_path: Path) -> None:
    host = _Host(tmp_path / 'events.jsonl')
    for run_id in ('dispatch-old', 'dispatch-new'):
        host.event_log.append(_dispatched('fanout-1', 'reader', 'verify', run_id=run_id))
    host.event_log.append(ZfEvent(
        type='fanout.child.dispatch_lost', actor='zf-cli', payload={
            'fanout_id': 'fanout-1', 'child_id': 'reader', 'run_id': 'dispatch-old',
            'role_instance': 'verify',
        },
    ))
    assert host._active_fanout_child_for_instance('verify')['run_id'] == 'dispatch-new'


def _dispatched(
    fanout_id: str,
    child_id: str,
    instance: str,
    *,
    workflow_run_id: str = "",
    run_id: str | None = None,
    task_id: str = "",
) -> ZfEvent:
    payload = {
        "fanout_id": fanout_id,
        "child_id": child_id,
        "run_id": run_id or f"run-{fanout_id}-{child_id}",
        "role_instance": instance,
        "task_id": task_id,
    }
    if workflow_run_id:
        payload["workflow_run_id"] = workflow_run_id
    if task_id:
        payload["task_id"] = task_id
    return ZfEvent(
        type="fanout.child.dispatched",
        actor="zf-cli",
        task_id=task_id or None,
        payload=payload,
        correlation_id=workflow_run_id or None,
    )


def test_child_of_cancelled_fanout_is_not_active(tmp_path: Path) -> None:
    host = _Host(tmp_path / "events.jsonl")
    host.event_log.append(_dispatched("fanout-impl-1", "queued-T2-2", "dev-lane-0"))
    host.event_log.append(ZfEvent(
        type="fanout.cancelled", actor="zf-cli",
        payload={"fanout_id": "fanout-impl-1", "reason": "superseded_by_latest_fanout"},
    ))
    assert host._active_fanout_child_for_instance("dev-lane-0") is None


def test_child_of_live_fanout_stays_active(tmp_path: Path) -> None:
    host = _Host(tmp_path / "events.jsonl")
    host.event_log.append(_dispatched("fanout-impl-1", "queued-T2-2", "dev-lane-0"))
    # 另一个 fanout 被取消不影响本 child
    host.event_log.append(ZfEvent(
        type="fanout.cancelled", actor="zf-cli",
        payload={"fanout_id": "fanout-impl-OTHER"},
    ))
    child = host._active_fanout_child_for_instance("dev-lane-0")
    assert child is not None
    assert child["child_id"] == "queued-T2-2"


def test_timed_out_pending_children_are_terminal(tmp_path: Path) -> None:
    # 此前 fanout.timed_out 有处理分支但从未被扫描命中(标记过滤先行)
    host = _Host(tmp_path / "events.jsonl")
    host.event_log.append(_dispatched("fanout-impl-1", "queued-T2-2", "dev-lane-0"))
    host.event_log.append(ZfEvent(
        type="fanout.timed_out", actor="zf-cli",
        payload={"fanout_id": "fanout-impl-1", "pending_children": ["queued-T2-2"]},
    ))
    assert host._active_fanout_child_for_instance("dev-lane-0") is None


def test_terminal_workflow_run_invalidates_active_child_and_recovery(
    tmp_path: Path,
) -> None:
    host = _Host(tmp_path / "events.jsonl")
    run_id = "workflow-real-1"
    host.event_log.append(ZfEvent(
        type="run.goal.started",
        actor="zf-cli",
        payload={"run_id": run_id},
        correlation_id=run_id,
    ))
    host.event_log.append(_dispatched(
        "fanout-scan-1",
        "scan-runtime",
        "scan-runtime",
        workflow_run_id=run_id,
        task_id="TASK-1",
    ))
    blocked = ZfEvent(
        type="run.goal.blocked",
        actor="zf-cli",
        task_id="TASK-1",
        payload={"run_id": run_id, "workflow_run_id": run_id},
        correlation_id=run_id,
    )
    host.event_log.append(blocked)

    assert host._active_fanout_child_for_instance("scan-runtime") is None
    assert host._fanout_task_state_for_instance(
        "scan-runtime",
        "TASK-1",
    ) == "terminal"
    assert host._fanout_identity_stale_reason("fanout-scan-1") == (
        "workflow_run_terminal:run.goal.blocked",
        blocked.id,
    )


def test_active_goal_update_does_not_reopen_preblocked_fanout_child(
    tmp_path: Path,
) -> None:
    host = _Host(tmp_path / "events.jsonl")
    run_id = "workflow-resumed-1"
    host.event_log.append(ZfEvent(
        type="run.goal.started",
        actor="zf-cli",
        payload={"run_id": run_id},
        correlation_id=run_id,
    ))
    host.event_log.append(_dispatched(
        "fanout-verify-1",
        "verify-lane-1",
        "verify-lane-1",
        workflow_run_id=run_id,
        task_id="TASK-1",
    ))
    host.event_log.append(ZfEvent(
        type="run.goal.blocked",
        actor="run-manager",
        task_id="TASK-1",
        payload={"run_id": run_id, "workflow_run_id": run_id},
        correlation_id=run_id,
    ))
    host.event_log.append(ZfEvent(
        type="run.goal.updated",
        actor="operator",
        task_id="TASK-1",
        payload={
            "run_id": run_id,
            "workflow_run_id": run_id,
            "status": "active",
        },
        correlation_id=run_id,
    ))

    assert host._active_fanout_child_for_instance("verify-lane-1") is None
    assert host._fanout_task_state_for_instance(
        "verify-lane-1",
        "TASK-1",
    ) == "terminal"
    assert host._fanout_identity_stale_reason("fanout-verify-1") == ("", "")


def test_terminal_for_other_run_does_not_invalidate_live_child(
    tmp_path: Path,
) -> None:
    host = _Host(tmp_path / "events.jsonl")
    for run_id in ("workflow-live", "workflow-blocked"):
        host.event_log.append(ZfEvent(
            type="run.goal.started",
            actor="zf-cli",
            payload={"run_id": run_id},
            correlation_id=run_id,
        ))
    host.event_log.append(_dispatched(
        "fanout-live",
        "scan-runtime",
        "scan-runtime",
        workflow_run_id="workflow-live",
    ))
    host.event_log.append(ZfEvent(
        type="run.goal.blocked",
        actor="zf-cli",
        payload={"run_id": "workflow-blocked"},
        correlation_id="workflow-blocked",
    ))

    child = host._active_fanout_child_for_instance("scan-runtime")
    assert child is not None
    assert child["fanout_id"] == "fanout-live"
    assert host._fanout_identity_stale_reason("fanout-live") == ("", "")


def test_blocked_run_reopen_keeps_only_post_reopen_child_active(
    tmp_path: Path,
) -> None:
    """R4: a legal goal reopen creates a new lifecycle epoch.

    The pre-block child stays terminal, while a child dispatched after the
    canonical ``run.goal.updated(status=active)`` must remain recoverable by
    watchdog respawn.
    """
    host = _Host(tmp_path / "events.jsonl")
    run_id = "workflow-resumed"
    host.event_log.append(ZfEvent(
        type="run.goal.started",
        actor="zf-cli",
        payload={"run_id": run_id},
        correlation_id=run_id,
    ))
    host.event_log.append(_dispatched(
        "fanout-before-block",
        "old-child",
        "dev-lane-0",
        workflow_run_id=run_id,
        task_id="TASK-OLD",
    ))
    host.event_log.append(ZfEvent(
        type="run.goal.blocked",
        actor="zf-cli",
        payload={"run_id": run_id, "workflow_run_id": run_id},
        correlation_id=run_id,
    ))
    host.event_log.append(ZfEvent(
        type="run.goal.updated",
        actor="operator",
        payload={
            "run_id": run_id,
            "workflow_run_id": run_id,
            "status": "active",
        },
        correlation_id=run_id,
    ))
    host.event_log.append(_dispatched(
        "fanout-after-reopen",
        "new-child",
        "dev-lane-0",
        workflow_run_id=run_id,
        task_id="TASK-NEW",
    ))

    child = host._active_fanout_child_for_instance("dev-lane-0")
    assert child is not None
    assert child["fanout_id"] == "fanout-after-reopen"
    assert host._fanout_task_state_for_instance(
        "dev-lane-0",
        "TASK-OLD",
    ) == "terminal"
    assert host._fanout_task_state_for_instance(
        "dev-lane-0",
        "TASK-NEW",
    ) == "active"


def test_hard_terminal_cannot_be_reopened_by_goal_update(tmp_path: Path) -> None:
    host = _Host(tmp_path / "events.jsonl")
    run_id = "workflow-complete"
    host.event_log.append(ZfEvent(
        type="run.goal.started",
        actor="zf-cli",
        payload={"run_id": run_id},
        correlation_id=run_id,
    ))
    host.event_log.append(ZfEvent(
        type="run.completed",
        actor="zf-cli",
        payload={"run_id": run_id, "workflow_run_id": run_id},
        correlation_id=run_id,
    ))
    host.event_log.append(ZfEvent(
        type="run.goal.updated",
        actor="operator",
        payload={
            "run_id": run_id,
            "workflow_run_id": run_id,
            "status": "active",
        },
        correlation_id=run_id,
    ))
    host.event_log.append(_dispatched(
        "fanout-after-complete",
        "new-child",
        "dev-lane-0",
        workflow_run_id=run_id,
        task_id="TASK-NEW",
    ))

    assert host._active_fanout_child_for_instance("dev-lane-0") is None
    assert host._fanout_task_state_for_instance(
        "dev-lane-0",
        "TASK-NEW",
    ) == "terminal"


def test_stale_completion_terminates_exact_superseded_child(tmp_path: Path) -> None:
    host = _Host(tmp_path / "events.jsonl")
    host.event_log.append(_dispatched(
        "fanout-impl-old",
        "dev-lane-1-T1",
        "dev-lane-1",
        run_id="run-old",
        task_id="T1",
    ))
    host.event_log.append(ZfEvent(
        type="fanout.child.stale_completion",
        actor="zf-cli",
        task_id="T1",
        payload={
            "fanout_id": "fanout-impl-old",
            "child_id": "dev-lane-1-T1",
            "run_id": "run-old",
            "role_instance": "dev-lane-1",
            "task_id": "T1",
            "reason": "superseded_by_latest_fanout",
        },
    ))

    assert host._active_fanout_child_for_instance("dev-lane-1") is None
    assert host._fanout_task_state_for_instance("dev-lane-1", "T1") == "terminal"


def test_late_stale_completion_does_not_terminate_newer_run(tmp_path: Path) -> None:
    host = _Host(tmp_path / "events.jsonl")
    host.event_log.append(_dispatched(
        "fanout-impl",
        "dev-lane-1-T1",
        "dev-lane-1",
        run_id="run-old",
        task_id="T1",
    ))
    host.event_log.append(_dispatched(
        "fanout-impl",
        "dev-lane-1-T1",
        "dev-lane-1",
        run_id="run-new",
        task_id="T1",
    ))
    host.event_log.append(ZfEvent(
        type="fanout.child.stale_completion",
        actor="zf-cli",
        task_id="T1",
        payload={
            "fanout_id": "fanout-impl",
            "child_id": "dev-lane-1-T1",
            "run_id": "run-old",
            "role_instance": "dev-lane-1",
            "task_id": "T1",
            "reason": "superseded_by_latest_fanout",
        },
    ))

    child = host._active_fanout_child_for_instance("dev-lane-1")
    assert child is not None
    assert child["run_id"] == "run-new"
    assert host._fanout_task_state_for_instance("dev-lane-1", "T1") == "active"
