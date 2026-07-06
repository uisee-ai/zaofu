"""Streaming Q&A card — fold agent token deltas into one throttled, in-place card.

feishu-stream (doc 98 §9): the operator asks the bot in Feishu and sees the answer
stream into ONE card (typewriter), instead of only Working→Done. The stream is
reduced into a throttled card update; this is "throttled summary update", not
per-token spam, and the deltas never touch events.jsonl.

Rich rendering (backlog 2026-06-22, R1-R4): the state is an INTERLEAVED block list
(text / tool, chronological) — a tool call renders as a collapsible panel with its
input summary + output, not a bare count. ≥3 tools collapse (latest stays visible
while running); size caps defeat Feishu's ~30KB per-element limit. Card schema 2.0.

Pure functions; the transport + throttled flush loop live in the delivery wiring.
"""

from __future__ import annotations

from typing import Any

from zf.integrations.feishu.tool_render import tool_body, tool_header

_REASONING_CAP = 1500       # truncate reasoning to defeat the ~30KB element limit
_TOOL_COLLAPSE_THRESHOLD = 3

# kind → footer status while running. Note: the emit layer
# (agent_session_stream._kind_for_message_type) produces "tool" / "tool_result";
# older paths used "tool_use" — accept both.
_FOOTER_BY_KIND = {
    "status": "thinking",     # run just started → 🧠 思考中 (before any content)
    "thinking": "thinking",
    "tool": "tool_running",
    "tool_use": "tool_running",
    "tool_result": "streaming",
    "result": "streaming",
    "text": "streaming",
}
_TOOL_KINDS = {"tool", "tool_use"}
_TOOL_RESULT_KINDS = {"tool_result", "result"}
# event type → terminal state.
_TERMINAL_BY_EVENT = {
    "channel.agent.reply.completed": "done",
    "agent.session.run.completed": "done",
    "channel.agent.reply.failed": "failed",
    "agent.session.run.failed": "failed",
    "agent.session.run.cancelled": "interrupted",
}
_TERMINAL = {"done", "failed", "interrupted"}


def new_stream_state(request_id: str = "", **meta: Any) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "blocks": [],            # ordered: {"kind":"text","content"} | {"kind":"tool","tool":{...}}
        "text": "",              # back-compat: concatenation of text deltas
        "tool_count": 0,         # back-compat / collapse: number of tool blocks
        "reasoning": "",
        "reasoning_active": False,
        "footer": None,
        "terminal": "running",
        "reason": "",
        "member_id": str(meta.get("member_id") or ""),
        "provider": str(meta.get("provider") or ""),
        "seq": 0,
    }


def _append_text(state: dict[str, Any], chunk: str) -> None:
    blocks = state["blocks"]
    if blocks and blocks[-1]["kind"] == "text":
        blocks[-1]["content"] += chunk
    else:
        blocks.append({"kind": "text", "content": chunk})
    state["text"] += chunk


def _close_tool(state: dict[str, Any], name: str, output: str, is_error: bool) -> None:
    """Mark the most recent matching running tool block done/error."""
    for block in reversed(state["blocks"]):
        if block["kind"] != "tool" or block["tool"]["status"] != "running":
            continue
        if name and block["tool"]["name"] and block["tool"]["name"] != name:
            continue
        block["tool"]["output"] = output
        block["tool"]["status"] = "error" if is_error else "done"
        return


def reduce(state: dict[str, Any], event: Any) -> dict[str, Any]:
    """Fold one event into the streaming state (in place). Terminal is sticky."""
    if state["terminal"] in _TERMINAL:
        return state
    etype = str(getattr(event, "type", "") or "")
    payload = getattr(event, "payload", None)
    payload = payload if isinstance(payload, dict) else {}

    terminal = _TERMINAL_BY_EVENT.get(etype)
    if terminal:
        state["terminal"] = terminal
        state["footer"] = None
        state["reasoning_active"] = False
        state["reason"] = str(payload.get("reason") or "")
        return state

    if etype != "agent.session.part.delta":
        return state

    kind = str(payload.get("kind") or "text")
    chunk = str(payload.get("delta") or payload.get("content") or "")
    refs = payload.get("refs")
    refs = refs if isinstance(refs, dict) else {}
    state["footer"] = _FOOTER_BY_KIND.get(kind, "streaming")

    if kind == "thinking":
        state["reasoning"] = (state["reasoning"] + chunk)[:_REASONING_CAP]
        state["reasoning_active"] = True
    elif kind in _TOOL_KINDS:
        tool = {"name": str(refs.get("tool") or ""), "input": refs.get("input"),
                "output": "", "status": "running"}
        state["blocks"].append({"kind": "tool", "tool": tool})
        state["tool_count"] += 1
        state["reasoning_active"] = False
    elif kind in _TOOL_RESULT_KINDS:
        raw = refs.get("raw") if isinstance(refs.get("raw"), dict) else {}
        is_error = bool(refs.get("is_error") or raw.get("is_error"))
        _close_tool(state, str(refs.get("tool") or ""),
                    str(refs.get("output") or chunk or ""), is_error)
        state["reasoning_active"] = False
    elif kind in ("text", ""):
        _append_text(state, chunk)
        state["reasoning_active"] = False

    if not state["member_id"]:
        state["member_id"] = str(payload.get("member_id") or "")
    if not state["provider"]:
        state["provider"] = str(payload.get("provider") or payload.get("backend") or "")
    return state


def _has_content(state: dict[str, Any]) -> bool:
    return bool(state["blocks"] or state["reasoning"])


def should_flush(state: dict[str, Any], last_flush_monotonic: float, now: float,
                 *, interval_s: float = 0.4) -> bool:
    """First content flushes immediately; then coalesce until interval/terminal."""
    if state["terminal"] in _TERMINAL:
        return True
    if last_flush_monotonic <= 0:  # first content not yet flushed
        return _has_content(state) or bool(state["footer"])
    return (now - last_flush_monotonic) >= interval_s


_FOOTER_LABEL = {
    "thinking": "🧠 正在思考",
    "tool_running": "🧰 正在调用工具",
    "streaming": "✍️ 正在输出",
}
_TERMINAL_NOTE = {
    "done": "✅ 完成",
    "failed": "⚠️ agent 失败",
    "interrupted": "⏹️ 已被中断",
}
_PANEL_BORDER = {"running": "grey", "done": "grey", "error": "red"}
_ICON = {"tag": "standard_icon", "token": "down-small-ccm_outlined", "size": "16px 16px"}


def _markdown(content: str, *, notation: bool = False) -> dict[str, Any]:
    el: dict[str, Any] = {"tag": "markdown", "content": content}
    if notation:
        el["text_size"] = "notation"
    return el


def _panel(title_md: str, body_md: str, *, expanded: bool, border: str) -> dict[str, Any]:
    return {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {"title": {"tag": "markdown", "content": title_md},
                   "vertical_align": "center", "icon": _ICON,
                   "icon_position": "follow_text", "icon_expanded_angle": -180},
        "border": {"color": border, "corner_radius": "5px"},
        "vertical_spacing": "8px", "padding": "8px 8px 8px 8px",
        "elements": [_markdown(body_md or "_无输出_", notation=True)],
    }


def _tool_panel(tool: dict[str, Any], expanded: bool) -> dict[str, Any]:
    border = _PANEL_BORDER.get("error" if tool["status"] == "error" else "running", "grey")
    return _panel(tool_header(tool), tool_body(tool), expanded=expanded, border=border)


def _collapsed_tools(tools: list[dict[str, Any]], finalized: bool) -> dict[str, Any]:
    suffix = "（已结束）" if finalized else "（已折叠）"
    title = f"🧰 **{len(tools)} 个工具调用{suffix}**"
    header_list = "\n".join(f"- {tool_header(t)}" for t in tools)
    return _panel(title, header_list, expanded=False, border="blue")


def _render_tool_group(tools: list[dict[str, Any]], finalized: bool) -> list[dict[str, Any]]:
    if not tools:
        return []
    if len(tools) < _TOOL_COLLAPSE_THRESHOLD:
        return [_tool_panel(t, False) for t in tools]
    if finalized:
        return [_collapsed_tools(tools, True)]
    # running: collapse prior tools, keep the latest visible (live observation)
    prior, latest = tools[:-1], tools[-1]
    out: list[dict[str, Any]] = []
    if prior:
        out.append(_collapsed_tools(prior, False))
    out.append(_tool_panel(latest, True))
    return out


def _group_blocks(blocks: list[dict[str, Any]]):
    tool_buf: list[dict[str, Any]] = []
    for block in blocks:
        if block["kind"] == "tool":
            tool_buf.append(block["tool"])
            continue
        if tool_buf:
            yield {"kind": "tools", "tools": tool_buf}
            tool_buf = []
        yield {"kind": "text", "content": block["content"]}
    if tool_buf:
        yield {"kind": "tools", "tools": tool_buf}


def _reasoning_panel(content: str, active: bool) -> dict[str, Any]:
    title = "🧠 **思考中**" if active else "🧠 **思考完成，点击查看**"
    return _panel(title, content, expanded=active, border="grey")


def _summary_text(state: dict[str, Any]) -> str:
    terminal = state["terminal"]
    if terminal == "interrupted":
        return "已中断"
    if terminal == "failed":
        return "出错"
    if terminal == "done":
        return "已完成"
    if state["footer"] == "tool_running":
        return "正在调用工具"
    if state["footer"] == "streaming":
        return "正在输出"
    return "思考中"


def render_streaming_card(state: dict[str, Any]) -> dict[str, Any]:
    """Pure state → Feishu card (schema 2.0). streaming_mode only while running."""
    terminal = state["terminal"]
    running = terminal == "running"
    finalized = not running
    elements: list[dict[str, Any]] = []

    if state["reasoning"]:
        elements.append(_reasoning_panel(state["reasoning"], state["reasoning_active"]))

    for group in _group_blocks(state["blocks"]):
        if group["kind"] == "text":
            if group["content"].strip():
                elements.append(_markdown(group["content"]))
        else:
            elements.extend(_render_tool_group(group["tools"], finalized))

    if terminal == "interrupted":
        elements.append(_markdown("_⏹ 已被中断_", notation=True))
    elif terminal == "failed":
        note = _TERMINAL_NOTE["failed"]
        if state["reason"]:
            note = f"{note} — {state['reason']}"
        elements.append(_markdown(note, notation=True))
    elif terminal == "done" and not elements:
        elements.append(_markdown("_（未返回内容）_", notation=True))

    if running and state["footer"]:
        elements.append(_markdown(_FOOTER_LABEL.get(state["footer"], "✍️ 正在输出"),
                                  notation=True))
    if not elements:
        elements.append(_markdown("…"))

    # No header: a clean white card (matches bbc/lark-bridge) — the bot identity
    # is shown by Feishu above the card, and status lives in the footer + terminal
    # notes, so a colored title bar is just visual noise. NB: card 2.0 strictly
    # rejects unknown top-level properties (400), so NO internal keys here — the
    # ledger keys off request_id independently (sync_stream_card).
    return {
        "schema": "2.0",
        "config": {"streaming_mode": running, "summary": {"content": _summary_text(state)}},
        "body": {"elements": elements},
    }


def _render_signature(state: dict[str, Any]) -> str:
    """A change signal for throttled in-place updates: any rendered content shift
    (text, tool status/output, reasoning, footer, terminal) flips the signature."""
    parts = [state["terminal"], str(state["footer"]), str(len(state["reasoning"]))]
    for block in state["blocks"]:
        if block["kind"] == "text":
            parts.append(f"t{len(block['content'])}")
        else:
            tool = block["tool"]
            parts.append(f"x{tool['name']}:{tool['status']}:{len(tool['output'])}")
    return "|".join(parts)


_STREAM_EVENT_TYPES = {"agent.session.part.delta"} | set(_TERMINAL_BY_EVENT)


def _fold_stream_states(events: list, member: str = "") -> dict[str, dict[str, Any]]:
    """Fold the event stream into {request_id: state}, ONLY for replies that
    actually streamed deltas (a non-streaming reply gets no stream card).

    When ``member`` is set, keep only replies authored by that member. This is
    what lets several per-bot bridges share one state_dir/events.jsonl without
    each bridge trying to (cross-)render every other bot's reply card — the
    shared-ledger collision that yields Feishu 230001 "not the message's sender".
    """
    states: dict[str, dict[str, Any]] = {}
    for event in events:
        etype = str(getattr(event, "type", "") or "")
        if etype not in _STREAM_EVENT_TYPES:
            continue
        payload = getattr(event, "payload", None)
        payload = payload if isinstance(payload, dict) else {}
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            continue
        if etype == "agent.session.part.delta":
            reduce(states.setdefault(request_id, new_stream_state(request_id)), event)
        elif request_id in states:  # terminal only folds into a streaming reply
            reduce(states[request_id], event)
    if member:
        states = {rid: st for rid, st in states.items()
                  if str(st.get("member_id") or "") == member}
    return states


def sync_stream_card(state_dir, *, send_card, update_card, ledger: dict | None = None,
                     member: str = "") -> dict:
    """Send one streaming card per reply, update it in place as content grows.

    Pull model: each call renders the CURRENT folded state — the tick interval is
    the throttle (N deltas since last tick collapse to one render). Idempotent via
    ledger {card_key: {message_id, seq, sig, terminal}}. Never writes events.jsonl
    (deltas drive the card only — §5.1)."""
    from pathlib import Path

    from zf.core.events.log import EventLog

    ledger = ledger if ledger is not None else {}
    try:
        events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        events = []
    # doc 106 B axis: token deltas ride the ephemeral LiveDeltaBus, not the
    # ledger. The card fold merges both — the bus file scratch is readable
    # across processes, so the bridge keeps streaming while replies run in
    # the web/orchestrator process. Committed terminal events still come
    # from events.jsonl.
    try:
        from zf.runtime.live_delta_bus import LiveDeltaBus

        live_rows, _ = LiveDeltaBus(Path(state_dir)).read_since()
        if live_rows:
            # Re-sort by ts: a committed terminal event must fold AFTER the
            # bus deltas that created its stream state, or the card never
            # finalizes.
            events = sorted(
                [*events, *live_rows],
                key=lambda e: str(getattr(e, "ts", "") or ""),
            )
    except Exception:
        pass
    states = _fold_stream_states(events, member=member)
    sent, updated = [], []
    for request_id, state in states.items():
        key = f"stream-{request_id}"
        entry = ledger.get(key) or {}
        card = render_streaming_card(state)
        sig = _render_signature(state)
        if not entry.get("message_id"):
            message_id = send_card(card)
            ledger[key] = {"message_id": str(message_id), "seq": 0,
                           "sig": sig, "terminal": state["terminal"]}
            sent.append(request_id)
            continue
        if sig != entry.get("sig"):
            seq = int(entry.get("seq", 0)) + 1
            update_card(entry["message_id"], card, seq)
            ledger[key] = {**entry, "seq": seq, "sig": sig,
                           "terminal": state["terminal"]}
            updated.append(request_id)
    return {"sent": sent, "updated": updated, "ledger": ledger}


def push_stream_card_once(state_dir, transport, *, receive_id: str,
                          receive_id_type: str = "chat_id") -> dict:
    """Production caller: build send/update closures from a transport + a
    persistent ledger and run one streaming-card sync pass."""
    import json
    import os
    from pathlib import Path

    from zf.integrations.feishu.transport import FeishuMessage

    # Per-bot isolation: when several bridges (one per Feishu app) share a
    # state_dir, scope each bridge to its own member's replies AND give it a
    # member-suffixed ledger file, so they never cross-update each other's cards.
    member = os.environ.get("ZF_FEISHU_STREAM_MEMBER", "").strip()
    suffix = f"-{member}" if member else ""
    ledger_path = (Path(state_dir) / "integrations" / "feishu"
                   / f"stream_ledger{suffix}.json")
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}

    def send_card(card: dict) -> str | None:
        return transport.send_card(FeishuMessage(
            chat_id=receive_id, content=json.dumps(card, ensure_ascii=False),
            msg_type="interactive", receive_id_type=receive_id_type))

    def update_card(message_id: str, card: dict, sequence: int = 0) -> bool:
        return transport.update_card(message_id, card, sequence)

    result = sync_stream_card(state_dir, send_card=send_card,
                              update_card=update_card, ledger=ledger, member=member)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(result["ledger"], ensure_ascii=False, indent=2),
                           encoding="utf-8")
    return result
