"""Backend adapters for agent CLI tools (claude-code, codex, mock).

Adapters produce the argv list used by transports (tmux or stream-json)
to launch the underlying CLI. Since Sprint D (pane-resume invariant X),
``build_command`` accepts ``session_id`` and ``is_resume`` so the
SpawnCoordinator can pre-seed conversation history (Claude) or switch
to ``codex exec resume`` for subsequent invocations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from zf.core.config.schema import RoleConfig


@dataclass(frozen=True)
class BackendCapabilities:
    """Backend / provider capability matrix.

    ZF-TR-PROVIDER-CAP-001 (doc 39 §2.1.3): consolidate the historically
    scattered "Codex has X, Claude has Y" assumptions into a single
    declarative descriptor so dispatch, recovery, doctor, and Agent View
    can branch on the same source instead of re-deriving capability per
    call site.

    Field semantics (each backend overrides as appropriate):

    - ``per_turn_hook``: backend supports a hook invoked before each
      LLM turn. Drives ``<zf-workflow-state>`` breadcrumb injection.
    - ``session_start_hook``: hook invoked once at session start.
    - ``native_resume``: backend has a built-in conversation-resume
      mechanism (vs. zaofu having to replay context).
    - ``context_usage_reader``: backend exposes per-session token
      usage (for ``backend_session_reader.UsageReport``).
    - ``stream_json``: backend supports stream-json transport
      (Claude Code SDK) vs. tmux send-keys only.
    - ``hook_review_required``: hook configuration must be re-issued
      on each spawn (Codex semantics).
    - ``nested_agent_disable``: whether nested sub-agents can be
      programmatically disabled. ``"full"`` / ``"partial"`` / ``"none"``.
    - ``native_compact``: backend accepts an in-session compact command.
    - ``compact_command``: command text sent through transport when
      compact-first context recovery is attempted.
    - ``compact_requires_idle``: backend rejects the compact command while an
      active turn/task is running; runtime should defer to recycle-at-idle
      instead of treating send-keys as successful compaction.
    """

    per_turn_hook: bool
    session_start_hook: bool
    native_resume: bool
    context_usage_reader: bool
    stream_json: bool
    hook_review_required: bool
    nested_agent_disable: str  # "full" | "partial" | "none"
    native_compact: bool = False
    compact_command: str = ""
    compact_requires_idle: bool = False
    provider_session_efforts: tuple[str, ...] = ()
    provider_session_agent: bool = False
    provider_session_parallelism: bool = False
    max_provider_session_parallel_agents: int = 0
    # E0 execution-profile capability baseline. These stay conservative
    # until a concrete headless/CLI probe proves the provider route.
    native_goal: bool = False
    native_multi_agent: bool = False
    child_lineage: str = "none"  # "none" | "summary" | "full"
    child_permission_isolation: str = "none"  # "none" | "prompt" | "enforced"
    compound_resume: str = "none"  # "none" | "root" | "tree"
    root_only_result_channel: bool = False


def validate_provider_session_config(
    role: RoleConfig,
    *,
    capabilities: BackendCapabilities | None = None,
) -> None:
    """Fail before spawn when explicit provider-session intent is unsupported."""

    session = getattr(role, "provider_session", None)
    if session is None:
        return
    caps = capabilities or get_adapter(role.backend).capabilities
    if session.native_delegation is True and not caps.native_multi_agent:
        raise ValueError("native delegation has not been admitted for this provider")
    if session.native_delegation is False and role.backend not in {"codex", "claude-code", "mock"}:
        raise ValueError("provider cannot explicitly disable native delegation")
    if session.effort and session.effort not in caps.provider_session_efforts:
        allowed = ", ".join(caps.provider_session_efforts) or "none"
        raise ValueError(
            f"role {role.name!r} backend={role.backend!r} does not support "
            f"provider_session.effort={session.effort!r}; supported: {allowed}"
        )
    if session.agent and not caps.provider_session_agent:
        raise ValueError(
            f"role {role.name!r} backend={role.backend!r} does not support "
            "provider_session.agent"
        )
    role_agent = str(getattr(role, "agent", "") or "")
    if role_agent and session.agent and role_agent != session.agent:
        raise ValueError(
            f"role {role.name!r}: role.agent and provider_session.agent conflict"
        )
    if session.max_parallel_agents is not None:
        if not caps.provider_session_parallelism:
            raise ValueError(
                f"role {role.name!r} backend={role.backend!r} does not support "
                "provider_session.max_parallel_agents"
            )
        ceiling = min(
            6,
            caps.max_provider_session_parallel_agents or 6,
        )
        if session.max_parallel_agents > ceiling:
            raise ValueError(
                f"role {role.name!r}: provider_session.max_parallel_agents="
                f"{session.max_parallel_agents} exceeds ceiling {ceiling}"
            )


class BackendAdapter(ABC):
    """Abstract backend for spawning agent CLI processes."""

    @abstractmethod
    def build_command(
        self,
        role: RoleConfig,
        *,
        session_id: str | None = None,
        is_resume: bool = False,
        prompt: str | None = None,
    ) -> list[str]:
        """Build the CLI command to launch this agent.

        Args:
            role: role config (name, model, permission_mode, etc.)
            session_id: deterministic session id for resume/seed.
                - Claude: used with ``--session-id`` on first spawn or
                  ``--resume`` on restart.
                - Codex: only used on ``is_resume=True`` via
                  ``codex exec resume <session_id>``.
            is_resume: True if this is a restart of a previously-seen
                instance. Changes which CLI flags are emitted.
            prompt: initial prompt text. Ignored by Claude Code
                (it reads prompts via stdin in stream-json or via
                tmux send-keys). Required by Codex because
                ``codex exec`` takes prompt as positional arg.
        """

    @property
    @abstractmethod
    def ready_pattern(self) -> str:
        """Regex pattern that indicates the agent is ready for input."""

    @property
    def post_ready_delay_s(self) -> float:
        """Seconds to wait AFTER ``ready_pattern`` matches before sending
        the first prompt. B-1203-06: Codex's ``›`` renders during boot
        before its input handler is fully wired, so a send_task that
        arrives in that window is silently dropped. Per-adapter override
        lets codex insert a short stabilization sleep while claude and
        mock stay at zero.
        """
        return 0.0

    @property
    def requires_ready_wait(self) -> bool:
        """Whether this backend exposes an interactive readiness signal."""
        return True

    @property
    @abstractmethod
    def clear_command(self) -> str | None:
        """Command to send to clear agent context, or None if respawn needed."""

    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        """Static capability descriptor for this backend.

        Used by ``zf doctor`` / ``zf validate`` / dispatch / recovery
        to branch on provider features instead of hard-coding ``if
        backend == "codex"`` throughout the codebase.
        """


class ClaudeCodeAdapter(BackendAdapter):
    """Adapter for the Claude Code CLI.

    Trusted ZaoFu provider processes always use
    ``--dangerously-skip-permissions``. ``permission_mode`` remains a logical
    role contract used by ZaoFu's deterministic write-policy, artifact, and
    controlled-action gates; it no longer selects a provider sandbox.

    G-RESUME-1: on first spawn with a known session_id, pass
    ``--session-id <uuid>`` so Claude writes to a deterministic file
    under ``~/.claude/projects/<cwd>/<uuid>.jsonl``. On restart (after
    pane/process crash), pass ``--resume <uuid>`` to continue the same
    conversation.
    """

    def build_command(
        self,
        role: RoleConfig,
        *,
        session_id: str | None = None,
        is_resume: bool = False,
        prompt: str | None = None,
    ) -> list[str]:
        validate_provider_session_config(role, capabilities=self.capabilities)
        cmd = ["claude", "--dangerously-skip-permissions"]
        # ``--add-dir`` keeps declared state/artifact directories visible to
        # the provider session. It is context routing rather than a sandbox
        # restriction, so it remains useful with the trusted full-access
        # baseline.
        for path in role.constraints.allowed_paths:
            cmd.extend(["--add-dir", path])
        if role.model and role.model != "placeholder":
            cmd.extend(["--model", role.model])
        if session_id:
            if is_resume:
                cmd.extend(["--resume", session_id])
            else:
                cmd.extend(["--session-id", session_id])
        # P-Y2: per-spawn extras. Order doesn't matter to claude, but
        # keep them after the core flags for log readability.
        for plugin_dir in role.plugins:
            cmd.extend(["--plugin-dir", plugin_dir])
        provider_session = role.provider_session
        if provider_session is not None and provider_session.native_delegation is False:
            cmd.extend(["--disallowedTools", "Agent", "Task"])
        if provider_session is not None and provider_session.effort:
            cmd.extend(["--effort", provider_session.effort])
        agent = (
            provider_session.agent
            if provider_session is not None and provider_session.agent
            else role.agent
        )
        if agent:
            cmd.extend(["--agent", agent])
        cmd.append("--verbose")
        return cmd

    @property
    def ready_pattern(self) -> str:
        return r"[❯>]"

    @property
    def clear_command(self) -> str | None:
        return "/clear"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            per_turn_hook=True,
            session_start_hook=True,
            native_resume=True,
            context_usage_reader=True,
            stream_json=True,
            hook_review_required=False,
            nested_agent_disable="full",
            native_compact=True,
            compact_command="/compact",
            provider_session_efforts=("low", "medium", "high", "max"),
            provider_session_agent=True,
        )


class CodexAdapter(BackendAdapter):
    """Adapter for the Codex CLI — interactive TUI mode.

    Symmetric with ClaudeCodeAdapter: codex runs as a persistent TUI in
    a tmux pane, prompts are delivered via send-keys (not argv), the
    session file is created when the first prompt is submitted.

    First spawn:
        ``codex --dangerously-bypass-approvals-and-sandbox [--model <m>]``
    Resume after pane crash:
        ``codex --dangerously-bypass-approvals-and-sandbox [--model <m>] resume <uuid>``

    Codex has no flag equivalent to Claude's ``--session-id`` (you cannot
    pre-seed the uuid). Instead, the SpawnCoordinator observes the
    session file codex writes after the first turn and caches the uuid
    via RoleSessionRegistry.observe_codex_session, which is then used
    on respawn. ``--last`` is intentionally NEVER used: in a multi-role
    or multi-instance setup it would pick whatever codex session was
    most recent globally, including unrelated sessions.

    The ``prompt`` parameter is accepted for interface symmetry but
    ignored — prompts are delivered via tmux send-keys, just like Claude.
    """

    def build_command(
        self,
        role: RoleConfig,
        *,
        session_id: str | None = None,
        is_resume: bool = False,
        prompt: str | None = None,
    ) -> list[str]:
        validate_provider_session_config(role, capabilities=self.capabilities)
        # 1202-T1 / 2026-05 Codex CLI migration: enable Codex hooks with
        # the current feature name. Older Codex builds used
        # `features.codex_hooks`; current builds warn and require
        # `features.hooks` / `--enable hooks`.
        #
        # P1-CODEX-HOOK-TRUST (2026-05-28, codex 0.133.0): zaofu writes the
        # project `.codex/hooks.json` itself (the `zf hook-recv` telemetry
        # hooks — a vetted source). Codex 0.133.0 refuses to run those hooks
        # until their per-hook hash is persisted as trusted, and the
        # app-server `hooks/list` pre-trust discovery no longer responds on
        # stdio, so workers stall at the interactive `/hooks` review prompt.
        # `--dangerously-bypass-hook-trust` is codex's documented escape hatch
        # for "automation that already vets hook sources": it runs the enabled
        # hooks without persisted trust and without the review prompt, so the
        # telemetry hooks still fire (no stall, no lost briefings).
        cmd = ["codex", "--enable", "hooks", "--dangerously-bypass-hook-trust"]

        # Provider sandboxing is intentionally uniform across all ZaoFu
        # workers. Logical role constraints are still enforced by the kernel,
        # not delegated to Codex's host-dependent bwrap/unshare sandbox.
        cmd.append("--dangerously-bypass-approvals-and-sandbox")

        if role.model and role.model != "placeholder":
            cmd.extend(["--model", role.model])
        provider_session = role.provider_session
        if provider_session is not None and provider_session.native_delegation is False:
            cmd.extend(["--disable", "multi_agent", "--disable", "multi_agent_v2"])
        session_effort = (
            provider_session.effort
            if provider_session is not None and provider_session.effort
            else role.model_reasoning_effort
        )
        if session_effort:
            cmd.extend([
                "--config",
                f'model_reasoning_effort="{session_effort}"',
            ])
        if (
            provider_session is not None
            and provider_session.max_parallel_agents is not None
        ):
            cmd.extend([
                "-c",
                "agents.max_concurrent_threads_per_session="
                f"{provider_session.max_parallel_agents}",
            ])
        if is_resume and session_id:
            cmd.extend(["resume", session_id])
        # is_resume=True with session_id=None falls through to a fresh
        # codex (no resume). Caller is responsible for emitting a
        # warning event when this happens (see SpawnCoordinator).
        return cmd

    @property
    def ready_pattern(self) -> str:
        # Codex TUI shows U+203A (›) at the input prompt, not ASCII >.
        return r"›"

    @property
    def post_ready_delay_s(self) -> float:
        # B-1203-06 R-1 (2026-04-21 mixed-e2e): codex paints its ›
        # prompt during startup but doesn't wire stdin to the ratatui
        # event loop for ~1s after that. If tmux send-keys lands inside
        # that window the Enter is silently dropped and the briefing
        # text sits in the draft buffer forever. 1.5s covers the
        # observed boot time on this machine with margin.
        return 1.5

    @property
    def clear_command(self) -> str | None:
        return None  # codex uses respawn instead of clear

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            per_turn_hook=True,
            # Codex has no session-start hook (only user-prompt-submit /
            # tool / stop) — 39 §2.1.3 documented gap.
            session_start_hook=False,
            native_resume=True,
            context_usage_reader=True,
            stream_json=True,
            # Per CodexAdapter docstring 1202-T1: hooks must be enabled
            # via `--enable hooks` on each spawn; equivalent to "must
            # re-issue hook config".
            hook_review_required=True,
            # Codex sub-agent disable is partial — `codex exec` can be
            # forbidden by tool restrictions but Codex still has internal
            # planning sub-agents we can't suppress.
            nested_agent_disable="partial",
            native_compact=True,
            compact_command="/compact",
            compact_requires_idle=True,
            provider_session_efforts=(
                "minimal",
                "low",
                "medium",
                "high",
                "xhigh",
                "ultra",
            ),
            provider_session_parallelism=True,
            max_provider_session_parallel_agents=6,
        )


class MockAdapter(BackendAdapter):
    """Mock adapter for testing — uses echo/cat as a fake agent."""

    def build_command(
        self,
        role: RoleConfig,
        *,
        session_id: str | None = None,
        is_resume: bool = False,
        prompt: str | None = None,
    ) -> list[str]:
        validate_provider_session_config(role, capabilities=self.capabilities)
        return ["cat"]

    @property
    def ready_pattern(self) -> str:
        return r">"

    @property
    def requires_ready_wait(self) -> bool:
        return False

    @property
    def clear_command(self) -> str | None:
        return None

    @property
    def capabilities(self) -> BackendCapabilities:
        # Mock has no capabilities — used for tests only. All flags
        # false / "none" so any code that branches on capability gets
        # the safe fallback path.
        return BackendCapabilities(
            per_turn_hook=False,
            session_start_hook=False,
            native_resume=False,
            context_usage_reader=False,
            stream_json=False,
            hook_review_required=False,
            nested_agent_disable="none",
        )


_ADAPTERS: dict[str, type[BackendAdapter]] = {
    "claude-code": ClaudeCodeAdapter,
    "codex": CodexAdapter,
    "mock": MockAdapter,
    "python": MockAdapter,  # default in zf.yaml, maps to mock for now
}


def get_adapter(backend: str) -> BackendAdapter:
    """Get an adapter instance by backend name."""
    cls = _ADAPTERS.get(backend)
    if cls is None:
        raise ValueError(f"Unknown backend: {backend!r}. Available: {list(_ADAPTERS)}")
    return cls()
