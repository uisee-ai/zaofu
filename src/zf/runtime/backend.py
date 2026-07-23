"""Backend adapters for agent CLI tools (claude-code, codex, mock).

Adapters produce the argv list used by transports (tmux or stream-json)
to launch the underlying CLI. Since Sprint D (pane-resume invariant X),
``build_command`` accepts ``session_id`` and ``is_resume`` so the
SpawnCoordinator can pre-seed conversation history (Claude) or switch
to ``codex exec resume`` for subsequent invocations.
"""

from __future__ import annotations

import os
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

    permission_mode=bypass: --dangerously-skip-permissions (full autonomy).
    permission_mode=allowlist + non-empty allowed_tools: --allowedTools "<list>"
        (Claude Code restricts tool use to the listed patterns).
    permission_mode=allowlist + empty allowed_tools: neither flag — Claude Code
        falls back to interactive permission prompts, which in headless mode
        means tools are blocked. Caller is expected to either populate
        allowed_tools or use permission_mode=bypass.

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
        cmd = ["claude"]
        if role.permission_mode == "bypass":
            cmd.append("--dangerously-skip-permissions")
        elif role.permission_mode == "allowlist" and role.allowed_tools:
            cmd.extend(["--allowedTools", " ".join(role.allowed_tools)])
            # R24: an allowlisted role still hits Claude's directory-trust
            # prompt on any read outside its worktree cwd (briefings/
            # instructions live in the state dir) — a headless pane then
            # hangs. Grant the declared paths up front, mirroring the
            # codex branch below.
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
        if role.agent:
            cmd.extend(["--agent", role.agent])
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

        # 1231-T2: Codex permission is a sandbox × approval 2-tuple,
        # not a tool allowlist. Map zaofu's permission_mode to Codex flags:
        #   bypass            → --dangerously-bypass-approvals-and-sandbox
        #   default / ""      → -a never -s workspace-write
        #   restricted /      → -a untrusted + sandbox choice:
        #   allowlist            · no allowed_paths → -s read-only
        #                        · has allowed_paths → -s workspace-write
        #                          + --add-dir <p> for each extra path
        #   <anything else>   → -a never -s workspace-write (safe fallback)
        #
        # B-1203-01 (2026-04-21 mixed-e2e smoke): combining
        # `-s read-only` with `--add-dir` makes codex abort on spawn
        # ("Ignoring --add-dir because the effective sandbox mode is
        # read-only"). The role can't decide at build-time which files
        # it will edit, so when allowed_paths is declared we upgrade
        # sandbox to workspace-write — this gives cwd-write *and* the
        # declared --add-dir entries, which is the closest Codex can
        # express to "writable only within these paths".
        pm = role.permission_mode or "default"
        sandbox_override = os.environ.get("ZF_CODEX_WORKER_SANDBOX", "").strip()
        if pm == "bypass":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        elif pm in ("restricted", "allowlist"):
            if sandbox_override == "danger-full-access":
                cmd.extend(["-a", "never", "-s", "danger-full-access"])
            elif role.constraints.allowed_paths:
                cmd.extend(["-a", "untrusted"])
                cmd.extend(["-s", "workspace-write"])
                for path in role.constraints.allowed_paths:
                    cmd.extend(["--add-dir", path])
            else:
                cmd.extend(["-a", "untrusted"])
                cmd.extend(["-s", "read-only"])
        else:
            # Codex CLI 0.130 removed the legacy --full-auto alias.
            # Keep the bounded workspace-write behavior without prompting
            # a headless tmux worker for approvals it cannot answer.
            cmd.extend(["-a", "never", "-s", "workspace-write"])

        if role.model and role.model != "placeholder":
            cmd.extend(["--model", role.model])
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
