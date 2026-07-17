"""Owner-visible Feishu message rendering (backlog 2026-07-07-1315).

Turns an ``owner.visible_message.requested`` payload into a friendly, Chinese,
owner-facing message instead of the developer key-value dump the delivery path
used to send (severity/title/route/restart_strategy field dump + a ``/zf`` CLI
line a Feishu user cannot run). Pure/deterministic: reason -> human text is a
static table, not an LLM call, so it stays in the kernel.
"""

from __future__ import annotations

from typing import Any

_SEVERITY_EMOJI = {
    "critical": "🔴",
    "fatal": "🔴",
    "high": "🟠",
    "error": "🟠",
    "warn": "🟡",
    "warning": "🟡",
    "info": "🟢",
    "low": "🟢",
}

# Known internal reason codes / summary fragments -> plain Chinese. Matched by
# substring so an embedded code (``prd.blocked: stage replan cap exhausted``) is
# still recognised. First match wins, so order specific-before-generic.
_REASON_HUMAN: tuple[tuple[str, str], ...] = (
    # 2026-07-17 card-quality review (/tmp/runm.png): the three
    # highest-frequency Run Manager cards shipped their internal English
    # summary verbatim. Specific-before-generic like the rest of the table.
    ("cost.budget.exceeded", "成本已超预算上限,需要你决定提额、暂停或忽略"),
    ("cost budget exceeded", "成本已超预算上限,需要你决定提额、暂停或忽略"),
    ("requested human decision", "监工在等你的决定才能继续"),
    ("claimed artifact missing", "任务声称的产物在磁盘上不存在,完成证据不可信"),
    ("completion event claims", "任务完成证据不可信(声称的产物在磁盘上不存在)"),
    # Push-prone registry specs surfaced by the coverage forcing test — every
    # title that can reach the Feishu group must render in Chinese.
    ("goal closure requires external input", "目标收尾需要外部输入或你的决定"),
    ("goal delivery operation failed", "目标交付操作失败,需要你关注"),
    ("goal delivery operation is blocked", "目标交付操作被阻塞,需要你关注"),
    ("rm outcome circuit breaker", "监工连续动作无进展已熔断,需要你介入"),
    ("recycle_threshold_exceeded", "有执行单元反复重启、超过阈值,可能已经卡住"),
    ("stage replan cap exhausted", "某阶段重规划次数已用尽,反复未通过准入"),
    ("prd.blocked", "PRD 阶段被阻断(多次重规划仍未通过准入)"),
    ("failure-closeout-activate requires explicit owner approval",
     "有失败流程需要你确认是否收尾"),
    ("requires explicit owner approval", "有动作需要你明确批准才能继续"),
    ("headless provider completed", "后台执行已完成"),
    ("silent_stall", "任务长时间没有进展,疑似停滞"),
    ("worker.stuck", "有执行单元疑似卡住,没有新的进展"),
    ("stuck", "任务疑似卡住,没有新的进展"),
    ("channel_alerts", "讨论频道有需要关注的告警"),
    ("provider binding is missing", "该角色还没有配置可用的后端,无法执行"),
)

# Generic titles that carry no real information — treat like an empty title so we
# fall back to the summary / reason mapping instead of surfacing boilerplate.
_GENERIC_TITLES = {
    "runtime escalated to human",
    "owner attention requested",
}


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()


def _body_signal(payload: dict[str, Any]) -> str:
    """The free-text carrier of the message — emitters put it in ``summary`` or
    ``text``. Kept as one accessor so empty-detection, humanizing and the dedup
    key all agree on what "the content" is."""
    return _text(payload, "summary") or _text(payload, "text")


def owner_message_is_empty(payload: dict[str, Any]) -> bool:
    """A request with no title AND no free-text body carries nothing to show; the
    old path shipped a near-empty field dump for these."""
    title = _text(payload, "title")
    if title.lower() in _GENERIC_TITLES:
        title = ""
    return not title and not _body_signal(payload)


def _humanize(summary: str, title: str) -> str:
    # Map either the summary or (when summary is blank/uninformative) the title
    # through the reason table — the real payloads put the signal in *either*
    # field (summary="recycle_threshold_exceeded" OR title="worker.stuck").
    for signal in (summary, title):
        lowered = signal.lower()
        for needle, human in _REASON_HUMAN:
            if needle in lowered:
                return human
    if summary:
        return summary
    if title and title.lower() not in _GENERIC_TITLES:
        return title
    return "运行需要你关注"


def humanize_owner_title(title: str) -> str:
    """Card-header variant of the reason mapping: known internal titles render
    in Chinese; generic no-information titles collapse to the standard prompt;
    anything else passes through unchanged (never an internal enum dump)."""
    return _humanize("", title)


def owner_message_dedup_key(payload: dict[str, Any]) -> str:
    """Content-based fold key: identical-looking owner messages collapse even
    when their fingerprints differ (the real recycle_threshold_exceeded ×9 each
    carried a distinct fingerprint). Keyed on severity + the humanized body."""
    severity = _text(payload, "severity").lower()
    body = _humanize(_body_signal(payload), _text(payload, "title"))
    return f"{severity}|{body}"


_VERDICT_HUMAN = {
    "terminal_seen": "任务已到达终态",
    "no_progress": "任务没有新的进展",
    "stalled": "任务疑似停滞",
    "healthy": "任务仍在正常推进",
}


def _events_state_line(payload: dict[str, Any]) -> str:
    derived = payload.get("events_derived_state")
    if not isinstance(derived, dict):
        return ""
    verdict = str(derived.get("verdict") or "").strip()
    if not verdict:
        return ""
    # Whitelist, not passthrough: an unmapped verdict is an internal enum
    # (no_missing / no_reconcile_contract / ...) that reads as noise or even a
    # contradiction on the card (/tmp/runm.png showed 事件判定:no_missing under
    # an "artifacts do not exist" title). No mapping -> drop the whole line.
    human = _VERDICT_HUMAN.get(verdict)
    if human is None:
        return ""
    terminal = str(derived.get("task_terminal_seen") or "").strip()
    if terminal:
        return f"事件判定:{human}({terminal})"
    missing = derived.get("missing") or []
    heads = "、".join(
        f"{m.get('stage_id')}停{m.get('age_s')}秒"
        for m in missing[:2]
        if isinstance(m, dict)
    )
    return f"事件判定:{human}" + (f",缺:{heads}" if heads else "")


def render_owner_message(payload: dict[str, Any], *, task_id: str = "") -> str:
    """Friendly, Chinese owner-facing message. Never returns the ``/zf`` CLI line
    or a raw field dump."""
    severity = _text(payload, "severity").lower()
    emoji = _SEVERITY_EMOJI.get(severity, "🔔")
    title = _text(payload, "title")
    body = _humanize(_body_signal(payload), title)

    lines = [f"{emoji} {body}"]

    # G2 (I41): lead with the events-derived truth over pane observation — kept,
    # just rendered in plain Chinese instead of the raw "events-state:" dump.
    state_line = _events_state_line(payload)
    if state_line:
        lines.append(state_line)

    task = task_id or _text(payload, "task_id")
    if task:
        lines.append(f"任务:{task}")

    # 2026-07-17 card-quality L3: the old closing line promised keyword replies
    # (「重试」/「忽略」) that NOTHING consumed — a dead prompt. Action guidance
    # now only states channels that actually exist: the card's buttons (added by
    # the delivery layer) and the Web inbox; info-only cards carry no action line.
    if bool(payload.get("human_action_required")):
        lines.append("需要你的确认后才能继续。")
        lines.append("——点「确认收到」或到 Web 收件箱处理;也可 @我 说明处理意见。")
    return "\n".join(lines)
