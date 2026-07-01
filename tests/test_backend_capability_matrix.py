"""ZF-TR-PROVIDER-CAP-001 (doc 39 §2.1.3) — backend capability matrix.

Each backend adapter declares a static ``BackendCapabilities``
descriptor so dispatch / recovery / doctor / Agent View can branch on
the same source instead of re-deriving capability per call site.
"""

from __future__ import annotations

import pytest

from zf.runtime.backend import (
    BackendCapabilities,
    ClaudeCodeAdapter,
    CodexAdapter,
    MockAdapter,
    get_adapter,
)
from zf.core.config.schema import RoleConfig
from zf.runtime.provider_capabilities import project_provider_capabilities


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


def test_backend_capabilities_is_frozen() -> None:
    caps = ClaudeCodeAdapter().capabilities
    with pytest.raises((AttributeError, TypeError)):
        caps.per_turn_hook = False  # type: ignore[misc]


def test_nested_agent_disable_uses_known_values() -> None:
    """Only ``full`` / ``partial`` / ``none`` are valid values."""
    valid = {"full", "partial", "none"}
    for adapter_cls in (ClaudeCodeAdapter, CodexAdapter, MockAdapter):
        caps = adapter_cls().capabilities
        assert caps.nested_agent_disable in valid, (
            f"{adapter_cls.__name__} nested_agent_disable="
            f"{caps.nested_agent_disable!r} not in {valid}"
        )


# ---------------------------------------------------------------------------
# Adapter-specific capability lock — these guard regressions if anyone
# silently flips a flag.
# ---------------------------------------------------------------------------


def test_claude_code_capabilities_locked() -> None:
    caps = ClaudeCodeAdapter().capabilities
    assert caps == BackendCapabilities(
        per_turn_hook=True,
        session_start_hook=True,
        native_resume=True,
        context_usage_reader=True,
        stream_json=True,
        hook_review_required=False,
        nested_agent_disable="full",
        native_compact=True,
        compact_command="/compact",
    )


def test_codex_capabilities_locked() -> None:
    """Codex differs from Claude on session_start_hook (no),
    hook_review_required (yes), nested_agent_disable (partial)."""
    caps = CodexAdapter().capabilities
    assert caps == BackendCapabilities(
        per_turn_hook=True,
        session_start_hook=False,
        native_resume=True,
        context_usage_reader=True,
        stream_json=True,
        hook_review_required=True,
        nested_agent_disable="partial",
        native_compact=True,
        compact_command="/compact",
        compact_requires_idle=True,
    )


def test_mock_capabilities_all_safe_fallback() -> None:
    """Mock declares no capabilities so any code branching on
    capability gets the safe fallback path."""
    caps = MockAdapter().capabilities
    assert caps.per_turn_hook is False
    assert caps.session_start_hook is False
    assert caps.native_resume is False
    assert caps.context_usage_reader is False
    assert caps.stream_json is False
    assert caps.hook_review_required is False
    assert caps.nested_agent_disable == "none"


# ---------------------------------------------------------------------------
# Registry consistency
# ---------------------------------------------------------------------------


def test_get_adapter_returns_capabilities_for_all_registered_backends() -> None:
    """Every backend in the registry must produce capabilities — no
    backend can ship without declaring its capability matrix."""
    for backend in ("claude-code", "codex", "mock", "python"):
        adapter = get_adapter(backend)
        caps = adapter.capabilities
        assert isinstance(caps, BackendCapabilities), (
            f"{backend}: capabilities is not a BackendCapabilities"
        )


def test_python_alias_uses_mock_capabilities() -> None:
    """zf.yaml default ``backend: python`` aliases to MockAdapter,
    so capabilities must match Mock's safe-fallback set."""
    python_caps = get_adapter("python").capabilities
    mock_caps = get_adapter("mock").capabilities
    assert python_caps == mock_caps


def test_provider_capability_projection_is_stable() -> None:
    projection = project_provider_capabilities(operator_backends=[
        {"id": "claude-headless", "available": True, "source": "headless"},
        {"id": "codex-headless", "available": False, "source": "headless"},
    ])
    rows = {row["backend"]: row for row in projection["providers"]}

    assert projection["schema_version"] == "provider-capabilities.v1"
    assert rows["claude-code"]["native_resume"] is True
    assert rows["claude-code"]["streaming"] is True
    assert rows["codex"]["hook_review_required"] is True
    assert rows["codex"]["nested_agent_disable"] == "partial"
    assert rows["mock"]["test_mode"] is True
    assert rows["mock"]["native_resume"] is False
    assert rows["claude-headless"]["availability"] == "available"
    assert rows["claude-headless"]["streaming"] is True
    assert rows["claude-headless"]["resume"] is True
    assert rows["claude-headless"]["interrupt"] is True
    assert rows["codex-headless"]["availability"] == "missing_command"


# ---------------------------------------------------------------------------
# Wire-up grep proof — validate.py prints the matrix on cold-start.
# ---------------------------------------------------------------------------


def test_validate_module_imports_backend_capability_helper() -> None:
    """The validate CLI must call _print_backend_capability_matrix so
    `zf validate --cold-start` surfaces capability info; without the
    caller this is Class D anti-pattern."""
    from zf.cli import validate

    assert hasattr(validate, "_print_backend_capability_matrix"), (
        "validate must expose _print_backend_capability_matrix"
    )
    # Source-level wire-up check
    import inspect

    source = inspect.getsource(validate)
    assert "_print_backend_capability_matrix(config)" in source, (
        "validate.cold-start path must call _print_backend_capability_matrix"
    )


# ---------------------------------------------------------------------------
# Behavioral expectations derived from capabilities — these are
# what downstream code branches on.
# ---------------------------------------------------------------------------


def test_claude_supports_session_start_hook_but_codex_does_not() -> None:
    """Documented capability gap from 39 §2.1.3: Codex has no
    session_start hook (only user-prompt-submit / tool / stop).
    Future dispatch / breadcrumb code must not assume both
    backends support it."""
    assert ClaudeCodeAdapter().capabilities.session_start_hook is True
    assert CodexAdapter().capabilities.session_start_hook is False


def test_claude_and_codex_support_native_compact() -> None:
    assert ClaudeCodeAdapter().capabilities.native_compact is True
    assert ClaudeCodeAdapter().capabilities.compact_command == "/compact"
    assert CodexAdapter().capabilities.native_compact is True
    assert CodexAdapter().capabilities.compact_command == "/compact"
    assert CodexAdapter().capabilities.compact_requires_idle is True
    assert MockAdapter().capabilities.native_compact is False
    assert MockAdapter().capabilities.compact_command == ""


def test_codex_requires_hook_review_but_claude_does_not() -> None:
    """Codex needs --enable hooks on each spawn (1202-T1 migration);
    Claude's hook config persists in ~/.claude. Spawn coordinator
    branches on this."""
    assert ClaudeCodeAdapter().capabilities.hook_review_required is False
    assert CodexAdapter().capabilities.hook_review_required is True


def test_codex_spawn_bypasses_hook_trust_prompt() -> None:
    """P1-CODEX-HOOK-TRUST (codex 0.133.0): zaofu writes the project
    `.codex/hooks.json` itself (vetted `zf hook-recv` source), but codex
    refuses to run those hooks until their hash is persisted as trusted, and
    the app-server `hooks/list` pre-trust discovery no longer responds — so
    workers stall at the interactive `/hooks` review prompt. The spawn command
    must pass `--dangerously-bypass-hook-trust` so the (vetted) hooks run
    without the prompt."""
    cmd = CodexAdapter().build_command(RoleConfig(name="dev", backend="codex"))
    assert "--enable" in cmd and "hooks" in cmd
    assert "--dangerously-bypass-hook-trust" in cmd


def test_codex_nested_agent_disable_is_partial_not_full() -> None:
    """Codex internal planning sub-agents cannot be suppressed even
    when the agent's user-facing sub-agent tool is removed. Documented
    in 39 §2.1.3 / 26 §5.5 nested-guard discussion."""
    assert CodexAdapter().capabilities.nested_agent_disable == "partial"
    assert ClaudeCodeAdapter().capabilities.nested_agent_disable == "full"
