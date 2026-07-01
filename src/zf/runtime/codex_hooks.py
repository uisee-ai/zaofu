"""Codex project hook rendering + deterministic trust-hash helpers."""

from __future__ import annotations

import hashlib
import json
import shlex
from pathlib import Path

from zf.runtime.cli_command import zf_cli_cmd


# P1-CODEX-HOOK-TRUST dual-mechanism split (F3 decision B, 2026-05-29):
#   1. backend.py adds `--dangerously-bypass-hook-trust` on codex spawn — the
#      PRIMARY fix. It runs hooks without persisted trust and is robust to the
#      hash-algorithm drift risk below.
#   2. `codex_hook_hash` (this module) + `_install_codex_project_hook_trust`
#      (spawn_coordinator) pre-seed CODEX_HOME/config.toml as defense-in-depth
#      for codex paths/versions where the bypass flag may not apply.
# Mechanism 2 replicates codex_rs internals and is therefore VERSION-COUPLED:
# it was verified byte-exact against the codex version pinned below. The
# `test_codex_version_matches_hash_baseline` sensor fails when a running codex
# drifts from this baseline, so the hash gets re-verified instead of silently
# mismatching in production (the silent-drift guard the static vectors lack).
# Removing mechanism 2 (option A) is deferred until the bypass flag is
# confirmed sufficient across all codex spawn modes.
CODEX_HASH_VERIFIED_VERSION = "0.142"


CODEX_HOOK_EVENTS: tuple[tuple[str, str], ...] = (
    # (Codex engine event name, zaofu event type namespace)
    ("SessionStart", "codex.hook.session_start"),
    ("UserPromptSubmit", "codex.hook.user_prompt_submit"),
    ("PreToolUse", "codex.hook.pre_tool_use"),
    ("PostToolUse", "codex.hook.post_tool_use"),
    ("Stop", "codex.hook.stop"),
)

# Codex events whose `matcher` field is meaningful and therefore retained in the
# normalized trust identity. Mirrors codex_rs `HOOK_EVENT_NAMES_WITH_MATCHERS`
# (hooks/src/lib.rs): only these keep the matcher; for the rest Codex drops it
# (matcher_pattern_for_event -> None), which changes the trust hash.
_CODEX_MATCHER_EVENTS: frozenset[str] = frozenset({
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "SessionStart",
    "SubagentStart",
    "SubagentStop",
})


def _hook_command(state_dir: Path, zf_event: str) -> str:
    """The exact `zf hook-recv` command string written into hooks.json.

    Shared by the renderer and the trust-hash computation so they can never
    drift — the trust hash is taken over this exact string.
    """
    hook_base = (
        f"{zf_cli_cmd()} hook-recv --state-dir "
        f"{shlex.quote(str(state_dir))} --backend codex"
    )
    return f"{hook_base} --event {zf_event}"


def write_codex_hook_settings(
    state_dir: Path,
    *,
    project_root: Path | None = None,
) -> None:
    """Render ``<project>/.codex/hooks.json`` for Codex's hook engine."""
    project_root = (project_root or state_dir.parent).resolve()
    codex_dir = project_root / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)

    hooks: dict[str, list[dict]] = {}
    for engine_name, zf_event in CODEX_HOOK_EVENTS:
        hooks[engine_name] = [{
            "matcher": "",
            "hooks": [{
                "type": "command",
                "command": _hook_command(state_dir, zf_event),
            }],
        }]

    (codex_dir / "hooks.json").write_text(
        json.dumps({"hooks": hooks}, indent=2),
        encoding="utf-8",
    )


def _codex_event_label(zf_event: str) -> str:
    """`codex.hook.session_start` -> `session_start` (the codex key label)."""
    return zf_event.rsplit(".", 1)[-1]


def codex_hook_hash(state_dir: Path, engine_name: str, zf_event: str) -> str:
    """Compute Codex's per-hook ``currentHash`` for a zaofu-rendered hook.

    Replicates codex_rs `command_hook_hash` + `version_for_toml`
    (hooks/src/engine/discovery.rs, config/src/fingerprint.rs): the hash is
    ``sha256`` over the canonical-JSON of a *normalized hook identity* — the
    TOML round-trip drops ``None`` fields (no `command_windows` /
    `status_message`), keeps the default ``timeout`` of 600 and ``async`` of
    false, and retains ``matcher`` only for matcher-bearing events. Verified
    byte-exact against 10 real codex-written hashes (cj-mono + cangjie-mono).
    """
    identity: dict = {
        "event_name": _codex_event_label(zf_event),
        "hooks": [{
            "type": "command",
            "command": _hook_command(state_dir, zf_event),
            "timeout": 600,
            "async": False,
        }],
    }
    if engine_name in _CODEX_MATCHER_EVENTS:
        identity["matcher"] = ""
    blob = json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def codex_hook_trust_states(
    state_dir: Path,
    *project_roots: Path,
) -> list[tuple[str, str]]:
    """Return ``(hook_state_key, trusted_hash)`` for every zaofu hook.

    Lets the kernel pre-seed ``CODEX_HOME/config.toml`` so Codex trusts the
    zaofu-rendered hooks without the interactive ``/hooks`` review — replacing
    the codex 0.133 ``app-server hooks/list`` RPC, which no longer responds.

    A key is emitted for each candidate ``project_root`` because Codex resolves
    project hooks by walking parent dirs from the worker cwd: a nested worktree
    can match either its own ``.codex/hooks.json`` or the ancestor project's.
    Writing both keys (same path-independent hash) covers both resolutions.
    """
    roots: list[Path] = []
    for root in project_roots:
        resolved = Path(root).resolve()
        if resolved not in roots:
            roots.append(resolved)

    states: list[tuple[str, str]] = []
    for root in roots:
        source_path = root / ".codex" / "hooks.json"
        for engine_name, zf_event in CODEX_HOOK_EVENTS:
            label = _codex_event_label(zf_event)
            # group_index/handler_index are always 0:0 — one matcher group with
            # one handler per event (codex_rs hook_key positional suffix).
            key = f"{source_path}:{label}:0:0"
            states.append((key, codex_hook_hash(state_dir, engine_name, zf_event)))
    return states
