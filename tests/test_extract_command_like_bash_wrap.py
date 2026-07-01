"""Test _extract_command_like quote-balance fix (#A cangjie 2026-05-21).

When verification text is `bash -lc 'cmd'` wrapped (agent self-fix
attempt to avoid PATH= sanitizer), the regex match starts from inside
the wrap (at `PATH=...` or the command keyword), strip-cuts `bash -lc '`
prefix but leaves a trailing unmatched quote → ContractD `sh -n -c`
fails "Unterminated quoted string".

Fix: detect trailing unmatched quote(s) and trim. Don't trim balanced
quotes (e.g. `pytest 'foo bar.test'` — quote count even).

Cangjie evidence: incidents/2026-05-21-bug-A-path-sanitizer.md
                  (TASK-P0V01 + TASK-P0V04 ContractD reject loop)
"""

from __future__ import annotations

import subprocess

import pytest

from zf.runtime.housekeeping import _extract_command_like


def _sh_n_pass(command: str) -> bool:
    """sh -n -c <command> returns 0 (syntax OK)."""
    if not command:
        return False
    proc = subprocess.run(
        ["sh", "-n", "-c", command],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return proc.returncode == 0


# ─── Cangjie real-world reproducers ──────────────────────────────────────


def test_cangjie_p0v01_bash_lc_wrap():
    """TASK-P0V01: bash -lc 'PATH=...:$PATH pnpm install --frozen-lockfile'.

    Before fix: returns `PATH=...:$PATH pnpm install --frozen-lockfile'`
    (trailing unmatched quote) → ContractD sh -n exit 2.
    After fix: trailing quote trimmed, sh -n passes.
    """
    text = (
        "bash -lc 'PATH=/path/to/node/bin:$PATH "
        "pnpm install --frozen-lockfile'"
    )
    result = _extract_command_like(text)
    assert not result.endswith("'"), f"trailing quote leaked: {result!r}"
    assert _sh_n_pass(result), f"sh -n still fails on: {result!r}"


def test_cangjie_p0v04_bash_lc_wrap_vitest():
    """TASK-P0V04: bash -lc 'pnpm vitest run test/unit/...'."""
    text = "bash -lc 'pnpm vitest run test/unit/p0-scaffold/vitest.test.ts'"
    result = _extract_command_like(text)
    assert not result.endswith("'"), f"trailing quote leaked: {result!r}"
    assert _sh_n_pass(result)


def test_double_quote_wrap_trimmed():
    """bash -lc \"pnpm test --watch=false\" → strip trailing \"."""
    text = 'bash -lc "pnpm test --watch=false"'
    result = _extract_command_like(text)
    assert not result.endswith('"'), f"trailing dquote leaked: {result!r}"
    assert _sh_n_pass(result)


# ─── Non-regressions: balanced quotes preserved ──────────────────────────


def test_plain_command_unchanged():
    """No wrap — return command as-is."""
    text = "PATH=/home/.local/bin:$PATH pnpm install --frozen-lockfile"
    result = _extract_command_like(text)
    assert result == text
    assert _sh_n_pass(result)


def test_env_plus_absolute_python_command_preserved():
    """Do not truncate `PYTHONPATH=... /abs/path/python ...` to bare python."""
    text = (
        "PYTHONPATH=src /path/to/zaofu/.venv/bin/python "
        "-m pytest tests/test_zf_e2e_textkit.py -q"
    )
    result = _extract_command_like(text)
    assert result == text
    assert _sh_n_pass(result)


def test_prose_with_command_extracted():
    """Prose containing command — extract just the command."""
    text = "passes when pnpm test succeeds in 30s"
    result = _extract_command_like(text)
    assert result == "pnpm test"


def test_balanced_inner_quote_preserved():
    """Quote count even (balanced) — don't trim trailing quote."""
    # `pytest 'foo bar.test'` — 2 quotes, balanced
    text = "pytest 'foo bar.test'"
    result = _extract_command_like(text)
    # Both quotes preserved (even count)
    assert result.count("'") == 2
    assert _sh_n_pass(result)


def test_no_command_returns_empty():
    """No recognized command keyword — return empty."""
    text = "just some prose without any command keyword"
    result = _extract_command_like(text)
    assert result == ""
