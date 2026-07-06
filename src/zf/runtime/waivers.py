"""Operator waive 裁决的事件化持久层(avbs-r4 F6,doc 124 STOP waive-trail)。

r4 实证:operator 对"物理不可达验证命令"的 waive 裁决只活在 chat/session
里,每次 worker respawn 即蒸发,同一裁决被迫重复下发 5+ 次;reviewer 侧
也各判各的。治理:waive 走事件(truth 在 events.jsonl),briefing 渲染时
自动带出活跃 waive 清单——裁决一次生效,respawn 不丢,全程可审计。

操作姿势(operator):

    zf emit verification.waived --payload '{
      "task_ids": ["AVBS-SCENE-001"],        # 或 ["*"] 全局
      "signature": "npx playwright test tests/e2e/scene",
      "reason": "chromium 缺 libnspr4.so,环境缺陷 r5 preflight 修",
      "waived_by": "operator"
    }'

撤销:emit `verification.waiver.revoked`,payload 带同一 signature。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent

WAIVE_EVENT = "verification.waived"
REVOKE_EVENT = "verification.waiver.revoked"


def _waiver_task_ids(event: ZfEvent) -> list[str]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    ids = [
        str(x).strip() for x in (payload.get("task_ids") or []) if str(x).strip()
    ]
    single = str(payload.get("task_id") or event.task_id or "").strip()
    if single:
        ids.append(single)
    return ids


def active_waivers(events: list[ZfEvent], task_id: str) -> list[dict[str, Any]]:
    """按事件序回放 waive/revoke,返回该任务当前活跃的 waive 清单。"""
    waivers: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.type not in (WAIVE_EVENT, REVOKE_EVENT):
            continue
        scoped = _waiver_task_ids(event)
        if scoped and task_id not in scoped and "*" not in scoped:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        signature = str(
            payload.get("signature")
            or payload.get("command")
            or payload.get("finding_signature")
            or ""
        ).strip()
        if not signature:
            continue
        if event.type == WAIVE_EVENT:
            waivers[signature] = {
                "signature": signature,
                "reason": str(payload.get("reason") or ""),
                "waived_by": str(
                    payload.get("waived_by") or event.actor or "operator"
                ),
                "event_id": event.id,
                "ts": event.ts,
            }
        else:
            waivers.pop(signature, None)
    return list(waivers.values())


def load_active_waivers(
    state_dir: Path, task_id: str, *, config: object | None = None,
) -> list[dict[str, Any]]:
    """从 state_dir 事件日志读活跃 waive;任何失败 fail-open 返回空。"""
    try:
        from zf.core.events.factory import event_log_from_project

        log = event_log_from_project(Path(state_dir), config=config)
        return active_waivers(log.read_all(), task_id)
    except Exception:
        return []


def render_waiver_lines(waivers: list[dict[str, Any]]) -> list[str]:
    if not waivers:
        return []
    lines = [
        "**Operator waivers**(以下验证命令/发现已由 operator 正式豁免,"
        "不要因它们 block/escalate;evidence 中注明 waived 即可):",
    ]
    for waiver in waivers:
        reason = waiver.get("reason") or "(no reason recorded)"
        lines.append(f"- `{waiver['signature']}` — {reason}")
    return lines
