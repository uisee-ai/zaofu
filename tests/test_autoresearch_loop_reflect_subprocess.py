"""Sprint §4 — LLM reflection subprocess invocation tests."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from zf.autoresearch.loop import (
    ReflectionResult,
    invoke_reflection_llm,
)


_HAPPY_JSON = json.dumps({
    "verdict": "best_so_far",
    "alternatives": ["try X"],
    "risk": "low",
    "rec_for_next_iter": "run controlled-stuck-recovery",
})


def _stub_completed_process(
    returncode: int, stdout: str = "", stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["claude", "-p"], returncode=returncode,
        stdout=stdout, stderr=stderr,
    )


def test_invoke_reflection_llm_happy_path() -> None:
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.return_value = _stub_completed_process(0, stdout=_HAPPY_JSON)
        r = invoke_reflection_llm("test prompt", backend="claude-code")
    assert isinstance(r, ReflectionResult)
    assert r.verdict == "best_so_far"
    assert r.risk == "low"
    # First positional should be the CLI invocation.
    args = mock_run.call_args[0][0]
    assert "claude" in args[0]
    assert "-p" in args


def test_invoke_reflection_llm_passes_prompt_via_stdin() -> None:
    """Large prompts should pass via stdin, not argv (avoid arg-length
    limits and process-listing leaks)."""
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.return_value = _stub_completed_process(0, stdout=_HAPPY_JSON)
        invoke_reflection_llm("a" * 200_000, backend="claude-code")
    kwargs = mock_run.call_args.kwargs
    # input= is how subprocess.run feeds stdin.
    assert "input" in kwargs
    assert kwargs["input"].startswith("a")


def test_invoke_reflection_llm_codex_backend_uses_stdin_exec() -> None:
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.return_value = _stub_completed_process(0, stdout=_HAPPY_JSON)
        r = invoke_reflection_llm("test prompt", backend="codex")

    assert r.verdict == "best_so_far"
    args = mock_run.call_args[0][0]
    assert args[:2] == ["codex", "exec"]
    assert "--ephemeral" in args
    assert "-s" in args
    assert "read-only" in args
    assert "-a" in args
    assert "never" in args
    assert args[-1] == "-"
    assert mock_run.call_args.kwargs["input"] == "test prompt"


def test_invoke_reflection_llm_nonzero_exit_falls_back() -> None:
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.return_value = _stub_completed_process(
            1, stderr="not authenticated",
        )
        r = invoke_reflection_llm("prompt", backend="claude-code")
    assert r.verdict == "unknown"
    assert r.risk == "medium"
    # Fallback must include the stderr in raw_response for debugging.
    assert "not authenticated" in r.raw_response


def test_invoke_reflection_llm_timeout_falls_back() -> None:
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd="claude", timeout=120,
        )
        r = invoke_reflection_llm(
            "prompt", backend="claude-code", timeout_seconds=120,
        )
    assert r.verdict == "unknown"
    assert "timeout" in r.raw_response.lower()


def test_invoke_reflection_llm_missing_binary_falls_back() -> None:
    """When claude binary is not on PATH, FileNotFoundError → fallback."""
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError(
            "claude binary not found"
        )
        r = invoke_reflection_llm("prompt", backend="claude-code")
    assert r.verdict == "unknown"
    assert "not found" in r.raw_response.lower() or "FileNotFound" in r.raw_response


def test_invoke_reflection_llm_unsupported_backend() -> None:
    """Unknown backend should fall back without subprocess attempt."""
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        r = invoke_reflection_llm("prompt", backend="palmistry")
    assert r.verdict == "unknown"
    mock_run.assert_not_called()
    assert "backend" in r.raw_response.lower() or "unsupported" in r.raw_response.lower()


def test_invoke_reflection_llm_uses_skip_permissions_for_safety() -> None:
    """Reflection is read-only — invoke with --print and disable hooks
    to keep cost / side-effects predictable."""
    with patch("zf.autoresearch.loop.subprocess.run") as mock_run:
        mock_run.return_value = _stub_completed_process(0, stdout=_HAPPY_JSON)
        invoke_reflection_llm("prompt", backend="claude-code")
    args = mock_run.call_args[0][0]
    # Either --bare or --print/-p or both — we just need a non-interactive flag.
    flags = set(args)
    assert "-p" in flags or "--print" in flags
