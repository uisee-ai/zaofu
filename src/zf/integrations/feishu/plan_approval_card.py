"""Plan approval 离场闭环卡片(B17 浅档,doc 93 §7.3)。

sidecar 纪律:kernel 只发事件;本模块把 plan.approval.requested/
approved/rejected 渲染成 feishu 交互卡片 dict(浅档=摘要+深链,动作在
Web 完成)。feishu 不可达不影响门 —— Kanban 板与 CLI 是兜底可见面。
纯函数:订阅与投递由 sync sidecar 做。
"""

from __future__ import annotations

from typing import Any


def _load_plan_tasks(state_dir, task_map_ref: str, *, project_root=None,
                     limit: int = 12) -> list[dict[str, Any]]:
    """Read the held plan's task_map → compact rows (task_id, title, affinity,
    scope) to INLINE in the card, so the operator reviews on their phone without
    a (often unreachable) web deep link."""
    import json
    from pathlib import Path

    from zf.runtime.operator_plan_preview import _first_existing_ref

    if not task_map_ref:
        return []
    path = _first_existing_ref(Path(state_dir), project_root=project_root,
                               refs=[task_map_ref])
    if path is None:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tasks = data.get("tasks") if isinstance(data, dict) else []
    rows: list[dict[str, Any]] = []
    for task in (tasks or [])[:limit]:
        if not isinstance(task, dict):
            continue
        rows.append({
            "task_id": str(task.get("task_id") or task.get("id") or ""),
            "title": str(task.get("title") or task.get("summary") or ""),
            "affinity": str(task.get("affinity_tag") or ""),
            "paths": [str(p) for p in (task.get("allowed_paths") or [])][:4],
        })
    return rows


def build_plan_approval_card(
    event_payload: dict[str, Any],
    *,
    web_base_url: str = "",
    checklist_warnings: list[str] | None = None,
    attempt: int = 1,
    tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """plan.approval.requested → 交互卡片。`tasks` 给出时**内容直接 inline**
    (task 列表 + scope),operator 手机上即可审,不依赖 web 深链可达性。"""
    plan_id = str(event_payload.get("plan_id") or "")
    warnings = checklist_warnings or []
    status_line = (
        f"⚠️ checklist {len(warnings)} 项告警" if warnings
        else "✅ checklist 全绿"
    )
    deep_link = (
        f"{web_base_url.rstrip('/')}/?page=inbox&plan={plan_id}"
        if web_base_url else ""
    )
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": (
            f"**待审 plan**(第 {attempt} 轮)\n"
            f"stage: {event_payload.get('stage_id')}  "
            f"tasks: {event_payload.get('task_count')}  "
            f"pdd: {event_payload.get('pdd_id')}\n{status_line}"
        )}},
    ]
    # inline the actual tasks (what's being approved) — the primary review surface
    for task in (tasks or []):
        head = task.get("task_id") or "task"
        if task.get("affinity"):
            head = f"{head} · {task['affinity']}"
        title = f" — {task['title']}" if task.get("title") else ""
        scope = "，".join(task.get("paths") or [])
        content = f"• **{head}**{title}"
        if scope:
            content += f"\n  scope: `{scope}`"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})
    for warning in warnings[:5]:
        elements.append({"tag": "div", "text": {
            "tag": "lark_md", "content": f"- {warning}",
        }})
    actions: list[dict[str, Any]] = []
    if deep_link:
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看 Preview"},
            "type": "primary",
            "url": deep_link,
        })
    # feishu-A P0.3: one-click approve in Feishu (callback gated by feishu-B).
    # Reject needs a reason for synth replan → routed to Web via the deep link,
    # not an inline button (a single click can't carry free-text feedback).
    actions.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "✅ 批准"},
        "type": "default",
        "value": {"action": f"plan-approve:{plan_id}"},
    })
    if actions:
        elements.append({"tag": "action", "actions": actions})
    elements.append({"tag": "note", "elements": [{
        "tag": "plain_text",
        "content": (
            f"plan_id: {plan_id} — approve/reject 在 Web 或 "
            f"`zf plan approve {plan_id}`(CLI 兜底,断网门照常)"
        ),
    }]})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Plan 审核请求"},
            "template": "orange" if warnings else "blue",
        },
        "elements": elements,
        # 卡片状态机:approved/rejected 后 sidecar 用同 card_key 更新
        "_card_key": f"plan-approval-{plan_id}",
    }


def build_plan_verdict_update(
    event_type: str,
    event_payload: dict[str, Any],
) -> dict[str, Any]:
    """plan.approved/rejected → 原卡片回执更新(幂等:同 _card_key)。"""
    plan_id = str(event_payload.get("plan_id") or "")
    approved = event_type == "plan.approved"
    auto = bool(event_payload.get("auto"))
    if approved:
        body = (
            "✅ 已自动放行(plan_approval.enabled=false)" if auto
            else "✅ 已批准 — fanout 已解锁"
        )
        template = "green"
    else:
        reason = str(event_payload.get("reason") or "")
        body = f"❌ 已驳回 — synth 修订中(replan)\nreason: {reason}"
        template = "red"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Plan 审核结果"},
            "template": template,
        },
        "elements": [{"tag": "div", "text": {
            "tag": "lark_md", "content": f"{body}\nplan_id: {plan_id}",
        }}],
        "_card_key": f"plan-approval-{plan_id}",
    }


def sync_plan_approval_cards(
    state_dir,
    *,
    send_card,
    update_card,
    ledger: dict | None = None,
    web_base_url: str = "",
) -> dict:
    """B17 投递接线(浅档,sync 周期 pull 模式同型)。

    扫 events:pending requested 且未发卡 → send_card(card)→ 记
    message_id;verdict(approved/rejected)且卡未更新 → update_card。
    ``ledger`` 为调用方持有的 {card_key: {message_id, state}} dict
    (FeishuSyncLedger 同型,幂等);feishu 不可达由调用方捕获 —— 门
    照常,CLI/Web 兜底。
    """
    from pathlib import Path

    from zf.core.events.log import EventLog

    ledger = ledger if ledger is not None else {}
    try:
        events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        events = []
    requested: dict[str, dict] = {}
    verdicts: dict[str, tuple[str, dict]] = {}
    for event in events:
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        plan_id = str(payload.get("plan_id") or "")
        if not plan_id:
            continue
        etype = str(getattr(event, "type", "") or "")
        if etype == "plan.approval.requested":
            requested[plan_id] = payload
        elif etype in ("plan.approved", "plan.rejected"):
            verdicts[plan_id] = (etype, payload)
    sent, updated = [], []
    for plan_id, payload in requested.items():
        key = f"plan-approval-{plan_id}"
        entry = ledger.get(key) or {}
        if plan_id in verdicts:
            if entry.get("state") != "verdict" and entry.get("message_id"):
                etype, vp = verdicts[plan_id]
                update_card(
                    entry["message_id"],
                    build_plan_verdict_update(etype, vp),
                )
                ledger[key] = {**entry, "state": "verdict"}
                updated.append(plan_id)
            continue
        if entry.get("message_id"):
            continue  # 已发,等裁决
        tasks = _load_plan_tasks(state_dir, str(payload.get("task_map_ref") or ""))
        message_id = send_card(build_plan_approval_card(
            payload, web_base_url=web_base_url, tasks=tasks,
        ))
        ledger[key] = {"message_id": str(message_id), "state": "pending"}
        sent.append(plan_id)
    return {"sent": sent, "updated": updated, "ledger": ledger}


def push_plan_approval_cards_once(
    state_dir,
    transport,
    *,
    receive_id: str,
    receive_id_type: str = "chat_id",
    web_base_url: str = "",
    action_secret: bytes | None = None,
    action_ttl_seconds: int = 86400,
    action_key_version: str = "1",
    now: float | None = None,
) -> dict:
    """Production caller for the sidecar: build card send/update callbacks from a
    Feishu transport + a persistent ledger and run one sync pass. Idempotent
    across ticks via the on-disk ledger. Feishu errors propagate to the caller;
    the approval gate is unaffected (Kanban/CLI remain the fallback surface).

    When ``action_secret`` is set, the inline approve button is signed
    (feishu-A2) so it cannot be forged/replayed/repurposed."""
    import json
    import time
    from pathlib import Path

    from zf.integrations.feishu.callback_token import attach_action_token
    from zf.integrations.feishu.transport import FeishuMessage

    issued_at = time.time() if now is None else now

    ledger_path = (
        Path(state_dir) / "integrations" / "feishu" / "plan_approval_ledger.json"
    )
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}

    def send_card(card: dict) -> str | None:
        if action_secret:
            attach_action_token(
                card,
                secret=action_secret,
                chat_id=receive_id,
                ttl_seconds=action_ttl_seconds,
                now=issued_at,
                key_version=action_key_version,
            )
        return transport.send_card(FeishuMessage(
            chat_id=receive_id,
            content=json.dumps(card, ensure_ascii=False),
            msg_type="interactive",
            receive_id_type=receive_id_type,
        ))

    def update_card(message_id: str, card: dict) -> bool:
        return transport.update_card(message_id, card)

    result = sync_plan_approval_cards(
        state_dir,
        send_card=send_card,
        update_card=update_card,
        ledger=ledger,
        web_base_url=web_base_url,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        json.dumps(result["ledger"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result
