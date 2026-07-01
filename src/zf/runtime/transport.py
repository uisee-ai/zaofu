"""Transport abstraction for agent I/O.

BackendAdapter answers "what argv do I spawn?". TransportAdapter
answers "how do I move bytes to/from a spawned agent?".

Inbound task-completion events still flow exclusively through
.zf/events.jsonl (via `zf emit`). Transports may additionally surface
structured side-channel events (e.g. stream-json tool.use / tool.result)
through poll_events(); the EventLog remains the single source of truth.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.events.model import ZfEvent
from zf.runtime.provider_context import has_provider_context_exhausted
from zf.runtime.tmux import TmuxError, TmuxSession


@dataclass
class AttachHandle:
    """argv to exec for `zf attach`. Empty argv means no live attach is supported."""

    argv: list[str] = field(default_factory=list)
    note: str = ""


@dataclass(frozen=True)
class WorkerLifecycleSnapshot:
    """Read-only runner/process observation for one worker slot."""

    role_name: str
    alive: bool
    pane_pid: str = ""
    current_command: str = ""
    current_path: str = ""
    process_probe: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return {
            "role_name": self.role_name,
            "alive": self.alive,
            "pane_pid": self.pane_pid,
            "current_command": self.current_command,
            "current_path": self.current_path,
            "process_probe": self.process_probe,
        }


@dataclass(frozen=True)
class DispatchContext:
    """Stable context for one task delivery through a transport/provider."""

    trace_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    role_name: str | None = None
    instance_id: str | None = None
    backend: str | None = None
    briefing_path: Path | None = None
    dispatch_id: str | None = None

    def to_payload(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        if self.trace_id:
            payload["trace_id"] = self.trace_id
        if self.run_id:
            payload["run_id"] = self.run_id
        if self.task_id:
            payload["task_id"] = self.task_id
        if self.role_name:
            payload["role"] = self.role_name
        if self.instance_id:
            payload["instance_id"] = self.instance_id
        if self.backend:
            payload["backend"] = self.backend
        if self.briefing_path:
            payload["briefing"] = str(self.briefing_path)
        if self.dispatch_id:
            payload["dispatch_id"] = self.dispatch_id
        return payload


class TransportAdapter(ABC):
    @abstractmethod
    def init(self, *, exclude_roles: set[str] | None = None) -> None:
        """Create the underlying session / connection. Idempotent."""

    @abstractmethod
    def is_session_running(self) -> bool:
        """True if a live harness session exists for this transport."""

    @abstractmethod
    def spawn(
        self,
        role: RoleConfig,
        argv: list[str],
        *,
        cwd: Path | None = None,
    ) -> None:
        """Allocate the role's pane/process and launch the agent CLI."""

    @abstractmethod
    def is_alive(self, role_name: str) -> bool: ...

    @abstractmethod
    def wait_ready(self, role_name: str, pattern: str, timeout: float) -> bool:
        """Block until the agent is accepting input."""

    @abstractmethod
    def send_task(
        self,
        role_name: str,
        briefing_path: Path,
        prompt: str,
        *,
        context: DispatchContext | None = None,
    ) -> None:
        """Deliver a task briefing as a user-turn prompt."""

    def compact_context(self, role_name: str, command: str) -> bool:
        """Request provider-native context compaction when supported."""
        return False

    @abstractmethod
    def capture_log(self, role_name: str, lines: int = 200) -> str:
        """Recent agent output for .zf/logs/{role}.log."""

    @abstractmethod
    def poll_events(self) -> list[ZfEvent]:
        """Drain transport-side structured events. TmuxTransport returns []."""

    @abstractmethod
    def attach_handle(self, role_name: str | None) -> AttachHandle: ...

    @abstractmethod
    def terminate(self, role_name: str) -> None: ...

    @abstractmethod
    def shutdown(self, *, exclude_roles: set[str] | None = None) -> None: ...


class TmuxTransport(TransportAdapter):
    """Run each role in its own tmux window. Wraps TmuxSession."""

    _SHELL_COMMANDS = frozenset({"", "bash", "sh", "zsh", "fish", "dash"})
    _AGENT_ENV_KEYS = (
        "PYTHONPATH",
        "PATH",
        "VIRTUAL_ENV",
        "ZF_PROJECT_ROOT",
        "ZF_STATE_DIR",
        "ZF_CLI_CMD",
    )

    def __init__(self, tmux: TmuxSession) -> None:
        self.tmux = tmux
        self._expected_cwds: dict[str, Path] = {}

    @property
    def session_name(self) -> str:
        return self.tmux.session_name

    @property
    def dry_run(self) -> bool:
        return self.tmux.dry_run

    def init(self, *, exclude_roles: set[str] | None = None) -> None:
        if not self.tmux.dry_run and self.tmux.has_session():
            self.tmux.kill_session()
        self.tmux.create_session()

    def is_session_running(self) -> bool:
        return self.tmux.has_session()

    def spawn(
        self,
        role: RoleConfig,
        argv: list[str],
        *,
        cwd: Path | None = None,
    ) -> None:
        # 1206 Phase B: delegate slot allocation to the active layout.
        # WindowPerRoleLayout issues new-window (legacy behavior),
        # PaneGridLayout issues new-window for the first role and
        # split-window for every subsequent role.
        self.tmux.layout.create_slot(self.tmux, role)
        if cwd is not None:
            expected = Path(cwd).resolve()
            self._expected_cwds[role.instance_id] = expected
            recorder = getattr(self.tmux.layout, "record_cwd", None)
            if recorder is not None:
                try:
                    recorder(self.tmux, role.instance_id, expected)
                except Exception:
                    pass
        if not self.tmux.dry_run:
            # shlex.join quotes argv elements that contain shell
            # metacharacters (parens, spaces, globs). Without this,
            # tokens like `Bash(zf kanban *)` break the shell command
            # line with "syntax error near unexpected token '('".
            command = shlex.join(argv)
            env_prefix = self._agent_env_prefix()
            if env_prefix:
                command = f"{env_prefix} {command}"
            if cwd is not None:
                command = f"cd {shlex.quote(str(cwd))} && {command}"
            self.tmux.send_keys(role.instance_id, command)

    @classmethod
    def _agent_env_prefix(cls) -> str:
        """Carry selected launch env into tmux panes.

        tmux panes inherit the long-lived tmux server environment, not
        reliably the current ``zf start`` process environment. A stale server
        can therefore launch Codex without the project PYTHONPATH; commands
        such as ``zf emit`` then import an older installed ZaoFu. Keep this
        narrow: only runtime resolution variables, never arbitrary secrets.
        """
        assignments: list[str] = []
        for key in cls._AGENT_ENV_KEYS:
            value = os.environ.get(key)
            if value:
                assignments.append(shlex.quote(f"{key}={value}"))
        if not assignments:
            return ""
        return "/usr/bin/env " + " ".join(assignments)

    def pane_current_command(self, role_name: str) -> str:
        return self.tmux.pane_current_command(role_name)

    def pane_current_path(self, role_name: str) -> str:
        getter = getattr(self.tmux, "pane_current_path", None)
        if getter is None:
            return ""
        return str(getter(role_name))

    def lifecycle_snapshot(self, role_name: str) -> WorkerLifecycleSnapshot:
        pid_getter = getattr(self.tmux, "pane_pid", None)
        pane_pid = ""
        if pid_getter is not None:
            try:
                pane_pid = str(pid_getter(role_name) or "")
            except Exception:
                pane_pid = ""
        try:
            current_command = self.pane_current_command(role_name).strip()
        except Exception:
            current_command = ""
        try:
            current_path = self.pane_current_path(role_name).strip()
        except Exception:
            current_path = ""
        try:
            alive = self._agent_process_alive(role_name)
        except Exception:
            alive = False
        process_probe = self._pane_process_probe(role_name)
        return WorkerLifecycleSnapshot(
            role_name=role_name,
            alive=alive,
            pane_pid=pane_pid,
            current_command=current_command,
            current_path=current_path,
            process_probe=process_probe,
        )

    def _agent_process_alive(self, role_name: str) -> bool:
        alive, _reason = self._agent_process_liveness(role_name)
        return alive

    def _agent_process_liveness(self, role_name: str) -> tuple[bool, str]:
        if self.tmux.dry_run:
            return True, ""
        if not self.tmux.pane_alive(role_name):
            return False, "pane_dead"
        command = self.pane_current_command(role_name).strip()
        leaf = command.rsplit("/", 1)[-1].strip().lower()
        if leaf in self._SHELL_COMMANDS:
            return False, "shell"
        if self._pane_has_provider_context_exhausted(role_name):
            return False, "provider_context_exhausted"
        if leaf == "node":
            probe = self._pane_process_probe(role_name)
            if self._probe_contains_codex(probe):
                return True, ""
            if bool(probe.get("available")) and probe.get("processes"):
                return False, "node_without_agent_wrapper"
        return True, ""

    def _pane_process_probe(self, role_name: str) -> dict[str, object]:
        probe = {
            "available": False,
            "pane_pid": "",
            "current_command": "",
            "processes": [],
            "agent_markers": [],
        }
        try:
            probe["pane_pid"] = str(getattr(self.tmux, "pane_pid")(role_name))
        except Exception:
            pass
        try:
            probe["current_command"] = self.pane_current_command(role_name).strip()
        except Exception:
            pass
        getter = getattr(self.tmux, "pane_process_probe", None)
        if callable(getter):
            try:
                raw = getter(role_name)
                if isinstance(raw, dict):
                    probe.update(raw)
            except Exception:
                pass
        markers = self._agent_markers(probe)
        probe["agent_markers"] = markers
        return probe

    @staticmethod
    def _agent_markers(probe: dict[str, object]) -> list[str]:
        rows = probe.get("processes")
        if not isinstance(rows, list):
            return []
        markers: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            command = str(row.get("command") or "")
            if _command_contains_executable(command, "codex"):
                markers.append("codex")
            if _command_contains_executable(command, "claude"):
                markers.append("claude")
        return sorted(set(markers))

    @classmethod
    def _probe_contains_codex(cls, probe: dict[str, object]) -> bool:
        markers = probe.get("agent_markers")
        if isinstance(markers, list) and "codex" in {str(item) for item in markers}:
            return True
        rows = probe.get("processes")
        if not isinstance(rows, list):
            return False
        return any(
            isinstance(row, dict)
            and _command_contains_executable(str(row.get("command") or ""), "codex")
            for row in rows
        )

    def _pane_has_provider_context_exhausted(self, role_name: str) -> bool:
        try:
            output = self.tmux.capture_pane(role_name, lines=120)
        except Exception:
            return False
        return has_provider_context_exhausted(output)

    def is_alive(self, role_name: str) -> bool:
        return self._agent_process_alive(role_name)

    def wait_ready(self, role_name: str, pattern: str, timeout: float) -> bool:
        return self.tmux.wait_for_prompt(role_name, pattern, timeout=timeout)

    def send_task(
        self,
        role_name: str,
        briefing_path: Path,
        prompt: str,
        *,
        context: DispatchContext | None = None,
    ) -> None:
        alive, dead_reason = self._agent_process_liveness(role_name)
        if not alive:
            command = self.pane_current_command(role_name).strip()
            probe = self._pane_process_probe(role_name)
            err = TmuxError(
                f"refusing to send task to {role_name}: "
                f"pane is not running an agent process "
                f"(current_command={command or 'unknown'}, "
                f"reason={dead_reason or 'unknown'})"
            )
            setattr(err, "backend", context.backend if context else "")
            setattr(err, "current_command", command)
            setattr(err, "process_probe", probe)
            setattr(err, "dead_reason", dead_reason)
            raise err
        self._assert_expected_cwd(role_name)
        self.tmux.send_keys(role_name, prompt)

    def compact_context(self, role_name: str, command: str) -> bool:
        if not command:
            return False
        if not self._agent_process_alive(role_name):
            return False
        self._assert_expected_cwd(role_name)
        self.tmux.send_keys(role_name, command)
        return True

    def _expected_cwd_for(self, role_name: str) -> Path | None:
        expected = self._expected_cwds.get(role_name)
        if expected is not None:
            return expected
        layout = getattr(self.tmux, "layout", None)
        getter = getattr(layout, "expected_cwd", None)
        if getter is None:
            return None
        try:
            raw = str(getter(role_name) or "")
        except Exception:
            return None
        if not raw:
            return None
        try:
            expected = Path(raw).resolve()
        except Exception:
            expected = Path(raw)
        self._expected_cwds[role_name] = expected
        return expected

    def _assert_expected_cwd(self, role_name: str) -> None:
        if self.tmux.dry_run:
            return
        expected = self._expected_cwd_for(role_name)
        if expected is None:
            return
        actual_raw = self.pane_current_path(role_name).strip()
        if not actual_raw:
            raise TmuxError(
                f"refusing to send task to {role_name}: "
                f"unable to verify pane cwd, expected {expected}"
            )
        try:
            actual = Path(actual_raw).resolve()
        except Exception:
            actual = Path(actual_raw)
        if actual != expected:
            raise TmuxError(
                f"refusing to send task to {role_name}: pane cwd mismatch "
                f"(expected={expected}, actual={actual})"
            )

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return self.tmux.capture_pane(role_name, lines=lines)

    def poll_events(self) -> list[ZfEvent]:
        return []

    def attach_handle(self, role_name: str | None) -> AttachHandle:
        if role_name:
            # 1206 Phase B-T3: under pane_grid, roles share one window
            # and live in separate panes, so attach selects the *pane*
            # instead of the window.
            target = self.tmux.layout.resolve(self.tmux, role_name)
            if target.pane is not None:
                return AttachHandle(
                    argv=["tmux", "select-pane", "-t", target.address()],
                    note=f"select tmux pane {target.address()}",
                )
            return AttachHandle(
                argv=["tmux", "select-window", "-t", target.address()],
                note=f"select tmux window {target.address()}",
            )
        return AttachHandle(
            argv=["tmux", "attach-session", "-t", self.tmux.session_name],
            note=f"attach tmux session {self.tmux.session_name}",
        )

    def terminate(self, role_name: str) -> None:
        terminator = getattr(self.tmux, "terminate_window", None)
        if callable(terminator):
            terminator(role_name)
            return
        self.tmux.kill_window(role_name)

    def shutdown(self, *, exclude_roles: set[str] | None = None) -> None:
        self.tmux.kill_session()


class CompositeTransport(TransportAdapter):
    """Per-instance transport router.

    Each worker *instance* has its own transport entry, keyed by
    ``RoleConfig.instance_id``. For single-instance configs,
    instance_id defaults to name so legacy code paths that pass the
    role name continue to work unchanged.

    The router itself implements TransportAdapter so the orchestrator
    and CLI commands can hold one object without caring about routing.
    """

    def __init__(self, by_role: dict[str, TransportAdapter]) -> None:
        # Key is instance_id (G-INST-3). The parameter is named by_role
        # for backward compatibility with existing test fixtures that
        # construct a router directly.
        self._by_role = by_role

    def for_role(self, role_name: str) -> TransportAdapter:
        # Exact instance_id lookup.
        if role_name in self._by_role:
            return self._by_role[role_name]
        # Legacy fallback: if the caller passed a bare role.name (e.g.
        # "dev") and there is exactly one matching prefix instance_id,
        # return that. Avoids breaking old single-instance callers.
        matches = [k for k in self._by_role if k == role_name or k.startswith(f"{role_name}-")]
        if len(matches) == 1:
            return self._by_role[matches[0]]
        raise KeyError(f"No transport configured for role {role_name!r}")

    def register_role(
        self,
        role: RoleConfig,
        *,
        parent_instance_id: str | None = None,
    ) -> None:
        """Register a runtime-created role with an existing transport.

        Autoscaled workers inherit the transport adapter from their
        template/parent instance. Stream-json transports also need to
        learn the new RoleConfig so side-channel attribution stays correct.
        """
        if role.instance_id in self._by_role:
            return
        adapter: TransportAdapter | None = None
        if parent_instance_id:
            adapter = self._by_role.get(parent_instance_id)
        if adapter is None:
            for instance_id, candidate in self._by_role.items():
                if instance_id == role.name or instance_id.startswith(f"{role.name}-"):
                    adapter = candidate
                    break
        if adapter is None:
            distinct = self._distinct()
            adapter = distinct[0] if distinct else None
        if adapter is None:
            raise KeyError(f"No parent transport available for {role.instance_id!r}")
        registrar = getattr(adapter, "register_role", None)
        if callable(registrar):
            registrar(role)
        self._by_role[role.instance_id] = adapter

    def init(self, *, exclude_roles: set[str] | None = None) -> None:
        # init each underlying transport at most once even if multiple roles share one
        for transport, roles in self._transports_with_roles():
            if _all_roles_excluded(roles, exclude_roles):
                continue
            transport.init()

    def is_session_running(self) -> bool:
        return any(t.is_session_running() for t in self._distinct())

    def spawn(
        self,
        role: RoleConfig,
        argv: list[str],
        *,
        cwd: Path | None = None,
    ) -> None:
        self.for_role(role.instance_id).spawn(role, argv, cwd=cwd)

    def is_alive(self, role_name: str) -> bool:
        return self.for_role(role_name).is_alive(role_name)

    def wait_ready(self, role_name: str, pattern: str, timeout: float) -> bool:
        return self.for_role(role_name).wait_ready(role_name, pattern, timeout)

    def send_task(
        self,
        role_name: str,
        briefing_path: Path,
        prompt: str,
        *,
        context: DispatchContext | None = None,
    ) -> None:
        self.for_role(role_name).send_task(
            role_name, briefing_path, prompt, context=context,
        )

    def compact_context(self, role_name: str, command: str) -> bool:
        return self.for_role(role_name).compact_context(role_name, command)

    def pane_current_command(self, role_name: str) -> str:
        transport = self.for_role(role_name)
        getter = getattr(transport, "pane_current_command", None)
        if getter is None:
            return ""
        try:
            return str(getter(role_name))
        except Exception:
            return ""

    def pane_process_probe(self, role_name: str) -> dict[str, object]:
        transport = self.for_role(role_name)
        getter = getattr(transport, "_pane_process_probe", None)
        if getter is None:
            getter = getattr(transport, "pane_process_probe", None)
        if getter is None:
            return {}
        try:
            result = getter(role_name)
        except Exception:
            return {}
        return result if isinstance(result, dict) else {}

    def capture_log(self, role_name: str, lines: int = 200) -> str:
        return self.for_role(role_name).capture_log(role_name, lines=lines)

    def poll_events(self) -> list[ZfEvent]:
        out: list[ZfEvent] = []
        for t in self._distinct():
            out.extend(t.poll_events())
        return out

    def attach_handle(self, role_name: str | None) -> AttachHandle:
        if role_name is None:
            # No specific role: return the first transport's session-level handle
            for t in self._distinct():
                return t.attach_handle(None)
            return AttachHandle()
        return self.for_role(role_name).attach_handle(role_name)

    def terminate(self, role_name: str) -> None:
        self.for_role(role_name).terminate(role_name)

    def shutdown(self, *, exclude_roles: set[str] | None = None) -> None:
        for transport, roles in self._transports_with_roles():
            if _all_roles_excluded(roles, exclude_roles):
                continue
            transport.shutdown()

    def _distinct(self) -> list[TransportAdapter]:
        seen: set[int] = set()
        out: list[TransportAdapter] = []
        for t in self._by_role.values():
            if id(t) not in seen:
                seen.add(id(t))
                out.append(t)
        return out

    def _transports_with_roles(self) -> list[tuple[TransportAdapter, set[str]]]:
        seen: dict[int, tuple[TransportAdapter, set[str]]] = {}
        order: list[int] = []
        for role_name, transport in self._by_role.items():
            key = id(transport)
            if key not in seen:
                seen[key] = (transport, set())
                order.append(key)
            seen[key][1].add(role_name)
        return [seen[key] for key in order]


def _all_roles_excluded(
    roles: set[str],
    exclude_roles: set[str] | None,
) -> bool:
    if not roles or not exclude_roles:
        return False
    return roles <= set(exclude_roles)


def make_transport(config: ZfConfig, *, dry_run: bool = False) -> TransportAdapter:
    """Build a per-role CompositeTransport from config.

    Roles default to tmux. Any role with `transport: stream-json` in zf.yaml
    gets a StreamJsonTransport instance. All tmux roles share one TmuxSession.
    """
    from pathlib import Path as _Path
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.runtime.transport_stream_json import StreamJsonTransport

    session_name = config.session.tmux_session
    state_dir = _Path.cwd() / config.project.state_dir
    project_root = str(_Path.cwd())
    # 1206: honor session.tmux_layout. One layout instance is shared
    # across every tmux-hosted role so pane indices stay consistent
    # (each TmuxTransport ends up with the same TmuxSession, so the
    # layout travels with it).
    from zf.runtime.tmux_layout import PaneGridLayout, WindowPerRoleLayout
    layout_name = config.session.tmux_layout
    if layout_name == "pane_grid":
        layout = PaneGridLayout(
            window_name="roles",
            binding_path=state_dir / "pane_bindings.json",
        )
    else:
        layout = WindowPerRoleLayout()
    tmux_session = TmuxSession(
        session_name=session_name, dry_run=dry_run, layout=layout,
    )
    tmux_transport = TmuxTransport(tmux_session)

    roles = list(config.roles)
    try:
        from zf.runtime.run_manager_resident import build_resident_run_manager_role

        resident_role = build_resident_run_manager_role(config)
        if resident_role is not None and all(
            role.instance_id != resident_role.instance_id for role in roles
        ):
            roles.append(resident_role)
    except Exception:
        resident_role = None

    by_role: dict[str, TransportAdapter] = {}
    sj_transport: StreamJsonTransport | None = None
    for role in roles:
        # G-INST-3: route by instance_id so replicas of the same role_type
        # get independent transport entries. For single-instance configs
        # this collapses to role.name via RoleConfig.__post_init__.
        if role.transport == "stream-json":
            if sj_transport is None:
                registry = RoleSessionRegistry(
                    state_dir / "role_sessions.yaml", project_root=project_root
                )
                sj_transport = StreamJsonTransport(
                    state_dir, registry, cwd=_Path(project_root),
                    timeout_s=config.orchestrator.transport_timeout_s,
                    max_turns=config.orchestrator.max_turns,
                )
            sj_transport.register_role(role)
            by_role[role.instance_id] = sj_transport
        else:
            by_role[role.instance_id] = tmux_transport
    try:
        from zf.runtime.run_manager_resident import (
            build_resident_run_manager_role,
            resident_run_manager_session_mode,
            resident_run_manager_tmux_session,
        )

        resident_role = build_resident_run_manager_role(config)
    except ImportError:
        resident_role = None
    if resident_role is not None and all(
        role.instance_id != resident_role.instance_id for role in config.roles
    ):
        if resident_run_manager_session_mode(config) == "dedicated":
            resident_tmux = TmuxSession(
                session_name=resident_run_manager_tmux_session(config),
                dry_run=dry_run,
                layout=WindowPerRoleLayout(),
            )
            by_role[resident_role.instance_id] = TmuxTransport(resident_tmux)
        else:
            by_role[resident_role.instance_id] = tmux_transport
    return CompositeTransport(by_role)


def transport_error_diagnostics(exc: BaseException) -> dict[str, object]:
    """Return structured diagnostics attached by transport failures."""
    out: dict[str, object] = {}
    backend = str(getattr(exc, "backend", "") or "")
    if backend:
        out["backend"] = backend
    current_command = str(getattr(exc, "current_command", "") or "")
    if current_command:
        out["current_command"] = current_command
    dead_reason = str(getattr(exc, "dead_reason", "") or "")
    if dead_reason:
        out["dead_reason"] = dead_reason
    probe = getattr(exc, "process_probe", None)
    if isinstance(probe, dict) and probe:
        out["process_probe"] = probe
    return out


def _command_contains_executable(command: str, executable: str) -> bool:
    try:
        tokens = shlex.split(command) if command else []
    except ValueError:
        tokens = command.split()
    if not tokens:
        return False
    needle = executable.lower()
    for token in tokens:
        leaf = token.rsplit("/", 1)[-1].lower()
        if leaf == needle:
            return True
    return False
