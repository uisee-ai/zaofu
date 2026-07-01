"""Contract health 投影(B9,doc 91 §8/P5)。

把"合同与事实的差距"从 events/artifacts 重算成 per-task 视图:
source_index 覆盖态 / rework attempt / quarantine / approval hold。
纯函数只读 truth;`record validated` 与 `contract satisfied` 是两个
不同状态(doc 91 §8 最后一条)。Web/CLI 共用。
"""

from __future__ import annotations

from typing import Any


def build_contract_health(
    tasks: list[Any],
    events: list[Any],
) -> dict[str, Any]:
    """→ {schema_version, tasks: [{task_id, source_anchor, attempts,
    quarantined, awaiting_approval, signals[]}], summary}"""
    degraded: set[str] = set()
    attempts: dict[str, int] = {}
    quarantined: set[str] = set()
    holds: set[str] = set()
    receipts_gap: set[str] = set()
    pending_plan: dict[str, str] = {}
    for event in events:
        etype = str(getattr(event, "type", "") or "")
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        task_id = str(getattr(event, "task_id", "") or "")
        if etype == "provenance.degraded":
            for tid in payload.get("missing_anchor_task_ids") or []:
                degraded.add(str(tid))
        elif etype == "task_map.ready" and payload.get("rework_of"):
            pdd = str(payload.get("pdd_id") or "")
            attempts[pdd] = attempts.get(pdd, 0) + 1
        elif etype == "issue.candidate.created" and (
            payload.get("source_kind") == "quarantine"
        ):
            quarantined.add(str(payload.get("pdd_id") or ""))
        elif etype == "plan.approval.requested":
            pending_plan[str(payload.get("plan_id") or "")] = str(
                payload.get("pdd_id") or "",
            )
        elif etype in ("plan.approved", "plan.rejected"):
            pending_plan.pop(str(payload.get("plan_id") or ""), None)
        elif etype == "task.context_manifest.gap" and task_id:
            receipts_gap.add(task_id)
    holds = {pdd for pdd in pending_plan.values() if pdd}

    out_tasks: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(getattr(task, "id", "") or "")
        contract = getattr(task, "contract", None)
        has_anchor = bool(
            str(getattr(contract, "source_key", "") or "").strip()
            or str(getattr(contract, "source_ref", "") or "").strip()
        )
        feature = str(getattr(contract, "feature_id", "") or "")
        signals: list[str] = []
        if task_id in degraded or not has_anchor:
            signals.append("source_anchor_degraded")
        if feature in quarantined or task_id in quarantined:
            signals.append("quarantined")
        if feature in holds:
            signals.append("awaiting_approval")
        if task_id in receipts_gap:
            signals.append("context_manifest_gap")
        out_tasks.append({
            "task_id": task_id,
            "status": str(getattr(task, "status", "") or ""),
            "source_anchor": "ok" if has_anchor and task_id not in degraded
                             else "degraded",
            "rework_attempts": attempts.get(feature, 0),
            "signals": signals,
        })
    flagged = [t for t in out_tasks if t["signals"]]
    return {
        "schema_version": "contract-health.v1",
        "tasks": out_tasks,
        "summary": {
            "total": len(out_tasks),
            "flagged": len(flagged),
            "quarantined": len(quarantined),
            "awaiting_approval": len(holds),
            "note": "record validated ≠ contract satisfied(doc 91 §8);"
                    "本投影只读 truth,纯函数可重算",
        },
    }
