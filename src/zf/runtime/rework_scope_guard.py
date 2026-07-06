"""Rework 路由与 findings 文件归属的错位检测(avbs-r4 F1-D2)。

r3 活锁第一因:固定路由 `review.rejected: dev-scene` 把每轮 rework 派给
一个 allowed_paths 不含 finding 文件的任务——scope guard 保证了"修不了"
是硬性的,而路由层对此毫无感知,活锁静默持续 2.5 小时。

这里只做纯判定:trigger findings 里带 path 的条目若**全部**落在任务
scope 之外,返回告警 payload;有任何一条可修、或无 findings/无 scope,
返回 None。行为不变,只加可观察性(D2 切片;D1 findings-ownership
定向路由待 r5 验证 same_lane 收敛效果后裁决)。
"""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.task_refs import _path_allowed_by_scope


def rework_scope_mismatch(task: Task, trigger_event: ZfEvent) -> dict[str, Any] | None:
    payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
    findings = payload.get("findings")
    if not isinstance(findings, list):
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        findings = report.get("findings")
    if not isinstance(findings, list):
        return None
    paths = [
        str(item.get("path")).strip()
        for item in findings
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    ]
    if not paths:
        return None
    scope = list(getattr(task.contract, "scope", None) or []) if task.contract else []
    if not scope:
        return None
    if any(_path_allowed_by_scope(path, scope) for path in paths):
        return None
    return {
        "task_id": task.id,
        "finding_paths": paths[:10],
        "task_scope": scope[:10],
        "reason": (
            "rework target task's scope covers none of the finding paths; "
            "the assigned worker cannot fix these findings (r3 livelock "
            "signature). Route rework to the owner task of the finding "
            "paths, or amend the task_map."
        ),
    }
