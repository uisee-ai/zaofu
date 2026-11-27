"""Read-only relationships and the latest lifecycle milestone for the task reader."""

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.feature.store import FeatureStore
from zf.core.task.pipeline_event_scope import task_pipeline_event_scope
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


_MILESTONES = {
    "task.created": "创建任务", "task.dispatched": "已派发", "task.started": "开始执行",
    "task.blocked": "进入阻塞", "dev.blocked": "开发受阻", "task.requeued": "重新排队",
    "dev.build.done": "完成构建", "test.started": "开始测试", "verify.started": "开始验证",
    "test.passed": "测试通过", "test.failed": "测试失败",
    "verify.passed": "验证通过", "verify.failed": "验证失败",
    "review.approved": "评审通过", "review.rejected": "评审未通过",
    "judge.passed": "裁决通过", "judge.failed": "裁决未通过",
    "task.pipeline.verify.completed": "验证已结束", "task.pipeline.verify.failed": "验证失败",
    "task.pipeline.acceptance.completed": "验收评审已结束",
    "task.pipeline.acceptance.failed": "验收评审失败",
    "task.pipeline.integration.completed": "集成已结束", "task.done.accepted": "完成任务",
}
_TRANSITIONS = {
    "backlog": "进入待办", "ready": "进入待调度", "in_progress": "开始执行",
    "review": "进入评审", "testing": "进入测试", "blocked": "进入阻塞",
    "done": "完成任务", "cancelled": "取消任务", "archived": "归档任务",
}
_STAGES = {"impl": "实现", "verify": "验证", "acceptance_review": "验收评审", "integration": "集成"}


def admitted_output(event: ZfEvent) -> dict | None:
    if event.type != "workflow.call.result.admitted" or event.origin != "kernel":
        return None
    data = event.payload
    descriptor = data.get("control_result_ref")
    if (not event.ts or data.get("admission_status") != "admitted" or not isinstance(descriptor, dict)
            or not descriptor.get("ref") or not descriptor.get("sha256")):
        return None
    verdict = data.get("semantic_verdict")
    label = f"已接纳阶段结果 · {verdict}" if verdict else "已接纳阶段结果"
    return {"label": label, "at": event.ts, "event_id": event.id}


def latest_milestone(task: Task, events: list[tuple[int, ZfEvent]]) -> dict | None:
    scoped = task_pipeline_event_scope(task, (event for _, event in events if event.task_id == task.id))
    for event in reversed(scoped):
        if (task.active_dispatch_id and event.payload.get("dispatch_id")
                and event.payload["dispatch_id"] != task.active_dispatch_id):
            continue
        label = _MILESTONES.get(event.type)
        if event.type == "task.status_changed" and event.payload.get("from") != event.payload.get("to"):
            label = _TRANSITIONS.get(str(event.payload.get("to") or ""))
        elif event.type == "task.pipeline.stage.dispatched":
            stage = _STAGES.get(str(event.payload.get("task_pipeline_stage") or ""))
            label = f"已派发{stage}" if stage else "已派发"
        elif event.type == "workflow.call.result.admitted":
            if receipt := admitted_output(event):
                label = receipt["label"]
        if label and event.ts:
            return {"label": label, "at": event.ts}
    return None


def task_properties(state_dir: Path, task: Task, events: list[tuple[int, ZfEvent]]) -> dict:
    store = TaskStore(state_dir / "kanban.json")
    rows: dict[str, Task] = {}
    try:
        candidates = store.list_all_with_archive()
    except (OSError, TypeError, ValueError):
        candidates = store.list_all()
    for row in candidates:
        # TaskStore resolves reopened records and duplicate archive versions.
        rows[row.id] = (store.get(row.id) or row) if row.id in rows else row

    def reference(task_id: str) -> dict:
        row = rows.get(task_id)
        return {"id": task_id, "title": row.title if row else "", "status": row.status if row else ""}

    upstream = [reference(key) for key in dict.fromkeys(task.blocked_by) if key != task.id]
    downstream = []
    children = []
    for row in rows.values():
        if row.id == task.id:
            continue
        if task.id in row.blocked_by:
            # In TaskStore.ready a backlog dependency is satisfied only by done.
            downstream.append({**reference(row.id), "waiting": row.status == "backlog" and task.status != "done"})
        if row.contract.parent_task_id == task.id:
            children.append(reference(row.id))

    source = None
    for key in (task.contract.parent_task_id, task.contract.source_task_id, task.contract.source_backlog_task_id):
        if key in rows and key != task.id:
            source = {"kind": "task", **reference(key)}
            break
    if not source and task.contract.feature_id:
        try:
            feature = FeatureStore(state_dir / "feature_list.json").get(task.contract.feature_id)
        except (OSError, TypeError, ValueError):
            feature = None
        if feature:
            source = {"kind": "feature", "id": feature.id, "title": feature.title}
    return {
        "source": source, "upstream": upstream, "downstream": downstream,
        "children": children, "milestone": latest_milestone(task, events),
    }
