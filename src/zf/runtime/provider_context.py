"""Provider-output helpers shared by lifecycle and transports."""

from __future__ import annotations

import re

_ANSI_RE = re.compile(
    r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07"
)

_CONTEXT_EXHAUSTED_MARKERS = (
    "codex ran out of room in the model's context window",
    "start a new thread or clear earlier history before retrying",
)


def has_provider_context_exhausted(output: str) -> bool:
    normalized = " ".join(_ANSI_RE.sub("", output).lower().split())
    return any(marker in normalized for marker in _CONTEXT_EXHAUSTED_MARKERS)
