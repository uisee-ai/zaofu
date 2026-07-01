"""Rich tool-call rendering for Feishu stream cards (R2, doc 98 §9 / backlog 2026-06-22).

A tool call renders as a header line (icon + name + input summary) plus a body
(input by tool type + output in a code fence), with size caps that defeat
Feishu's ~30KB per-element limit — a single oversized element 400s and aborts
the whole card stream.

`tool` is a dict {name, input, output, status} (status: running|done|error).
"""

from __future__ import annotations

from typing import Any

HEADER_SUMMARY_MAX = 80
BODY_FIELD_MAX = 600
OUTPUT_MAX = 1200
# Last belt across the whole rendered body string: even with per-field caps, a
# pathological tool (many inputs + maxed output) can still stack past the limit.
BODY_TOTAL_MAX = 2500


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else f"{s[:n]}…"


def _pick(input_obj: Any, key: str, max_len: int = HEADER_SUMMARY_MAX) -> str:
    if not isinstance(input_obj, dict):
        return ""
    value = input_obj.get(key)
    if not isinstance(value, str):
        return ""
    one_line = " ".join(value.split()).strip()
    return _truncate(one_line, max_len)


def summarize_input(name: str, input_obj: Any) -> str:
    """One-line header summary, picked by tool type (Bash→command, Read→path, …)."""
    if name == "Bash":
        return _pick(input_obj, "command")
    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        return _pick(input_obj, "file_path")
    if name == "Grep":
        pattern = _pick(input_obj, "pattern", 40)
        path = _pick(input_obj, "path", 30)
        return f"{pattern} in {path}" if path else pattern
    if name == "Glob":
        return _pick(input_obj, "pattern")
    if name == "WebFetch":
        return _pick(input_obj, "url")
    if name == "WebSearch":
        return _pick(input_obj, "query", 60)
    if name in ("Agent", "Task"):
        return _pick(input_obj, "description") or _pick(input_obj, "subagent_type")
    return (_pick(input_obj, "command") or _pick(input_obj, "file_path")
            or _pick(input_obj, "path") or _pick(input_obj, "query"))


def tool_header(tool: dict[str, Any]) -> str:
    status = tool.get("status")
    icon = "✅" if status == "done" else "❌" if status == "error" else "⏳"
    name = str(tool.get("name") or "tool")
    summary = summarize_input(str(tool.get("name") or ""), tool.get("input"))
    return f"{icon} **{name}** — {summary}" if summary else f"{icon} **{name}**"


def _render_input(tool: dict[str, Any]) -> str:
    name = str(tool.get("name") or "")
    inp = tool.get("input")
    if not isinstance(inp, dict):
        return ""

    def s(key: str) -> str:
        return inp[key] if isinstance(inp.get(key), str) else ""

    if name == "Bash":
        cmd = s("command")
        return f"**Command**\n```bash\n{_truncate(cmd, BODY_FIELD_MAX)}\n```" if cmd else ""
    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        fp = s("file_path")
        return f"**File** `{fp}`" if fp else ""
    if name == "Grep":
        lines = []
        if s("pattern"):
            lines.append(f"**Pattern** `{s('pattern')}`")
        if s("path"):
            lines.append(f"**Path** `{s('path')}`")
        return "\n".join(lines)
    if name == "WebFetch":
        return f"**URL** {s('url')}" if s("url") else ""
    if name == "WebSearch":
        return f"**Query** `{_truncate(s('query'), BODY_FIELD_MAX)}`" if s("query") else ""
    return ""


def tool_body(tool: dict[str, Any]) -> str:
    parts: list[str] = []
    input_md = _render_input(tool)
    if input_md:
        parts.append(input_md)

    output = str(tool.get("output") or "")
    status = tool.get("status")
    if output:
        truncated = _truncate(output, OUTPUT_MAX)
        label = "Error" if status == "error" else "Output"
        parts.append(f"**{label}**\n```\n{truncated}\n```")
    elif status == "running":
        parts.append("_运行中…_")

    body = "\n\n".join(parts)
    if len(body) <= BODY_TOTAL_MAX:
        return body
    return f"{body[:BODY_TOTAL_MAX]}…\n\n_（body 已截断,完整内容查日志）_"
