"""驳回有效性原语(U22,r6.1 断点续跑凭证复核)。

"同 findings 再拒"在三处被当真却可能是传输错位:①指纹计数(假停滞)
②rework cap/escalate(6 次误报)③操作员/RM 停止决策(触发停机的第
12 轮正是慢一拍伪拒——停机时 worker 分支已修净全部已知 findings)。

机械判定:**被审 candidate 为该 task 集成的 source_commit 等于驳回
时刻该 task 最新的 worker 完成 commit,驳回才有效**;否则 review 判
的是旧内容,驳回不得计入停滞/cap/终局依据。纯事件计算,无 git 依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from zf.core.events.model import ZfEvent


@dataclass(frozen=True)
class RejectionValidity:
    effective: bool
    reason: str
    integrated_commit: str = ""
    latest_completion_commit: str = ""


def _commits_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a)


def rejection_effective(
    events: Iterable[ZfEvent],
    *,
    task_id: str,
    rejection_event_id: str,
) -> RejectionValidity:
    """驳回是否有效(被审内容是否为该 task 最新交付)。

    找不到集成记录或完成记录时保守判有效(不因信息缺失豁免驳回)。
    """
    integrated = ""
    latest_completion = ""
    for event in events:
        if event.id == rejection_event_id:
            break
        if str(event.task_id or "") != task_id:
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("task_id") or "") != task_id:
                continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "candidate.task_ref.applied":
            integrated = str(payload.get("source_commit") or "") or integrated
        elif event.type == "dev.build.done":
            if str(event.actor or "") == "zf-cli":
                continue  # kernel 回声携带 manifest 旧值
            commit = str(payload.get("source_commit") or "").strip()
            if commit:
                latest_completion = commit
    if not integrated or not latest_completion:
        return RejectionValidity(
            effective=True,
            reason="insufficient_records_conservative_effective",
            integrated_commit=integrated,
            latest_completion_commit=latest_completion,
        )
    if _commits_match(integrated, latest_completion):
        return RejectionValidity(
            effective=True,
            reason="candidate_current",
            integrated_commit=integrated,
            latest_completion_commit=latest_completion,
        )
    return RejectionValidity(
        effective=False,
        reason="candidate_behind_latest_completion",
        integrated_commit=integrated,
        latest_completion_commit=latest_completion,
    )


__all__ = ["RejectionValidity", "rejection_effective"]
