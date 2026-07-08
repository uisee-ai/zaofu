"""feishu-stream B1: streaming reducer (delta → accumulating card state) +
rich tool-card rendering (backlog 2026-06-22 R1-R4)."""

from __future__ import annotations

import json

from zf.core.events.model import ZfEvent
from zf.integrations.feishu.stream_card import (
    new_stream_state,
    reduce,
    render_streaming_card,
    should_flush,
)


def _delta(kind: str, chunk: str, request_id: str = "R1") -> ZfEvent:
    return ZfEvent(type="agent.session.part.delta", actor="dev",
                   payload={"request_id": request_id, "kind": kind,
                            "delta": chunk, "content": chunk})


def _tool(name: str, tool_input=None, request_id: str = "R1") -> ZfEvent:
    return ZfEvent(type="agent.session.part.delta", actor="dev",
                   payload={"request_id": request_id, "kind": "tool",
                            "refs": {"tool": name, "input": tool_input or {}}})


def _tool_result(name: str, output: str, is_error: bool = False,
                 request_id: str = "R1") -> ZfEvent:
    return ZfEvent(type="agent.session.part.delta", actor="dev",
                   payload={"request_id": request_id, "kind": "tool_result",
                            "refs": {"tool": name, "output": output,
                                     "is_error": is_error}})


def test_text_deltas_accumulate():
    st = new_stream_state("R1")
    for c in ["Hel", "lo ", "world"]:
        reduce(st, _delta("text", c))
    assert st["text"] == "Hello world"
    assert st["footer"] == "streaming" and st["terminal"] == "running"


def test_thinking_then_tool_then_text_footer():
    st = new_stream_state("R1")
    reduce(st, _delta("thinking", "hmm"))
    assert st["footer"] == "thinking" and st["reasoning"] == "hmm"
    reduce(st, _delta("tool_use", ""))
    assert st["footer"] == "tool_running" and st["tool_count"] == 1
    reduce(st, _delta("text", "answer"))
    assert st["footer"] == "streaming" and st["text"] == "answer"


def test_terminal_done_clears_footer_and_is_sticky():
    st = new_stream_state("R1")
    reduce(st, _delta("text", "partial"))
    reduce(st, ZfEvent(type="channel.agent.reply.completed",
                       actor="dev", payload={"request_id": "R1"}))
    assert st["terminal"] == "done" and st["footer"] is None
    reduce(st, _delta("text", " more"))
    assert st["text"] == "partial"  # late delta ignored (sticky terminal)


def test_terminal_interrupted_carries_reason():
    st = new_stream_state("R1")
    reduce(st, ZfEvent(type="agent.session.run.cancelled", actor="op",
                       payload={"request_id": "R1", "reason": "operator"}))
    assert st["terminal"] == "interrupted" and st["reason"] == "operator"


def test_reasoning_truncated_at_cap():
    st = new_stream_state("R1")
    reduce(st, _delta("thinking", "x" * 5000))
    assert len(st["reasoning"]) == 1500


def test_should_flush_first_immediate_then_throttled():
    st = new_stream_state("R1")
    reduce(st, _delta("text", "hi"))
    assert should_flush(st, 0.0, 0.0) is True        # first content immediate
    assert should_flush(st, 100.0, 100.1) is False   # 100ms later, throttled
    assert should_flush(st, 100.0, 100.5) is True    # >=400ms
    reduce(st, ZfEvent(type="channel.agent.reply.completed", actor="d",
                       payload={"request_id": "R1"}))
    assert should_flush(st, 100.0, 100.1) is True     # terminal always flushes


def test_render_streaming_mode_on_while_running_off_terminal():
    st = new_stream_state("R1")
    reduce(st, _delta("text", "typing"))
    card = render_streaming_card(st)
    assert card["config"]["streaming_mode"] is True   # typewriter on while running
    assert "typing" in str(card)
    reduce(st, ZfEvent(type="channel.agent.reply.completed", actor="d",
                       payload={"request_id": "R1"}))
    done = render_streaming_card(st)
    assert done["config"]["streaming_mode"] is False  # finalized


def test_card_has_no_header_clean_white():
    # clean white card (bbc-style): no colored header bar, status in footer/notes
    st = new_stream_state("R1")
    reduce(st, _delta("text", "hi"))
    assert "header" not in render_streaming_card(st)


def test_card_has_only_valid_v2_top_level_keys():
    # card 2.0 strictly rejects unknown top-level properties (400 "unknown
    # property"). An internal key like _card_key here silently breaks every
    # live send — guard the exact allowed set.
    st = new_stream_state("R9")
    reduce(st, _delta("text", "hi"))
    card = render_streaming_card(st)
    assert set(card) == {"schema", "config", "body"}
    assert card["schema"] == "2.0"


# --- R1: interleaved block model (text/tool chronological) -------------------

def test_blocks_preserve_text_tool_text_order():
    st = new_stream_state("R1")
    reduce(st, _delta("text", "before"))
    reduce(st, _tool("Bash", {"command": "ls"}))
    reduce(st, _tool_result("Bash", "a.py b.py"))
    reduce(st, _delta("text", "after"))
    kinds = [b["kind"] for b in st["blocks"]]
    assert kinds == ["text", "tool", "text"]
    body = json.dumps(render_streaming_card(st), ensure_ascii=False)
    # text appears before and after the tool panel; order preserved
    assert body.index("before") < body.index("ls") < body.index("after")


def test_tool_result_closes_matching_tool():
    st = new_stream_state("R1")
    reduce(st, _tool("Read", {"file_path": "money.js"}))
    assert st["blocks"][0]["tool"]["status"] == "running"
    reduce(st, _tool_result("Read", "function cents(a){...}"))
    assert st["blocks"][0]["tool"]["status"] == "done"
    reduce(st, _tool("Bash", {"command": "boom"}))
    reduce(st, _tool_result("Bash", "command not found", is_error=True))
    assert st["blocks"][1]["tool"]["status"] == "error"


# --- R2: rich per-tool panel -------------------------------------------------

def test_tool_panel_renders_header_summary_and_output():
    st = new_stream_state("R1")
    reduce(st, _tool("Bash", {"command": "pytest -q"}))
    reduce(st, _tool_result("Bash", "5 passed"))
    body = json.dumps(render_streaming_card(st), ensure_ascii=False)
    assert "collapsible_panel" in body
    assert "**Bash**" in body and "pytest -q" in body   # header summary + input
    assert "5 passed" in body                           # output rendered
    assert "✅" in body                                  # done icon


# --- R3: collapse strategy ---------------------------------------------------

def test_render_collapses_many_tools_running_keeps_latest():
    st = new_stream_state("R1")
    for i in range(4):
        reduce(st, _tool("Bash", {"command": f"step{i}"}))
    body = json.dumps(render_streaming_card(st), ensure_ascii=False)
    # running: prior 3 collapse into a summary, the latest stays its own panel
    assert "3 个工具调用（已折叠）" in body
    assert "step3" in body  # latest still visible


def test_render_collapses_many_tools_finalized():
    st = new_stream_state("R1")
    for i in range(4):
        reduce(st, _tool("Bash", {"command": f"step{i}"}))
        reduce(st, _tool_result("Bash", "ok"))
    reduce(st, ZfEvent(type="channel.agent.reply.completed", actor="d",
                       payload={"request_id": "R1"}))
    body = json.dumps(render_streaming_card(st), ensure_ascii=False)
    assert "4 个工具调用（已结束）" in body


# --- R4: size caps defeat the ~30KB per-element limit ------------------------

def test_oversized_tool_output_is_capped():
    st = new_stream_state("R1")
    reduce(st, _tool("Bash", {"command": "cat huge.log"}))
    reduce(st, _tool_result("Bash", "X" * 100_000))  # 100KB output
    card = render_streaming_card(st)
    # every element serializes well under Feishu's ~30KB per-element limit
    for el in card["body"]["elements"]:
        assert len(json.dumps(el, ensure_ascii=False)) < 30_000


def test_empty_terminal_refold_does_not_stomp_good_card(tmp_path, monkeypatch):
    """feishu e2e: streaming deltas live on the ephemeral LiveDeltaBus and
    terminal events carry no text. Once the bus rotates, a later re-fold folds
    a reply to an empty terminal state. sync_stream_card must NOT overwrite the
    already-rendered content card with the '（未返回内容）' empty render."""
    from zf.integrations.feishu import stream_card as sc

    (tmp_path / "events.jsonl").write_text("", encoding="utf-8")

    def _state(text: str, terminal: str):
        st = new_stream_state("R1")
        if text:
            reduce(st, _delta("text", text))
        st["terminal"] = terminal
        return st

    calls = {"sent": [], "updated": []}
    send = lambda card: (calls["sent"].append(card), "om_msg_1")[1]
    update = lambda mid, card, seq=0: calls["updated"].append((mid, card, seq))
    ledger: dict = {}

    # round 1: reply streamed real content -> card sent with content
    monkeypatch.setattr(sc, "_fold_stream_states",
                        lambda events, member="": {"R1": _state("最终 PRD v0.2 ...", "done")})
    sc.sync_stream_card(tmp_path, send_card=send, update_card=update, ledger=ledger)
    assert len(calls["sent"]) == 1
    assert ledger["stream-R1"]["had_content"] is True

    # round 2: bus rotated -> same reply now folds to an EMPTY terminal state
    monkeypatch.setattr(sc, "_fold_stream_states",
                        lambda events, member="": {"R1": _state("", "done")})
    sc.sync_stream_card(tmp_path, send_card=send, update_card=update, ledger=ledger)
    # guard: the good card must NOT be stomped to （未返回内容）
    assert calls["updated"] == []


def test_empty_terminal_first_send_is_skipped(tmp_path, monkeypatch):
    """A reply that folds straight to an empty terminal state (bus already
    rotated before the first push, e.g. after a ledger reset) must NOT be sent
    as a fresh '（未返回内容）' card — there is nothing to show."""
    from zf.integrations.feishu import stream_card as sc

    (tmp_path / "events.jsonl").write_text("", encoding="utf-8")
    st = new_stream_state("R9"); st["terminal"] = "done"  # empty + terminal

    calls = {"sent": []}
    send = lambda card: (calls["sent"].append(card), "om_x")[1]
    update = lambda *a, **k: None
    monkeypatch.setattr(sc, "_fold_stream_states",
                        lambda events, member="": {"R9": st})
    out = sc.sync_stream_card(tmp_path, send_card=send, update_card=update, ledger={})
    assert calls["sent"] == []
    assert out["sent"] == []


def test_running_empty_reply_still_gets_thinking_card(tmp_path, monkeypatch):
    """A still-running reply with no content yet must keep its thinking card —
    the empty-terminal skip must not swallow the live spinner."""
    from zf.integrations.feishu import stream_card as sc

    (tmp_path / "events.jsonl").write_text("", encoding="utf-8")
    st = new_stream_state("R8"); st["footer"] = "thinking"  # running, no content

    sent = []
    monkeypatch.setattr(sc, "_fold_stream_states",
                        lambda events, member="": {"R8": st})
    sc.sync_stream_card(tmp_path, send_card=lambda c: (sent.append(c), "om")[1],
                        update_card=lambda *a, **k: None, ledger={})
    assert len(sent) == 1
