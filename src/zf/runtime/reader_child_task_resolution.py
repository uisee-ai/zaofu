"""Reader-fanout child 失败事件的 task_id 消费侧富化(avbs-r4 F8)。

`review.child.failed` / `verify.child.failed` 在 baseline
rework_trigger 集合里,但它们由 reviewer/verifier agent 直接 emit,
实战 payload 不带 task_id——Layer-1 rework triage 与 stage 级
`on_fail: same_lane` backedge 全部以 event.task_id 为前置,于是对
fanout_reader 聚合拓扑结构性哑火(r3 归档 `impl.rework.requested`
0 次)。lane-pipeline 路径(`lane.stage.failed`)自带 manifest 反查,
不受影响。

这里在 orchestrator 消费循环入口做同样的反查:fanout manifest 的
child 条目携带 task_id(dispatch 时写入),按 (fanout_id, child_id)
定位即可。只富化内存中的事件对象,不回写日志。
"""

from __future__ import annotations

from typing import Callable

from zf.core.events.model import ZfEvent

READER_CHILD_FAILURE_EVENTS: frozenset[str] = frozenset({
    "review.child.failed",
    "verify.child.failed",
})


def resolve_reader_child_task_id(
    event: ZfEvent,
    *,
    manifest_loader: Callable[[str], dict | None],
) -> str:
    """返回可归属的 task_id;无法解析时返回空串(调用方保持原样)。"""
    if event.task_id or event.type not in READER_CHILD_FAILURE_EVENTS:
        return ""
    payload = event.payload if isinstance(event.payload, dict) else {}
    fanout_id = str(payload.get("fanout_id") or "")
    child_id = str(payload.get("child_id") or payload.get("child_run") or "")
    if not fanout_id or not child_id:
        return ""
    try:
        manifest = manifest_loader(fanout_id)
    except Exception:
        return ""
    if not isinstance(manifest, dict):
        return ""
    for child in manifest.get("children") or []:
        if not isinstance(child, dict):
            continue
        if str(child.get("child_id") or "") != child_id:
            continue
        child_payload = child.get("payload") if isinstance(child.get("payload"), dict) else {}
        return str(
            child.get("task_id")
            or child_payload.get("task_id")
            or child_payload.get("upstream_task_id")
            or ""
        )
    return ""
