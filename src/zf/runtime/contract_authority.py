"""kanban 契约作为 verification 的唯一权威源(avbs-r4 F4)。

r4 实证的三向真相分叉:`task.contract.update` 修订 kanban 契约后,
writer briefing 读 plan-synth workdir 的 task_map 副本、reviewer 读
candidate 树内副本,两处都是旧命令——工人按旧契约诚实卡死、reviewer
按旧命令判拒,operator 被迫机械对齐两份副本。

治理原则:派发 payload 组装时,若 kanban 存在 canonical 任务且其契约
带 verification,则以契约覆盖 task_map 工件值;工件副本降级为历史记录
与无 canonical 任务时的回退。
"""

from __future__ import annotations

from typing import Any


def apply_contract_authority(task_item: dict[str, Any], task_store) -> dict[str, Any]:
    """返回以 kanban 契约为准修订后的 task_item(无 canonical 任务时原样)。"""
    task_id = str(task_item.get("task_id") or task_item.get("id") or "")
    if not task_id:
        return task_item
    try:
        task = task_store.get(task_id)
    except Exception:
        return task_item
    contract = getattr(task, "contract", None) if task is not None else None
    if contract is None:
        return task_item
    updated = dict(task_item)
    verification = str(getattr(contract, "verification", "") or "").strip()
    if verification and verification != str(updated.get("verification") or ""):
        updated["verification"] = verification
        updated["verification_source"] = "kanban_contract"
    tiers = list(getattr(contract, "verification_tiers", []) or [])
    if tiers:
        updated["verification_tiers"] = tiers
    return updated
