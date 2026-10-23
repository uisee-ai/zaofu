"""Claude SDK options shared by stream transport launches and resumes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig

_CLAUDE_SDK_PERMISSION_MODE = "bypassPermissions"


def build_claude_stream_options(
    session_id: str, role: RoleConfig, *, cwd: Path, max_turns: int,
    is_resume: bool = False, env: dict[str, str] | None = None,
) -> Any:
    option_cwd = cwd
    try:
        from claude_code_sdk import ClaudeCodeOptions  # type: ignore
    except ImportError:
        class _Stub:
            pass
        stub = _Stub()
        stub.permission_mode = _CLAUDE_SDK_PERMISSION_MODE  # type: ignore[attr-defined]
        stub.allowed_tools = list(role.allowed_tools)  # type: ignore[attr-defined]
        stub.disallowed_tools = (  # type: ignore[attr-defined]
            ["Agent", "Task"] if role.provider_session is not None
            and role.provider_session.native_delegation is False else []
        )
        stub.cwd = str(option_cwd)  # type: ignore[attr-defined]
        stub.model = (  # type: ignore[attr-defined]
            role.model if role.model and role.model != "placeholder" else None
        )
        stub.max_turns = max_turns  # type: ignore[attr-defined]
        stub.env = dict(env or {})  # type: ignore[attr-defined]
        stub.extra_args = {}  # type: ignore[attr-defined]
        if is_resume:
            stub.resume = session_id  # type: ignore[attr-defined]
        else:
            stub.session_id = session_id  # type: ignore[attr-defined]
            stub.extra_args = {"session-id": session_id}  # type: ignore[attr-defined]
        return stub

    opts: dict = dict(
        permission_mode=_CLAUDE_SDK_PERMISSION_MODE,
        allowed_tools=list(role.allowed_tools),
        cwd=str(option_cwd),
        model=role.model if role.model and role.model != "placeholder" else None,
        # B9: without max_turns, Claude CLI defaults to a low cap that
        # ends the stream right after the first tool call (observed:
        # Layer 2 reads briefing, gets tool_result, then stop with no
        # follow-up thinking). Default 30 is generous enough for one
        # wake to decompose a feature, set N contracts, and dispatch.
        max_turns=max_turns,
        env=dict(env or {}),
    )
    if is_resume:
        opts["resume"] = session_id
    else:
        opts["extra_args"] = {"session-id": session_id}
    if role.provider_session is not None and role.provider_session.native_delegation is False:
        opts["disallowed_tools"] = ["Agent", "Task"]
    return ClaudeCodeOptions(**opts)
