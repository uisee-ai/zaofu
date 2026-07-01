"""Wire-up proof for 1202 Codex hook closure.

Per CLAUDE.md "Library-Without-Callers" rule: every new component must
be demonstrably imported by orchestrator.py / start.py / cli/*.py so it
actually participates in the runtime. This file grep-greens three
anchors that prove 1202-T1/T2/T3 are wired, not dead code.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "zf"


def _read(path: Path) -> str:
    return path.read_text()


def test_write_codex_hook_settings_is_called_from_start():
    """T1: `_write_codex_hook_settings` must be invoked by start.py run(),
    not just defined."""
    start = _read(SRC / "cli" / "start.py")
    assert "_write_codex_hook_settings" in start, \
        "function missing from start.py"
    # At least two occurrences: definition + call-site
    assert start.count("_write_codex_hook_settings") >= 2, \
        "_write_codex_hook_settings defined but never called from start.run()"


def test_codex_adapter_enables_feature_flag():
    """T1 step 3: CodexAdapter must enable current Codex hooks."""
    backend = _read(SRC / "runtime" / "backend.py")
    assert '"--enable", "hooks"' in backend, \
        "CodexAdapter must enable the Codex hooks feature"


def test_hook_recv_accepts_backend_flag():
    """T1 step 1 (hooks.json command line uses `--backend codex`).
    hook_recv must accept the flag or the codex-side hook will fail
    on argparse before it ever emits the event.
    """
    hook_recv = _read(SRC / "cli" / "hook_recv.py")
    assert '"--backend"' in hook_recv, \
        "hook_recv must declare --backend argument"


def test_hook_recv_extracts_codex_specific_fields():
    """T2: codex.hook.* payload carries turn_id / transcript_path /
    permission_mode. Verify extraction branch exists."""
    hook_recv = _read(SRC / "cli" / "hook_recv.py")
    assert "codex.hook." in hook_recv, \
        "hook_recv must branch on codex.hook.* namespace"
    # All three codex-specific fields present in the source
    for field in ("turn_id", "transcript_path", "permission_mode"):
        assert field in hook_recv, f"{field} extraction missing in hook_recv.py"


def test_reactor_registers_codex_hook_handlers():
    """T3: all five codex.hook.* handlers registered in _BUILTIN_HANDLER_METHODS."""
    reactor = _read(SRC / "runtime" / "orchestrator_reactor.py")
    # Parse the tuple to count entries
    tree = ast.parse(reactor)
    registered: set[str] = set()

    def _collect(value: ast.AST) -> None:
        if isinstance(value, ast.Tuple):
            for item in value.elts:
                if isinstance(item, ast.Tuple) and len(item.elts) == 2:
                    evt = item.elts[0]
                    if isinstance(evt, ast.Constant):
                        registered.add(evt.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_BUILTIN_HANDLER_METHODS":
                    _collect(node.value)
        elif isinstance(node, ast.AnnAssign):
            tgt = node.target
            if (isinstance(tgt, ast.Name) and tgt.id == "_BUILTIN_HANDLER_METHODS"
                    and node.value is not None):
                _collect(node.value)
    expected = {
        "codex.hook.session_start",
        "codex.hook.user_prompt_submit",
        "codex.hook.pre_tool_use",
        "codex.hook.post_tool_use",
        "codex.hook.stop",
    }
    missing = expected - registered
    assert not missing, f"_BUILTIN_HANDLER_METHODS missing: {sorted(missing)}"


def test_codex_hook_events_are_in_wake_patterns():
    """T3: each registered codex.hook.* handler must also wake run_once."""
    wake = _read(SRC / "runtime" / "wake_patterns.py")
    for evt in (
        "codex.hook.session_start",
        "codex.hook.user_prompt_submit",
        "codex.hook.pre_tool_use",
        "codex.hook.post_tool_use",
        "codex.hook.stop",
    ):
        assert f'"{evt}"' in wake, f"{evt} missing from WAKE_PATTERNS"
