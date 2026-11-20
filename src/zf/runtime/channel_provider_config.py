"""Project-scoped Codex context overrides; no changes to global CLI settings."""
from __future__ import annotations

import shlex
import json
from typing import Any


def channel_codex_backend(context_window_tokens: int = 0, *, model: str = "", reasoning_effort: str = "") -> Any:
    from zf.web.headless_agent import CodexHeadlessBackend

    adapter = CodexHeadlessBackend()
    args = shlex.split(adapter.command)
    if context_window_tokens:
        args.extend([
            "-c", f"model_context_window={context_window_tokens}",
            "-c", f"model_auto_compact_token_limit={context_window_tokens * 9 // 10}",
        ])
    if model:
        args.extend(["-c", f"model={json.dumps(model)}"])
    if reasoning_effort:
        args.extend(["-c", f"model_reasoning_effort={json.dumps(reasoning_effort)}"])
    if context_window_tokens or model or reasoning_effort:
        adapter.command = shlex.join(args)
    return adapter
