"""跨代收编真实完成(r6.1 断点复盘 BF-1)。

r6.1 实弹:fanout 超时重派/重启换代(fanout_id/run_id 轮换)期间,dev
的真交付携带派发时的旧身份到达,被 stale_completion 一律丢弃——16:41
一笔命中全部 findings 的完整 rework 被丢,review 环此后整晚未再转。

身份 ≠ 有效性:同一 task 在当前代 fanout 的 child 仍未终局,说明这份
工作**仍被等待**。kernel 只判"任务还要不要"(机械),内容好坏归
review(语义)。收编陈旧交付的代价是一轮 review 拒收;丢弃真交付的
代价是整轮死锁。

目标查找走 fanout_identity 投影的 current-by-key 一跳直达(r6.1 实测
换代链 13+ 代,逐跳走 superseded_by 既慢又易断)。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

TERMINAL_CHILD_STATUSES = frozenset({"completed", "failed"})


@dataclass(frozen=True)
class AdoptionTarget:
    manifest: dict[str, Any]
    child: dict[str, Any]
    adopted_into: str


def find_writer_adoption_target(
    *,
    fanout_id: str,
    task_id: str,
    current_sibling_lookup: Callable[[str], dict[str, Any] | None],
    manifest_loader: Callable[[str], dict[str, Any] | None],
) -> AdoptionTarget | None:
    """同 logical_key 当前代中,同 task 的 child 未终局则返回收编目标。

    返回 None 的情形:无当前代同键实例(cancelled 收尾等)、当前代不是
    fanout_writer_scoped、当前代没有同 task 的 child、或该 child 已终局
    (说明新一代已交付,来件才是真正的陈旧完成)。
    """
    if not task_id or not fanout_id:
        return None
    sibling = current_sibling_lookup(fanout_id) or {}
    current_id = str(sibling.get("fanout_id") or "")
    if not current_id:
        return None
    manifest = manifest_loader(current_id) or {}
    if manifest.get("topology") != "fanout_writer_scoped":
        return None
    for child in manifest.get("children") or []:
        if not isinstance(child, dict):
            continue
        if str(child.get("task_id") or "") != task_id:
            continue
        if str(child.get("status") or "") in TERMINAL_CHILD_STATUSES:
            return None
        return AdoptionTarget(
            manifest=manifest,
            child=child,
            adopted_into=current_id,
        )
    return None


__all__ = [
    "AdoptionTarget",
    "TERMINAL_CHILD_STATUSES",
    "find_writer_adoption_target",
]
