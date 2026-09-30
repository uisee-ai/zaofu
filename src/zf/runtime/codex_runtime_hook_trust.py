"""Codex role-local TOML hook trust materialization used before spawn."""

from __future__ import annotations

import json
from pathlib import Path

from zf.core.state.atomic_io import atomic_write_text

_ZAOFU_CODEX_RUNTIME_BEGIN = "# BEGIN ZaoFu runtime Codex hook trust\n"
_ZAOFU_CODEX_RUNTIME_END = "# END ZaoFu runtime Codex hook trust\n"


def _toml_basic_string(value: str) -> str:
    """Return a TOML-compatible quoted string for simple runtime config."""
    return json.dumps(value, ensure_ascii=False)


def _strip_codex_runtime_hook_trust_block(text: str) -> str:
    start = text.find(_ZAOFU_CODEX_RUNTIME_BEGIN)
    end = text.find(_ZAOFU_CODEX_RUNTIME_END)
    if start == -1 or end == -1 or end < start:
        return text.rstrip() + ("\n" if text.strip() else "")
    end += len(_ZAOFU_CODEX_RUNTIME_END)
    stripped = (text[:start] + text[end:]).strip()
    return stripped + ("\n" if stripped else "")


def _write_codex_runtime_hook_trust(
    config_path: Path,
    *,
    project_root: Path,
    hook_states: list[tuple[str, str]],
    trust_project: bool = True,
) -> None:
    """Write per-runtime Codex project trust and hook hash state.

    The hook key is the stable key returned by Codex `hooks/list`, for example
    `/work/.codex/hooks.json:stop:0:0`. `trusted_hash` must match the
    corresponding `currentHash`; otherwise Codex keeps the hook untrusted.
    """
    try:
        current = config_path.read_text(encoding="utf-8")
    except OSError:
        current = ""
    base = _strip_codex_runtime_hook_trust_block(current)

    # The per-role config starts as a copy of the operator's global Codex
    # config.  That file may already contain trust tables for this project;
    # appending the generated block verbatim creates duplicate TOML table
    # definitions and Codex refuses to start the worker.  Remove only the
    # tables we are about to regenerate, preserving every unrelated setting.
    hook_keys = {key for key, _ in hook_states}
    if hook_keys:
        lines = base.splitlines(keepends=True)
        filtered: list[str] = []
        skipping = False
        for line in lines:
            if line.startswith("[hooks.state."):
                skipping = any(
                    line.rstrip("\r\n") == f'[hooks.state.{_toml_basic_string(key)}]'
                    for key in hook_keys
                )
                if skipping:
                    continue
            elif skipping and line.startswith("["):
                skipping = False
            if not skipping:
                filtered.append(line)
        base = "".join(filtered)

    project_key = f'[projects.{_toml_basic_string(str(project_root))}]'
    block_lines = [_ZAOFU_CODEX_RUNTIME_BEGIN.rstrip()]
    if trust_project and project_key not in base:
        block_lines.extend([
            project_key,
            'trust_level = "trusted"',
            "",
        ])
    for key, trusted_hash in hook_states:
        block_lines.extend([
            f"[hooks.state.{_toml_basic_string(key)}]",
            f"trusted_hash = {_toml_basic_string(trusted_hash)}",
            "",
        ])
    block_lines.append(_ZAOFU_CODEX_RUNTIME_END.rstrip())

    config_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(config_path, base + "\n".join(block_lines) + "\n")
