"""codex item→kind mapping: non-tool items must not render as tool panels
(rich-card noise fix, doc 98 §9). reasoning→thinking, user/agentMessage→skip,
real tools unchanged."""

from __future__ import annotations

from zf.web.headless_agent import _codex_item_message


def _item(item_type: str, method: str = "item/completed", **fields) -> dict:
    return {"method": f"codex/{method}",
            "params": {"item": {"type": item_type, **fields}}}


def test_reasoning_becomes_thinking_on_completed():
    msg = _codex_item_message(_item("reasoning", text="weighing the options"),
                              message_type="tool_result")
    assert msg is not None
    assert msg.type == "thinking" and "weighing" in msg.content


def test_reasoning_skipped_on_started():
    # only emit reasoning once (on completed) — not as a started tool_use
    assert _codex_item_message(_item("reasoning", method="item/started",
                                     text="x"), message_type="tool_use") is None


def test_user_and_agent_messages_are_not_tools():
    for t in ("userMessage", "agentMessage", "agentMessageDelta"):
        assert _codex_item_message(_item(t, text="hi"),
                                   message_type="tool_use") is None
        assert _codex_item_message(_item(t, text="hi"),
                                   message_type="tool_result") is None


def test_empty_reasoning_skipped():
    assert _codex_item_message(_item("reasoning", text="  "),
                               message_type="tool_result") is None


def test_real_tool_items_still_map_to_tool():
    cmd = _codex_item_message(_item("commandExecution", command="ls -la"),
                              message_type="tool_use")
    assert cmd is not None and cmd.type == "tool_use" and cmd.tool == "exec_command"
    assert cmd.input == {"command": "ls -la"}

    patch = _codex_item_message(_item("fileChange"), message_type="tool_result")
    assert patch is not None and patch.tool == "patch_apply"

    mcp = _codex_item_message(_item("mcpToolCall", name="search"),
                              message_type="tool_use")
    assert mcp is not None and mcp.tool == "search"
