"""Tests for codex.hook.* reactor routing — 1202-T3.

Registers a minimal 5-event handler set for Codex hooks under the
`codex.hook.*` namespace. The handlers must:
  - For `codex.hook.stop`: wake run_once (no state transition); acts
    as the codex equivalent of `orchestrator.round.complete`.
  - For `codex.hook.pre_tool_use` + `permissionDecision=deny`: emit
    `agent.api_blocked` so the circuit breaker + rate-limit path kick in.
  - For the other three (session_start / user_prompt_submit /
    post_tool_use): observational no-op (return None, do not mutate
    state), but be present in both the registry and WAKE_PATTERNS so
    the invariant `test_every_reactor_handler_has_wake_pattern` holds.
"""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.wake_patterns import WAKE_PATTERNS, reactor_handler_events


def test_codex_hook_all_five_events_are_registered():
    handlers = reactor_handler_events()
    expected = {
        "codex.hook.session_start",
        "codex.hook.user_prompt_submit",
        "codex.hook.pre_tool_use",
        "codex.hook.post_tool_use",
        "codex.hook.stop",
    }
    missing = expected - handlers
    assert not missing, f"codex.hook.* handlers missing: {sorted(missing)}"


def test_codex_hook_all_five_events_are_in_wake_patterns():
    """LH-3.T4 invariant: every reactor handler must wake run_once."""
    wake = set(WAKE_PATTERNS)
    expected = {
        "codex.hook.session_start",
        "codex.hook.user_prompt_submit",
        "codex.hook.pre_tool_use",
        "codex.hook.post_tool_use",
        "codex.hook.stop",
    }
    missing = expected - wake
    assert not missing, f"codex.hook.* missing from WAKE_PATTERNS: {sorted(missing)}"


def _build_reactor_stub():
    """Minimal reactor host for handler unit tests — only fields the
    codex.hook handlers touch."""

    class _Log:
        def __init__(self) -> None:
            self.appended: list[ZfEvent] = []

        def append(self, event: ZfEvent) -> None:
            self.appended.append(event)

    class _Stub:
        def __init__(self) -> None:
            self.event_log = _Log()

    # Import inside the factory so the mixin can be attached without a
    # full Orchestrator constructor.
    from zf.runtime.orchestrator_reactor import EventReactorMixin

    class _HostedReactor(EventReactorMixin, _Stub):
        pass

    return _HostedReactor()


def test_codex_hook_pre_tool_use_deny_emits_api_blocked():
    reactor = _build_reactor_stub()
    event = ZfEvent(
        type="codex.hook.pre_tool_use",
        actor="dev-1",
        payload={
            "tool_name": "Bash",
            "permissionDecision": "deny",
            "permission_mode": "workspace-write",
        },
    )
    decision = reactor._on_codex_hook_pre_tool_use(event)  # type: ignore[attr-defined]

    kinds = [e.type for e in reactor.event_log.appended]
    assert "agent.api_blocked" in kinds, (
        f"expected agent.api_blocked after deny, got events: {kinds}"
    )
    assert decision is None  # no direct task-state decision


def test_codex_hook_pre_tool_use_allow_is_noop():
    reactor = _build_reactor_stub()
    event = ZfEvent(
        type="codex.hook.pre_tool_use",
        actor="dev-1",
        payload={"tool_name": "Bash", "permissionDecision": "allow"},
    )
    decision = reactor._on_codex_hook_pre_tool_use(event)  # type: ignore[attr-defined]
    assert not reactor.event_log.appended
    assert decision is None


def test_codex_hook_stop_is_noop_observation():
    """codex.hook.stop wakes run_once via WAKE_PATTERNS but does not
    drive a state transition itself."""
    reactor = _build_reactor_stub()
    event = ZfEvent(
        type="codex.hook.stop",
        actor="dev-1",
        payload={"session_id": "s", "turn_id": "t"},
    )
    decision = reactor._on_codex_hook_stop(event)  # type: ignore[attr-defined]
    assert decision is None
    assert not reactor.event_log.appended


def test_codex_hook_observational_three_are_noop():
    """session_start / user_prompt_submit / post_tool_use only record."""
    reactor = _build_reactor_stub()
    for etype, method in (
        ("codex.hook.session_start", "_on_codex_hook_session_start"),
        ("codex.hook.user_prompt_submit", "_on_codex_hook_user_prompt_submit"),
        ("codex.hook.post_tool_use", "_on_codex_hook_post_tool_use"),
    ):
        handler = getattr(reactor, method)
        decision = handler(ZfEvent(type=etype, actor="dev-1"))
        assert decision is None, f"{method} should be no-op"
    assert not reactor.event_log.appended
