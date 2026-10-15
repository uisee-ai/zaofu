"""Project-scoped Codex context overrides; no changes to global CLI settings."""
from __future__ import annotations

import shlex
from typing import Any


def channel_codex_backend(context_window_tokens: int = 0) -> Any:
    from zf.web.headless_agent import CodexHeadlessBackend

    adapter = CodexHeadlessBackend()
    if context_window_tokens:
        adapter.command = shlex.join([
            *shlex.split(adapter.command),
            "-c", f"model_context_window={context_window_tokens}",
            "-c", f"model_auto_compact_token_limit={context_window_tokens * 9 // 10}",
        ])
    return adapter
