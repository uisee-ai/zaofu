"""SpawnCoordinator — unified spawn flow for workers.

Owns the glue between:
  - BackendAdapter (argv generation)
  - RoleSessionRegistry (session_id resolution + spawn tracking)
  - TransportAdapter (physical spawn)

Called in two contexts:
  1. start.py on first boot — spawn every role in config.roles
  2. Orchestrator._respawn_instance — after pane/process crash

Both Claude and Codex are now persistent TUI processes in tmux panes:

Claude:
  ``claude --session-id <uuid>`` on first spawn, ``claude --resume <uuid>``
  on restart. Session id is pre-seeded by the registry.

Codex:
  ``codex`` on first spawn (interactive TUI), ``codex resume <uuid>`` on
  restart. Codex has no pre-seed flag — the uuid is observed AFTER the
  first turn writes ``~/.codex/sessions/.../rollout-*-<uuid>.jsonl``.
  RoleSessionRegistry.observe_codex_session caches it for respawn.
  ``--last`` is intentionally NEVER used (see CodexAdapter docstring).
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.backend import get_adapter
from zf.runtime.codex_hooks import codex_hook_trust_states, write_codex_hook_settings
from zf.runtime.launch_artifact import write_launch_artifact
from zf.runtime.transport import TransportAdapter

if TYPE_CHECKING:
    from zf.core.config.schema import ZfConfig
    from zf.core.events.log import EventLog


_MAX_FRESH_CLAUDE_SESSION_CANDIDATES = 8


class SpawnCoordinator:
    def __init__(
        self,
        *,
        state_dir: Path,
        registry: RoleSessionRegistry,
        transport: TransportAdapter,
        project_root: str,
        event_log: "EventLog | None" = None,
        config: "ZfConfig | None" = None,
    ) -> None:
        self.state_dir = state_dir
        self.registry = registry
        self.transport = transport
        self.project_root = project_root
        self.event_log = event_log
        self.config = config
        # Codex observation tracking. Codex writes its session file only
        # after the first turn starts, so we observe AFTER the first
        # send_task. _spawn_ts gates the glob to files newer than spawn.
        self._spawn_ts: dict[str, float] = {}
        self._codex_observe_inflight: set[str] = set()
        self._codex_hook_trust_cache: dict[Path, list[tuple[str, str]]] = {}

    def _claude_session_exists(self, session_id: str) -> bool:
        """B-W5-01 (2026-04-20): probe whether Claude already has a
        session file for this uuid. Needed to distinguish "true respawn"
        (pane crashed, session file survives, --resume will work) from
        "re-entry" (second zf start invocation with stale meta, session
        file was never created → --resume would fail with 'No conversation
        found'). Mirrors `transport_stream_json._session_exists_on_disk`.
        """
        from zf.runtime.session_tailer import claude_session_path
        return claude_session_path(self.project_root, session_id).exists()

    def _codex_session_exists(self, role: RoleConfig, session_id: str) -> bool:
        """Return True only when the cached Codex rollout still exists.

        Codex exits immediately when asked to ``resume <uuid>`` for a rollout
        file that no longer exists. In tmux that leaves the pane at a shell
        prompt, so the next recovery/dispatch prompt gets pasted into bash.
        Probe the cached path first, then the role-local CODEX_HOME sessions
        root, before allowing resume.
        """
        def _matches(path: Path) -> bool:
            try:
                return (
                    path.is_file()
                    and session_id in path.stem
                    and self.registry._rollout_matches_project(path)
                )
            except OSError:
                return False

        cached = self.registry.get_path(role.instance_id)
        if cached is not None and _matches(cached):
            return True

        roots = [self._codex_sessions_root(role), Path.home() / ".codex" / "sessions"]
        for root in roots:
            try:
                matches = root.glob(f"*/*/*/rollout-*-{session_id}.jsonl")
            except OSError:
                continue
            if any(_matches(path) for path in matches):
                return True
        return False

    # -- primary entry: spawn a role (first boot or respawn) --

    def spawn(self, role: RoleConfig, *, cwd: Path | None = None) -> None:
        """Spawn the given role instance (claude, codex, mock, python).

        Whether this is a first-spawn or a respawn is inferred from two
        signals:
          1. ``registry._meta[instance_id]["spawned_at"]`` — persistent
             flag set by a previous mark_spawned.
          2. For claude-code: whether the session JSONL file actually
             exists under ``~/.claude/projects/<escaped-cwd>/<uuid>.jsonl``.

        Signal 1 alone is unsafe (B-W5-01, 2026-04-20): when start.py is
        invoked a second time (e.g. ``zf start --foreground`` after the
        initial non-foreground boot), ``spawned_at`` persists on disk
        even though the claude process from the prior boot has since
        exited and left no session file behind. Passing ``--resume``
        to that re-spawn makes claude abort with "No conversation found",
        silently breaking Layer 2. Signal 2 closes the gap: respawn is
        only real when the file exists.

        For codex: its session JSONL is written lazily (post-first-turn),
        so file-existence can't be trusted. Keep the meta-only rule.
        """
        role = self._apply_runner_policy(role)
        meta_spawned = bool(
            self.registry._meta.get(role.instance_id, {}).get("spawned_at")
        )

        session_id: str | None = None
        # B-MIXEDBACKEND-01: bind UUID to (instance, backend). When the
        # backend under an instance_id flips (e.g. dev-1 was claude, now
        # codex), the registry regenerates the UUID so the new backend
        # never inherits a session file the other backend owns.
        #
        # Asymmetry: claude pre-seeds the UUID (needs it on argv), codex
        # only has a real UUID after observe_codex_session. So we only
        # call get_or_create for claude; for codex we bind backend via
        # mark_backend (meta-only, no UUID generation) so a future
        # backend flip on the same instance still rotates correctly.
        if role.backend == "claude-code":
            session_id = str(self.registry.get_or_create(
                role.instance_id, backend=role.backend,
            ))
            is_respawn = meta_spawned and self._claude_session_exists(session_id)
        else:
            self.registry.mark_backend(role.instance_id, role.backend)
            is_respawn = meta_spawned

        if role.backend == "codex" and is_respawn:
            # Codex's uuid was observed after the first turn (or not at
            # all if codex died before the first turn completed).
            cached = self.registry.get(role.instance_id)
            if cached is None:
                # Strategy B: start a fresh codex (no resume). Never use
                # --last — in multi-instance/multi-role configs it would
                # pick whichever codex session was most recent globally.
                self._emit_warning(
                    role,
                    "codex_no_cached_session",
                    "respawning codex without resume — uuid was never observed",
                )
            else:
                cached_id = str(cached)
                if self._codex_session_exists(role, cached_id):
                    session_id = cached_id
                else:
                    self.registry.clear(role.instance_id)
                    is_respawn = False
                    session_id = None
                    self._emit_warning(
                        role,
                        "codex_cached_session_missing",
                        (
                            "respawning codex without resume — cached "
                            f"session {cached_id} is not present on disk"
                        ),
                    )

        if role.backend == "codex":
            # Codex shows a blocking "Update available! Press enter to
            # continue" prompt at TUI start when latest_version differs
            # from dismissed_version in ~/.codex/version.json. Setting
            # dismissed_version to latest_version converts the prompt
            # into a non-blocking banner so send-keys can drive the TUI
            # immediately.
            _dismiss_codex_update_prompt()

            # 1231-T2: Codex has no tool allowlist — its permission model
            # is sandbox × approval. Warn the user when allowed_tools is
            # set on a codex role so silent-ignore doesn't hide a misconfig.
            if role.allowed_tools:
                self._emit_warning(
                    role,
                    "codex_ignores_allowed_tools",
                    (
                        f"role {role.name!r} has allowed_tools set but "
                        f"backend=codex ignores tool allowlists "
                        f"(use permission_mode=restricted + "
                        f"constraints.allowed_paths instead)"
                    ),
                )

        adapter = get_adapter(role.backend)
        spawn_cwd = (
            Path(cwd).resolve()
            if cwd is not None
            else Path(self.project_root).resolve()
        )
        # DID-6 (2026-06-19 e2e): a fresh Claude ``--session-id`` spawn whose
        # deterministic id is still held by a live process aborts with "Session
        # ID is already in use".  A single rotation is insufficient because a
        # prior state_dir for the same project can have left that rotated UUID's
        # lock behind.  Reserve a usable fresh candidate before baking it into
        # argv; normal ``--resume`` paths deliberately keep their session.
        if role.backend == "claude-code" and session_id and not is_respawn:
            session_id = self._reserve_fresh_claude_session_id(role, session_id)
        argv = adapter.build_command(
            role,
            session_id=session_id,
            is_resume=is_respawn,
        )
        env_prefix = [
            "env",
            f"ZF_PROJECT_ROOT={Path(self.project_root).resolve()}",
            f"ZF_STATE_DIR={self.state_dir.resolve()}",
            f"ZF_ROLE_NAME={role.name}",
            f"ZF_ROLE_INSTANCE={role.instance_id}",
        ]
        from zf.runtime.result_submit import provision_role_submit_credential

        submit_token_file = provision_role_submit_credential(
            self.state_dir,
            role.instance_id,
            rotate=True,
        )
        env_prefix.append(f"ZF_RESULT_SUBMIT_TOKEN_FILE={submit_token_file}")
        zf_cli_cmd = os.environ.get("ZF_CLI_CMD", "").strip()
        if zf_cli_cmd:
            env_prefix.append(f"ZF_CLI_CMD={zf_cli_cmd}")
        if role.backend == "codex":
            codex_home = self._prepare_codex_home(
                role,
                hook_project_root=spawn_cwd,
            )
            env_prefix.append(f"CODEX_HOME={codex_home}")
        argv = [*env_prefix, *argv]
        # Phase 1: if a hook settings file exists, wire it to the claude
        # backend. Codex loads project `.codex/hooks.json`; its per-role
        # CODEX_HOME receives hook trust state in `_prepare_codex_home`.
        if role.backend == "claude-code":
            hooks_settings = self.state_dir / "hooks" / "settings.json"
            if hooks_settings.exists():
                argv.extend(["--settings", str(hooks_settings)])
            # Pre-accept the Claude workspace trust dialog for ``cwd`` so
            # the spawn does not stall on an interactive prompt. Without
            # this, every fresh per-role worktree is "untrusted" and the
            # first `tmux send-keys` of the briefing lands inside the
            # trust dialog instead of the prompt input.
            self._ensure_claude_workspace_trusted(cwd)
            # (stale-session purge + rotate-on-live-collision now runs above,
            # before build_command bakes the id into argv — see DID-6.)
        self._spawn_ts[role.instance_id] = time.time()
        launch_ref = write_launch_artifact(
            state_dir=self.state_dir,
            project_root=Path(self.project_root).resolve(),
            role=role,
            argv=argv,
            cwd=spawn_cwd,
            session_id=session_id,
            is_resume=is_respawn,
            transport=self.transport,
        )
        launch_attempt = 0
        try:
            launch_payload = json.loads(launch_ref.read_text(encoding="utf-8"))
            launch_attempt = int(launch_payload.get("attempt") or 0)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
        if self.event_log is not None:
            try:
                self.event_log.append(ZfEvent(
                    type="worker.launch_artifact.written",
                    actor="zf-cli",
                    payload={
                        "instance_id": role.instance_id,
                        "role": role.name,
                        "backend": role.backend,
                        "artifact_ref": str(launch_ref),
                        "launch_attempt": launch_attempt,
                        "is_resume": is_respawn,
                    },
                ))
            except Exception:  # noqa: BLE001 — launch artifact is best-effort telemetry
                pass
        self.transport.spawn(role, argv, cwd=cwd)
        self.registry.mark_spawned(role.instance_id)

    def _apply_runner_policy(self, role: RoleConfig) -> RoleConfig:
        from zf.core.workflow.runner_policy import (
            apply_goal_closure_judge_policy,
            apply_pure_aggregator_policy,
            goal_closure_judge_policy_plan,
            pure_aggregator_policy_plan,
        )

        state_dir = self.state_dir.resolve()
        plan = pure_aggregator_policy_plan(self.config, role, state_dir=state_dir)
        effective = apply_pure_aggregator_policy(
            self.config, role, state_dir=state_dir,
        )
        if effective is not role and plan.get("applied"):
            self._emit_policy_applied(role, plan)
        judge_plan = goal_closure_judge_policy_plan(
            self.config, effective, state_dir=state_dir,
        )
        narrowed = apply_goal_closure_judge_policy(
            self.config, effective, state_dir=state_dir,
        )
        if narrowed is not effective and judge_plan.get("applied"):
            self._emit_policy_applied(effective, judge_plan)
        return narrowed

    def _emit_policy_applied(self, role: RoleConfig, plan: dict) -> None:
        if self.event_log is None:
            return
        try:
            self.event_log.append(ZfEvent(
                type="worker.policy.applied",
                actor="zf-cli",
                payload={
                    "policy_id": str(plan.get("policy_id") or ""),
                    "role": role.name,
                    "instance_id": role.instance_id,
                    "backend": role.backend,
                    "changes": dict(plan.get("changes") or {}),
                    "original": dict(plan.get("original") or {}),
                    "effective": dict(plan.get("effective") or {}),
                    "reason": (
                        "read-only workflow role; runner permissions are "
                        "narrowed for this spawn"
                    ),
                },
            ))
        except Exception:
            pass

    def _ensure_claude_workspace_trusted(self, cwd: Path | None) -> None:
        """Mark the Claude workspace at ``cwd`` as trusted in ~/.claude.json.

        Claude Code records per-folder trust under
        ``projects.<absolute_path>.hasTrustDialogAccepted``. A fresh
        worktree (e.g. ``.zf/workdirs/dev-1/project``) is treated as a
        distinct workspace, so harness-spawned workers stall on the
        "Do you trust the files in this folder?" prompt unless the
        entry exists.

        Best-effort: any I/O or JSON failure is swallowed because the
        spawn must keep going (operator can still trust manually).
        """
        if cwd is None:
            return
        try:
            target = str(Path(cwd).resolve())
        except (OSError, RuntimeError):
            return
        claude_config = Path.home() / ".claude.json"
        try:
            if claude_config.exists():
                data = json.loads(claude_config.read_text(encoding="utf-8"))
            else:
                data = {}
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        projects = data.setdefault("projects", {})
        if not isinstance(projects, dict):
            return
        entry = projects.get(target)
        if not isinstance(entry, dict):
            entry = {}
        if entry.get("hasTrustDialogAccepted") is True:
            return
        entry["hasTrustDialogAccepted"] = True
        # The `allowedTools` field is required to be a list when present;
        # newer Claude versions reject missing key, so seed an empty list.
        entry.setdefault("allowedTools", [])
        entry.setdefault("hasClaudeMdExternalIncludesApproved", True)
        entry.setdefault("hasClaudeMdExternalIncludesWarningShown", True)
        projects[target] = entry
        try:
            claude_config.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError:
            return

    def _purge_stale_claude_session(self, role: RoleConfig, session_id: str) -> None:
        """Clear stale `lastSessionId` / `lastHintSessionId` references in
        ``~/.claude.json`` and a couple of auxiliary residual dirs so
        ``claude --session-id <uuid>`` does not abort with
        "Session ID is already in use".

        Discovered on cangjie r4 (backlog 2026-05-14-1549): the REAL lock
        claude CLI checks lives in ``~/.claude.json projects.<key>.*``;
        the project key is the git toplevel, not the worktree cwd, so a
        single dead worker can poison every replica that shares the same
        repo. Auxiliary residuals (``~/.claude/session-env/<uuid>`` and
        ``~/.claude/tasks/<uuid>``) are cleaned as a tidy-up but are
        NOT the actual lock.

        Safe when:
          - no live process currently holds ``--session-id <uuid>``
          - the json file is parseable
        Best-effort: any I/O / parse error is swallowed; the spawn must
        proceed.

        The filesystem work lives in module-level
        ``purge_stale_claude_session_lock`` so the stream-json transport
        (which spawns claude headless without going through this coordinator)
        can reuse the exact same lock-clearing — see P0-1 (2026-06-19 e2e).
        This method keeps the tmux-path telemetry event unchanged.
        """
        purged = purge_stale_claude_session_lock(session_id)
        if not any(purged.values()):
            return
        if self.event_log is None:
            return
        try:
            self.event_log.append(ZfEvent(
                type="worker.spawn.stale_session_purged",
                actor="zf-cli",
                payload={
                    "instance_id": role.instance_id,
                    "role": role.name,
                    "backend": role.backend,
                    "session_id": session_id,
                    **purged,
                },
            ))
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def _reserve_fresh_claude_session_id(
        self,
        role: RoleConfig,
        session_id: str,
    ) -> str:
        """Return a fresh Claude UUID whose stale state has been cleared.

        ``RoleSessionRegistry.rotate`` is deterministic for an instance.  Two
        independently created state directories for the same project can
        therefore reach the same rotation number.  Re-checking every rotated
        candidate prevents a prior run's live process or stale Claude lock from
        turning a recovery into a shell pane while the kernel reports success.
        """
        candidate = session_id
        for attempt in range(_MAX_FRESH_CLAUDE_SESSION_CANDIDATES):
            self._purge_stale_claude_session(role, candidate)
            if not _uuid_used_by_live_process(candidate):
                return candidate
            if attempt + 1 < _MAX_FRESH_CLAUDE_SESSION_CANDIDATES:
                candidate = str(self.registry.rotate(role.instance_id))
        raise RuntimeError(
            "unable to reserve a fresh Claude session after "
            f"{_MAX_FRESH_CLAUDE_SESSION_CANDIDATES} candidates for "
            f"{role.instance_id}"
        )

    def _prepare_codex_home(
        self,
        role: RoleConfig,
        *,
        hook_project_root: Path | None = None,
    ) -> Path:
        """Prepare per-role Codex runtime home before spawning Codex."""
        codex_home = self._codex_home(role)
        codex_home.mkdir(parents=True, exist_ok=True)
        global_home = Path.home() / ".codex"
        hook_project_root = (
            hook_project_root.resolve()
            if hook_project_root is not None
            else Path(self.project_root).resolve()
        )

        for name in ("auth.json",):
            src = global_home / name
            dst = codex_home / name
            if dst.exists() or dst.is_symlink():
                continue
            if src.exists():
                try:
                    dst.symlink_to(src, target_is_directory=src.is_dir())
                except OSError:
                    if src.is_dir():
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)

        sessions_dir = codex_home / "sessions"
        if sessions_dir.is_symlink() or sessions_dir.is_file():
            sessions_dir.unlink()
        sessions_dir.mkdir(parents=True, exist_ok=True)

        config_src = global_home / "config.toml"
        config_dst = codex_home / "config.toml"
        if config_src.exists() and not config_dst.exists():
            shutil.copy2(config_src, config_dst)

        version_src = global_home / "version.json"
        version_dst = codex_home / "version.json"
        if version_src.exists() and not version_dst.exists():
            shutil.copy2(version_src, version_dst)
        _dismiss_codex_update_prompt(codex_home)

        stale_hooks = codex_home / "hooks.json"
        if stale_hooks.exists() or stale_hooks.is_symlink():
            stale_hooks.unlink()

        write_codex_hook_settings(self.state_dir, project_root=hook_project_root)
        self._install_codex_project_hook_trust(
            role,
            codex_home,
            project_root=hook_project_root,
        )
        return codex_home

    def _install_codex_project_hook_trust(
        self,
        role: RoleConfig,
        codex_home: Path,
        *,
        project_root: Path,
    ) -> None:
        """Trust ZaoFu-rendered project hooks inside this runtime CODEX_HOME.

        Codex 0.130+ loads project `.codex/hooks.json` but refuses to run
        those hooks until a user-scoped config layer records the hook's
        current hash. The ZaoFu hooks are generated by the deterministic
        kernel for the current worktree, so we keep the trust record scoped to
        the per-role runtime `CODEX_HOME/config.toml` instead of mutating the
        operator's global `~/.codex/config.toml`.
        """
        project_root = project_root.resolve()
        project_hooks = project_root / ".codex" / "hooks.json"
        if not project_hooks.exists():
            return

        config_path = codex_home / "config.toml"
        # Codex refuses to run project hooks until their per-hook hash is
        # recorded as trusted. The codex 0.133 `app-server hooks/list` RPC that
        # used to supply those hashes no longer responds on stdio, so compute
        # the hashes deterministically instead (codex_hook_hash, verified
        # byte-exact against real codex-written hashes). Key by both the
        # worktree project_root and the main project root: codex resolves
        # project hooks by walking parent dirs from the worker cwd, so a nested
        # worktree may match either its own or the ancestor project's hooks.json.
        hook_states = codex_hook_trust_states(
            self.state_dir, project_root, Path(self.project_root)
        )
        if not hook_states:
            self._emit_warning(
                role,
                "codex_hook_trust_unavailable",
                "could not compute Codex hook trust hashes",
            )
            return
        _write_codex_runtime_hook_trust(
            config_path,
            project_root=project_root,
            hook_states=hook_states,
        )

    def _codex_home(self, role: RoleConfig) -> Path:
        return self.state_dir / "workdirs" / role.instance_id / "codex-home"

    def _codex_sessions_root(self, role: RoleConfig) -> Path:
        return self._codex_home(role) / "sessions"

    # -- post-dispatch hook: observe codex session file --

    def notify_first_dispatch(self, role: RoleConfig) -> None:
        """Called by orchestrator after a successful send_task.

        For codex backends: schedules background observation of the
        session file codex writes once the first turn starts. Caches
        the uuid via RoleSessionRegistry so subsequent respawns can
        use ``codex resume <uuid>``.

        Idempotent and cheap to call repeatedly:
          - non-codex backends → no-op
          - uuid already cached → no-op
          - observation already in-flight for this instance → no-op
        """
        if role.backend != "codex":
            return
        if self.registry.get(role.instance_id) is not None:
            return  # uuid already cached
        if role.instance_id in self._codex_observe_inflight:
            return  # background thread is already polling
        self._codex_observe_inflight.add(role.instance_id)
        since_ts = self._spawn_ts.get(role.instance_id, 0.0)
        threading.Thread(
            target=self._observe_codex_in_background,
            args=(role, since_ts),
            daemon=True,
        ).start()

    def _observe_codex_in_background(
        self, role: RoleConfig, since_ts: float
    ) -> None:
        try:
            result = self.registry.observe_codex_session(
                role.instance_id,
                since_ts=since_ts,
                max_wait_seconds=30.0,
                sessions_root=self._codex_sessions_root(role),
            )
            if result is None:
                self._emit_warning(
                    role,
                    "codex_observe_timeout",
                    "no session file appeared within 30s after dispatch",
                )
        except Exception as exc:
            self._emit_warning(
                role, "codex_observe_failed", f"observe error: {exc}"
            )
        finally:
            self._codex_observe_inflight.discard(role.instance_id)

    # -- internal: warning event emission --

    def _emit_warning(self, role: RoleConfig, code: str, message: str) -> None:
        if self.event_log is None:
            return  # tests / dry-runs without an event log
        self.event_log.append(ZfEvent(
            type="worker.spawn_warning",
            actor="zf-cli",
            payload={
                "instance_id": role.instance_id,
                "role": role.name,
                "backend": role.backend,
                "code": code,
                "message": message,
            },
        ))


def _uuid_used_by_live_process(uuid: str) -> bool:
    """Return True if any live process has ``--session-id <uuid>`` in its
    argv. Used as a safety gate before purging stale claude state — we
    never want to clear the lock that an actually-running process owns.

    Fails closed: any subprocess error returns True (do not purge).
    """
    try:
        result = subprocess.run(
            ["ps", "-eo", "args"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return uuid in (result.stdout or "")


def purge_stale_claude_session_lock(session_id: str) -> dict[str, list[str]]:
    """Clear the residual locks that make ``claude --session-id <uuid>``
    abort with "Session ID is already in use", returning what was cleared so
    the caller can emit its own telemetry.

    The REAL lock claude CLI checks lives in
    ``~/.claude.json projects.<key>.{lastSessionId,lastHintSessionId}`` (the
    project key is the git toplevel, so one dead worker poisons every replica
    sharing the repo). Two further residuals also read as "in use": the
    conversation jsonl ``~/.claude/projects/<slug>/<uuid>.jsonl`` (r6) and the
    aux dirs ``~/.claude/{session-env,tasks}/<uuid>`` (tidy-up only).

    No-op (returns empty lists) when a live process still owns the uuid — we
    never clear a lock an actually-running process holds, so concurrent real
    sessions are safe. Best-effort: I/O / parse errors are swallowed.
    """
    cleared_keys: list[str] = []
    aux_removed: list[str] = []
    jsonl_archived: list[str] = []
    result = {
        "claude_json_fields_cleared": cleared_keys,
        "aux_paths_removed": aux_removed,
        "jsonl_archived": jsonl_archived,
    }
    if not session_id or _uuid_used_by_live_process(session_id):
        return result
    claude_config = Path.home() / ".claude.json"
    if claude_config.exists():
        try:
            data = json.loads(claude_config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            projects = data.get("projects")
            if isinstance(projects, dict):
                for path_key, proj in projects.items():
                    if not isinstance(proj, dict):
                        continue
                    for field in ("lastSessionId", "lastHintSessionId"):
                        if proj.get(field) == session_id:
                            proj[field] = ""
                            cleared_keys.append(f"{path_key}.{field}")
                if cleared_keys:
                    try:
                        claude_config.write_text(
                            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8",
                        )
                    except OSError:
                        return result
    for aux in (
        Path.home() / ".claude" / "session-env" / session_id,
        Path.home() / ".claude" / "tasks" / session_id,
    ):
        if aux.exists():
            try:
                shutil.rmtree(aux, ignore_errors=True)
                aux_removed.append(str(aux))
            except OSError:
                pass
    projects_root = Path.home() / ".claude" / "projects"
    if projects_root.exists():
        for project_dir in projects_root.iterdir():
            if not project_dir.is_dir():
                continue
            jsonl_path = project_dir / f"{session_id}.jsonl"
            if not jsonl_path.exists():
                continue
            archive_path = project_dir / (
                f"{session_id}.jsonl.archived-{int(time.time())}"
            )
            try:
                jsonl_path.rename(archive_path)
                jsonl_archived.append(str(archive_path))
            except OSError:
                pass
    return result


def _dismiss_codex_update_prompt(codex_home: Path | None = None) -> None:
    """Suppress codex's blocking "Update available" prompt at TUI start.

    Codex stores its update state in ``~/.codex/version.json``:
        {"latest_version": "X", "last_checked_at": "...",
         "dismissed_version": null|"X"}
    When ``latest_version != dismissed_version`` (or dismissed is null
    while latest is set), the TUI shows a 3-option blocking prompt
    asking the user to update / skip / skip-until-next-version. That
    prompt cannot be driven reliably via send-keys before the actual
    input prompt is ready. Setting dismissed_version=latest_version
    converts the prompt into a non-blocking informational banner.

    Failures here are non-fatal — the worst case is the user sees the
    update prompt and must dismiss it manually once.
    """
    path = (codex_home or Path(os.path.expanduser("~/.codex"))) / "version.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    latest = data.get("latest_version")
    if not latest:
        return
    if data.get("dismissed_version") == latest:
        return
    data["dismissed_version"] = latest
    try:
        path.write_text(json.dumps(data))
    except OSError:
        pass


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

    project_key = f'[projects.{_toml_basic_string(str(project_root))}]'
    block_lines = [_ZAOFU_CODEX_RUNTIME_BEGIN.rstrip()]
    if project_key not in base:
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
    config_path.write_text(base + "\n".join(block_lines) + "\n", encoding="utf-8")


def _list_codex_project_hook_states(
    *,
    codex_home: Path,
    project_root: Path,
    timeout_s: float = 45.0,
) -> list[tuple[str, str]]:
    """Ask the installed Codex app-server for hook keys and current hashes."""
    project_hooks = str(project_root / ".codex" / "hooks.json")
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "zaofu", "version": "0"},
                "capabilities": {"experimentalApi": True},
            },
        },
        {"jsonrpc": "2.0", "method": "initialized"},
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "hooks/list",
            "params": {"cwds": [str(project_root)]},
        },
    ]
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    proc: subprocess.Popen[str] | None = None
    lines: queue.Queue[str] = queue.Queue()
    try:
        proc = subprocess.Popen(
            ["codex", "app-server", "--listen", "stdio://", "--enable", "hooks"],
            stdout=subprocess.PIPE,
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=project_root,
            env=env,
        )
        assert proc.stdin is not None and proc.stdout is not None

        def _read_stdout() -> None:
            assert proc is not None and proc.stdout is not None
            for raw_line in proc.stdout:
                lines.put(raw_line)

        threading.Thread(target=_read_stdout, daemon=True).start()
        proc.stdin.write(payload)
        proc.stdin.flush()
    except OSError:
        if proc is not None:
            try:
                proc.kill()
                proc.communicate(timeout=1)
            except Exception:
                pass
        return []

    states: list[tuple[str, str]] = []
    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline:
            try:
                line = lines.get(timeout=0.1).strip()
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if not line.startswith("{"):
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != 1:
                continue
            for entry in (message.get("result") or {}).get("data", []):
                for hook in entry.get("hooks", []):
                    if str(hook.get("sourcePath")) != project_hooks:
                        continue
                    key = str(hook.get("key") or "")
                    current_hash = str(hook.get("currentHash") or "")
                    if key and current_hash:
                        states.append((key, current_hash))
            break
    finally:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
    return states
