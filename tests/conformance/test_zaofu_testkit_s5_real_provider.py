"""Explicit real-provider lifecycle canaries for the S5 provider boundary."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from zf.web.headless_agent import CodexHeadlessBackend, HeadlessMessage, HeadlessTurnResult
from zf.web.headless_agent import ClaudeHeadlessBackend
from zf.core.config.schema import RoleConfig
from zf.runtime.backend import CodexAdapter


RUN_REAL_CODEX = os.environ.get("ZF_RUN_REAL_CODEX_CANARY", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
RUN_REAL_CLAUDE = os.environ.get("ZF_RUN_REAL_CLAUDE_CANARY", "").strip().lower() in {
    "1",
    "true",
    "yes",
}


def _require_real_codex() -> None:
    if not RUN_REAL_CODEX:
        pytest.skip("set ZF_RUN_REAL_CODEX_CANARY=1 for the real Codex tier")
    if shutil.which("codex") is None:
        pytest.skip("Codex CLI is unavailable")


def _real_claude_command() -> str:
    """Return an explicit Claude command without relying on a stale user shim."""

    command = os.environ.get("ZF_REAL_CLAUDE_COMMAND", "claude").strip()
    parts = shlex.split(command)
    if not parts:
        pytest.skip("ZF_REAL_CLAUDE_COMMAND is empty")
    executable = parts[0]
    if Path(executable).is_absolute():
        available = Path(executable).is_file() and os.access(executable, os.X_OK)
    else:
        available = shutil.which(executable) is not None
    if not available:
        pytest.skip("Claude CLI is unavailable; set ZF_REAL_CLAUDE_COMMAND to an executable")
    return command


def _require_real_claude() -> str:
    if not RUN_REAL_CLAUDE:
        pytest.skip("set ZF_RUN_REAL_CLAUDE_CANARY=1 for the real Claude tier")
    return _real_claude_command()


def _direct_codex_app_server_children() -> set[str]:
    result = subprocess.run(
        ["ps", "-o", "pid=,args=", "--ppid", str(os.getpid())],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if "codex" in line.lower() and "app-server" in line.lower()
    }


def _run_turn(
    backend: CodexHeadlessBackend,
    *,
    root: Path,
    marker: str,
    provider_session_id: str = "",
    timeout_s: float = 90,
    messages: list[HeadlessMessage] | None = None,
    session_ids: list[str] | None = None,
) -> HeadlessTurnResult:
    transcript = messages if messages is not None else []
    observed_sessions = session_ids if session_ids is not None else []
    return backend.run_turn(
        prompt=f"Reply with exactly {marker} and do not call tools or access files.",
        cwd=root,
        system_prompt=(
            "Read-only ZaoFu provider lifecycle canary. Do not call tools, read "
            "files, write files, or perform additional network actions."
        ),
        thread_id="zf-real-codex-canary-thread",
        provider_session_id=provider_session_id,
        on_session_id=observed_sessions.append,
        on_message=transcript.append,
        timeout_s=timeout_s,
        thinking_level="low",
        run_id="zf-real-codex-canary",
        run_thread_id="zf-real-codex-canary-thread",
        project_id="zf-real-codex-canary",
        conversation_id="zf-real-codex-canary-conversation",
        permission_profile="dangerous_full",
    )


def _assert_completed_marker(result: HeadlessTurnResult, marker: str) -> None:
    if not result.ok:
        error = (result.error or "").lower()
        categories = tuple(
            category
            for category, needles in (
                ("timeout", ("timed out",)),
                ("quota", ("usage limit", "quota")),
                ("capacity", ("model is at capacity",)),
                ("sandbox", ("sandbox", "unshare", "bubblewrap")),
                ("auth", ("unauthorized", "authentication", "authenticate", "login")),
                ("rate_limit", ("rate limit", "too many requests")),
                ("stream_disconnect", ("stream disconnected",)),
            )
            if any(needle in error for needle in needles)
        )
        category = categories[0] if categories else "provider_error"
        pytest.fail(
            f"real Codex provider did not complete: status={result.status}; "
            f"error_category={category}"
        )
    assert result.ok is True
    assert result.status == "completed"
    assert result.backend == "codex-headless"
    assert result.provider_session_id
    assert result.reply.strip() == marker
    assert result.incomplete is False
    assert result.completion_reason == "completed"
    assert result.error == ""
    assert result.fallback_reason == ""
    assert result.usage
    assert all(not message.tool for message in result.messages)


def _assert_completed_claude_marker(
    result: HeadlessTurnResult,
    marker: str,
) -> None:
    if not result.ok:
        error = (result.error or "").lower()
        categories = tuple(
            category
            for category, needles in (
                ("timeout", ("timed out",)),
                ("quota", ("usage limit", "quota")),
                ("auth", ("unauthorized", "authentication", "authenticate", "login")),
                ("rate_limit", ("rate limit", "too many requests")),
                ("stream_disconnect", ("stream disconnected",)),
            )
            if any(needle in error for needle in needles)
        )
        category = categories[0] if categories else "provider_error"
        pytest.fail(
            f"real Claude provider did not complete: status={result.status}; "
            f"error_category={category}"
        )
    assert result.ok is True
    assert result.status == "completed"
    assert result.backend == "claude-headless"
    assert result.provider_session_id
    assert result.reply.strip() == marker
    assert result.incomplete is False
    assert result.completion_reason == "end_turn"
    assert result.error == ""
    assert result.usage
    assert all(not message.tool for message in result.messages)


def _direct_claude_children() -> set[str]:
    result = subprocess.run(
        ["ps", "-o", "pid=,args=", "--ppid", str(os.getpid())],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if "claude" in line.lower()
    }


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_real_codex_start_resume_transcript_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start Codex, resume its durable thread and leave no local side effect."""

    _require_real_codex()
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX",
        "danger-full-access",
    )
    monkeypatch.setenv("ZF_KANBAN_AGENT_CODEX_HEADLESS_SKIP_SANDBOX_PREFLIGHT", "1")
    root = tmp_path / "workspace"
    root.mkdir()
    children_before = _direct_codex_app_server_children()

    backend = CodexHeadlessBackend(tool_timeout_s=60)
    first_messages: list[HeadlessMessage] = []
    first_session_ids: list[str] = []
    try:
        first = _run_turn(
            backend,
            root=root,
            marker="ZF_PROVIDER_CANARY_OK",
            messages=first_messages,
            session_ids=first_session_ids,
        )
        _assert_completed_marker(first, "ZF_PROVIDER_CANARY_OK")
        assert first.resumed is False
        assert first_session_ids == [first.provider_session_id]
        assert any(message.type == "text" for message in first_messages)
        assert not any(message.type in {"tool_use", "tool_result"} for message in first_messages)

        second_messages: list[HeadlessMessage] = []
        second_session_ids: list[str] = []
        second = _run_turn(
            backend,
            root=root,
            marker="ZF_PROVIDER_RESUME_OK",
            provider_session_id=first.provider_session_id,
            messages=second_messages,
            session_ids=second_session_ids,
        )
        _assert_completed_marker(second, "ZF_PROVIDER_RESUME_OK")
        assert second.resumed is True
        assert second.provider_session_id == first.provider_session_id
        assert second_session_ids == [first.provider_session_id]
        assert any(message.type == "text" for message in second_messages)
        assert not any(message.type in {"tool_use", "tool_result"} for message in second_messages)
    finally:
        assert list(root.iterdir()) == []
        assert _direct_codex_app_server_children() == children_before


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_real_codex_timeout_is_fail_closed_and_cleans_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real app-server request timeout must be classified and cleaned up."""

    _require_real_codex()
    monkeypatch.setenv(
        "ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX",
        "danger-full-access",
    )
    monkeypatch.setenv("ZF_KANBAN_AGENT_CODEX_HEADLESS_SKIP_SANDBOX_PREFLIGHT", "1")
    root = tmp_path / "workspace"
    root.mkdir()
    children_before = _direct_codex_app_server_children()

    result = _run_turn(
        CodexHeadlessBackend(tool_timeout_s=0.001),
        root=root,
        marker="ZF_PROVIDER_TIMEOUT_MUST_NOT_COMPLETE",
        timeout_s=0.001,
    )

    assert result.ok is False
    assert result.status == "timeout"
    assert result.incomplete is False
    assert "timed out" in result.error.lower()
    assert list(root.iterdir()) == []
    assert _direct_codex_app_server_children() == children_before


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_codex_restricted_spawn_arguments_are_accepted_by_installed_cli() -> None:
    """Argv compatibility only; role-local policy/hook host tests prove preparation."""

    _require_real_codex()
    command = CodexAdapter().build_command(
        RoleConfig(name="delivery_worker", permission_mode="restricted")
    )
    result = subprocess.run(
        [*command, "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, (
        "Codex rejected ZaoFu's generated restricted-worker argv: "
        f"exit={result.returncode}; stderr={result.stderr[-600:]}"
    )


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_real_claude_start_resume_transcript_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start Claude, resume its durable session and leave no workdir side effect."""

    command = _require_real_claude()
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", command)
    monkeypatch.setenv("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_TOOLS", "default")
    root = tmp_path / "workspace"
    root.mkdir()
    children_before = _direct_claude_children()
    backend = ClaudeHeadlessBackend(command=command, max_turns=2)

    first_messages: list[HeadlessMessage] = []
    first_session_ids: list[str] = []
    first = backend.run_turn(
        prompt="Reply with exactly ZF_CLAUDE_CANARY_OK and do not call tools or access files.",
        cwd=root,
        system_prompt=(
            "Read-only ZaoFu provider lifecycle canary. Do not call tools, read "
            "files, write files, or perform additional network actions."
        ),
        thread_id="8be9f729-9ce9-4258-8f0a-bfdc101d6e3a",
        provider_session_id="",
        on_session_id=first_session_ids.append,
        on_message=first_messages.append,
        timeout_s=90,
        thinking_level="low",
        run_id="zf-real-claude-canary",
        run_thread_id="zf-real-claude-canary-thread",
        project_id="zf-real-claude-canary",
        conversation_id="zf-real-claude-canary-conversation",
        permission_profile="read_only",
    )
    _assert_completed_claude_marker(first, "ZF_CLAUDE_CANARY_OK")
    assert first.resumed is False
    assert first_session_ids and set(first_session_ids) == {first.provider_session_id}
    assert any(message.type == "text" for message in first_messages)

    second_messages: list[HeadlessMessage] = []
    second_session_ids: list[str] = []
    second = backend.run_turn(
        prompt="Reply with exactly ZF_CLAUDE_RESUME_OK and do not call tools or access files.",
        cwd=root,
        system_prompt=(
            "Read-only ZaoFu provider lifecycle canary. Do not call tools, read "
            "files, write files, or perform additional network actions."
        ),
        thread_id="8be9f729-9ce9-4258-8f0a-bfdc101d6e3a",
        provider_session_id=first.provider_session_id,
        on_session_id=second_session_ids.append,
        on_message=second_messages.append,
        timeout_s=90,
        thinking_level="low",
        run_id="zf-real-claude-canary",
        run_thread_id="zf-real-claude-canary-thread",
        project_id="zf-real-claude-canary",
        conversation_id="zf-real-claude-canary-conversation",
        permission_profile="read_only",
    )
    _assert_completed_claude_marker(second, "ZF_CLAUDE_RESUME_OK")
    assert second.resumed is True
    assert second.provider_session_id == first.provider_session_id
    assert second_session_ids and set(second_session_ids) == {first.provider_session_id}
    assert any(message.type == "text" for message in second_messages)
    assert not any(message.tool for message in first_messages + second_messages)
    assert list(root.iterdir()) == []
    assert _direct_claude_children() == children_before


@pytest.mark.host
@pytest.mark.real_provider
@pytest.mark.serial
def test_real_claude_timeout_is_fail_closed_and_cleans_process(
    tmp_path: Path,
) -> None:
    """A real Claude request timeout must not be reported as a successful turn."""

    command = _require_real_claude()
    root = tmp_path / "workspace"
    root.mkdir()
    children_before = _direct_claude_children()
    result = ClaudeHeadlessBackend(command=command, max_turns=2).run_turn(
        prompt="Reply with exactly ZF_CLAUDE_TIMEOUT_MUST_NOT_COMPLETE.",
        cwd=root,
        system_prompt="Do not call tools.",
        thread_id="9fe980d6-3b99-462f-a25f-a6cf33a0cd09",
        provider_session_id="",
        on_session_id=lambda _session_id: None,
        on_message=None,
        timeout_s=0.001,
        thinking_level="low",
        run_id="zf-real-claude-timeout",
        run_thread_id="zf-real-claude-timeout-thread",
        project_id="zf-real-claude-timeout",
        conversation_id="zf-real-claude-timeout-conversation",
        permission_profile="read_only",
    )
    assert result.ok is False
    assert result.status == "timeout"
    assert result.incomplete is False
    assert "timed out" in result.error.lower()
    assert list(root.iterdir()) == []
    assert _direct_claude_children() == children_before
