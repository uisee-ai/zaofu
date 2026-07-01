"""Closeout 沉淀门 — decision evidence(X16,G-MEM-1 终局形)。

历史:G-MEM-1 把 memory.note 从 Optional 升级为 checkpoint(workers
跳过 optional 节);K5 按审计 Q2 把该 prose 降级回单行提示。本模块是
终局形:**用门不用 prose** —— 完成 payload 可带结构化 learning 决策,
kernel 在 done 时刻铸 `closeout.learning.recorded`;strict/release 下
缺失发 gap 事件(observe-first,不回滚任务,与 K5 两段式一致)。

决策四选一:docs_patch / skill_update / backlog_candidate / no_update
(+reason)。不自动改 runtime truth:decision 是 evidence,落地动作
(写 docs/skill/backlog)仍走人工或独立批次。
"""

from __future__ import annotations

from typing import Any

LEARNING_DECISIONS = frozenset({
    "docs_patch", "skill_update", "backlog_candidate", "no_update",
})


def extract_closeout_decision(payload: Any) -> dict | None:
    """完成事件 payload.learning → 规范化决策 dict;无/畸形 → None。"""
    if not isinstance(payload, dict):
        return None
    learning = payload.get("learning")
    if not isinstance(learning, dict):
        return None
    decision = str(learning.get("decision") or "").strip()
    if decision not in LEARNING_DECISIONS:
        return None
    return {
        "decision": decision,
        "reason": str(learning.get("reason") or "").strip(),
        "refs": [str(r) for r in learning.get("refs") or []][:5],
    }


def closeout_events_for_done(
    *,
    task_id: str,
    terminal_event_id: str,
    payload: Any,
    harness_profile: str,
) -> list[dict]:
    """done 时刻应铸的 closeout 事件(payload dict 列表,调用方包 ZfEvent)。

    - 有合法 learning → closeout.learning.recorded
    - 无且 strict/release → closeout.learning.gap(severity=STOP 标注,
      observe-first 不回滚);baseline → 不发(K5 已有单行提示兜底)
    """
    decision = extract_closeout_decision(payload)
    if decision is not None:
        return [{
            "type": "closeout.learning.recorded",
            "payload": {
                "task_id": task_id,
                "terminal_event_id": terminal_event_id,
                **decision,
            },
        }]
    if harness_profile in ("strict", "release"):
        return [{
            "type": "closeout.learning.gap",
            "payload": {
                "task_id": task_id,
                "terminal_event_id": terminal_event_id,
                "severity": "STOP",
                "note": "strict 档完成 payload 缺 learning 决策"
                        "(docs_patch|skill_update|backlog_candidate|"
                        "no_update);observe-first,不回滚任务",
            },
        }]
    return []
