"""Tmux layout strategies — window-per-role vs pane-grid (1206 Phase A).

``TmuxSession`` historically created one tmux window per role instance
(``TmuxSession.create_window``). That works but makes the 6-role W5-E2E
/ 7-role mixed-team sessions hard to observe — a human attaching the
session has to tab through each window individually to watch progress.

This module introduces a ``TmuxLayout`` strategy injected into
``TmuxSession``. Two concrete layouts ship:

- ``WindowPerRoleLayout`` (default): preserves the legacy behavior,
  one window per call. All existing yamls keep working unchanged.
- ``PaneGridLayout`` (opt-in via ``session.tmux_layout: pane_grid``):
  every role lands in the same window, split-window'd into panes. The
  human sees all roles simultaneously.

Phase A ships the abstractions + default layout wiring; Phase B plugs
the real split-window / kill-pane calls into ``PaneGridLayout``; Phase C
ports respawn / recycle to be pane-aware.

PaneTarget is the value object the rest of the session passes around:

    ``session:window``          (pane = None → window-level ops)
    ``session:window.pane``     (pane int → pane-level ops)
    ``%pane_id``                (pane str → stable tmux pane id)

tmux accepts either form; callers no longer hardcode the separator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shlex
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from zf.runtime.tmux import TmuxSession


@dataclass(frozen=True)
class PaneTarget:
    """Immutable ``session:window[.pane]`` address.

    ``pane=None`` maps to window-level tmux targets (legacy semantics);
    ``pane=int`` targets a specific pane index within a shared window;
    ``pane=str`` targets a stable tmux pane id such as ``%42``.
    """

    session: str
    window: str
    pane: int | str | None = None

    def address(self) -> str:
        if self.pane is None:
            return f"{self.session}:{self.window}"
        if isinstance(self.pane, str):
            return self.pane
        return f"{self.session}:{self.window}.{self.pane}"


class TmuxLayout(ABC):
    """Strategy for mapping role instances onto tmux windows/panes."""

    @abstractmethod
    def create_slot(self, session: "TmuxSession", role: Any) -> PaneTarget:
        """Allocate a new slot (window or pane) for ``role`` and return
        a PaneTarget pointing at it. Implementations issue the requisite
        ``tmux new-window`` or ``tmux split-window`` call on ``session``.
        """

    @abstractmethod
    def kill_slot(
        self,
        session: "TmuxSession",
        target: PaneTarget,
        *,
        instance_id: str | None = None,
    ) -> None:
        """Tear down the slot identified by ``target``."""

    @abstractmethod
    def resolve(self, session: "TmuxSession", instance_id: str) -> PaneTarget:
        """Return the PaneTarget for an existing instance.

        Called by ``TmuxSession._target`` so every read/write routes to
        the correct pane under pane_grid without changing caller code.
        If the instance has not been allocated yet, implementations
        return a legacy ``session:instance_id`` target so early-boot
        calls (before create_slot completed) still succeed at the
        window level — this matches pre-1206 behavior.
        """

    def address(self, session: "TmuxSession", instance_id: str) -> str:
        """Convenience: ``resolve(...).address()``. Used by TmuxSession
        primitives when they only need the string form."""
        return self.resolve(session, instance_id).address()

    def target_owned_by(
        self,
        session: "TmuxSession",
        target: PaneTarget,
        instance_id: str,
    ) -> bool:
        """Return whether ``target`` still belongs to ``instance_id``.

        Window-per-role targets encode ownership in the window name. Shared
        pane layouts override this with a stable tmux pane identity check.
        """
        return target.pane is None and target.window == instance_id

    def owned_pane_pid(
        self, session: "TmuxSession", target: PaneTarget, instance_id: str,
    ) -> str:
        """Read a PID only after this layout validates its target ownership."""
        if not self.target_owned_by(session, target, instance_id):
            return ""
        return session._pane_pid_for_target(target)

    def owned_pane_process_status(
        self, session: "TmuxSession", target: PaneTarget, instance_id: str,
    ) -> tuple[str, str]:
        """Keep legacy window reads; pane layouts provide an owned snapshot."""
        pid = self.owned_pane_pid(session, target, instance_id)
        return (pid, session.pane_current_command(instance_id)) if pid else ("", "")

    def owned_pane_process_status_for_instance(
        self, session: "TmuxSession", instance_id: str,
    ) -> tuple[str, str]:
        """Observe one instance while retaining the layout safety boundary."""
        target = self.resolve(session, instance_id)
        return self.owned_pane_process_status(session, target, instance_id)


class WindowPerRoleLayout(TmuxLayout):
    """Default: one tmux window per role instance (legacy behavior).

    ``pane`` is always None so existing target-formatting callers that
    expect ``session:window`` strings remain correct.
    """

    def create_slot(self, session: "TmuxSession", role: Any) -> PaneTarget:
        name = getattr(role, "instance_id", None) or role.name
        session.create_window(name)
        return PaneTarget(session=session.session_name, window=name, pane=None)

    def kill_slot(
        self,
        session: "TmuxSession",
        target: PaneTarget,
        *,
        instance_id: str | None = None,
    ) -> None:
        session.kill_window(target.window)

    def resolve(self, session: "TmuxSession", instance_id: str) -> PaneTarget:
        return PaneTarget(
            session=session.session_name,
            window=instance_id,
            pane=None,
        )


class PaneGridLayout(TmuxLayout):
    """All roles share one window, each in its own pane.

    Dry-runs keep pane-index bookkeeping so tests can assert readable
    ``session:roles.0`` targets. Real tmux runs use stable ``%pane_id``
    targets because tmux renumbers pane indexes after every kill-pane;
    storing indexes in a long-running harness would eventually route
    send/kill operations to the wrong worker after respawn/recycle.
    """

    def __init__(
        self,
        window_name: str = "roles",
        *,
        binding_path: Path | None = None,
    ) -> None:
        self.window_name = window_name
        self.binding_path = binding_path
        self._panes: dict[str, int | str] = {}
        self._expected_cwds: dict[str, str] = {}
        self._free: list[int] = []
        self._next: int = 0
        self._window_created = False
        self._window_id: str | None = None

    def _window_target(self, session: "TmuxSession") -> str:
        if not session.dry_run:
            window_id = self._window_id or self._find_shared_window_id(session)
            if window_id:
                self._window_id = window_id
                return window_id
        return f"{session.session_name}:{self.window_name}"

    def create_slot(self, session: "TmuxSession", role: Any) -> PaneTarget:
        name = getattr(role, "instance_id", None) or role.name
        existing = self._existing_target(session, name)
        if existing is not None:
            self.kill_slot(session, existing, instance_id=name)

        if not self._window_created and not session.dry_run:
            self._window_id = self._find_shared_window_id(session)
            if self._window_id:
                self._window_created = True

        # First slot creates the window; subsequent slots split it. We
        # target the existing window explicitly so split-window lands
        # inside the grid rather than the currently focused window.
        #
        # After each split run ``select-layout tiled`` so tmux
        # redistributes the pane sizes evenly. Without this, repeatedly
        # splitting the current focus halves each pane in turn and the
        # 5th/6th split aborts with "pane too small" — the run 5 smoke
        # that surfaced this bug in the first pane-grid real run.
        if not self._window_created:
            session.create_window(self.window_name)
            self._window_created = True
            if not session.dry_run:
                self._window_id = self._find_shared_window_id(session)
            if session.dry_run:
                pane: int | str = self._allocate_dry_run_index()
            else:
                pane = self._current_pane_id(session)
                self._set_pane_identity(session, pane, name)
        else:
            target = self._window_target(session)
            if session.dry_run:
                session._run(["tmux", "split-window", "-t", target])
                pane = self._allocate_dry_run_index()
            else:
                try:
                    result = session._run(
                        [
                            "tmux", "split-window",
                            "-P", "-F", "#{pane_id}",
                            "-t", target,
                        ],
                        capture=True,
                    )
                    pane = result.stdout.strip() or self._current_pane_id(session)
                except Exception as exc:
                    if not self._stale_target_error(exc):
                        raise
                    pane = self._recreate_shared_window(session, name)
                    target = self._window_target(session)
                self._set_pane_identity(session, pane, name)
            session._run(["tmux", "select-layout", "-t", target, "tiled"])

        self._panes[name] = pane
        self._record_binding(session, name, pane)
        return PaneTarget(
            session=session.session_name,
            window=self.window_name,
            pane=pane,
        )

    def kill_slot(
        self,
        session: "TmuxSession",
        target: PaneTarget,
        *,
        instance_id: str | None = None,
    ) -> None:
        from zf.runtime.tmux import TmuxError

        if target.pane is None and not session.dry_run:
            pane = self._find_pane_by_title(session, target.window)
            if pane is not None:
                target = PaneTarget(
                    session=session.session_name,
                    window=self.window_name,
                    pane=pane,
                )
        if target.pane is None:
            raise TmuxError("pane-grid slot teardown requires an exact pane target")

        mapped_owner = next(
            (
                name
                for name, pane in self._panes.items()
                if pane == target.pane
            ),
            None,
        )
        expected_owner = str(instance_id or mapped_owner or "").strip()
        if not expected_owner:
            raise TmuxError(
                f"refusing unowned pane-grid teardown for {target.address()}"
            )
        if mapped_owner and mapped_owner != expected_owner:
            raise TmuxError(
                f"pane-grid target {target.address()} is mapped to "
                f"{mapped_owner!r}, not {expected_owner!r}"
            )

        pane_exists = True
        if session.dry_run:
            owned = mapped_owner == expected_owner
        else:
            pane_exists, actual_owner = self._pane_owner(session, str(target.pane))
            owned = pane_exists and actual_owner == expected_owner
        if pane_exists and not owned:
            raise TmuxError(
                f"pane-grid target {target.address()} is not owned by "
                f"{expected_owner!r}"
            )

        idx = self._panes.get(expected_owner)
        if idx == target.pane:
            del self._panes[expected_owner]
            self._expected_cwds.pop(expected_owner, None)
            self._forget_binding(expected_owner)
            if session.dry_run and isinstance(idx, int):
                self._free.append(idx)
                self._free.sort()
        # Kill the actual pane. When the shared window loses its last
        # pane tmux destroys the window, so reset the _window_created
        # flag so the next create_slot rebuilds it.
        if pane_exists:
            session._run(
                ["tmux", "kill-pane", "-t", target.address()],
                check=False,
            )
        if not self._panes:
            # All panes gone → window gone. Reset so we know to
            # new-window on the next create_slot.
            self._window_created = False
            self._window_id = None
            self._next = 0
            self._free.clear()

    def resolve(self, session: "TmuxSession", instance_id: str) -> PaneTarget:
        pane = self._panes.get(instance_id)
        if (
            pane is not None
            and not session.dry_run
            and not self._pane_binding_still_valid(
                session,
                str(pane),
                instance_id,
            )
        ):
            self._panes.pop(instance_id, None)
            self._expected_cwds.pop(instance_id, None)
            self._forget_binding(instance_id)
            pane = None
        if pane is None:
            pane = self._pane_from_binding(session, instance_id)
        if pane is None and not session.dry_run:
            pane = self._find_pane_by_instance(session, instance_id)
            if pane is not None:
                self._panes[instance_id] = pane
                self._window_created = True
                self._set_pane_identity(session, pane, instance_id)
                self._record_binding(session, instance_id, pane)
        if pane is None:
            # Not yet allocated (early-boot calls from wait_ready etc.)
            # — fall back to the legacy session:instance_id form so the
            # call doesn't crash. Post-create_slot calls resolve normally.
            return PaneTarget(
                session=session.session_name,
                window=instance_id,
                pane=None,
            )
        return PaneTarget(
            session=session.session_name,
            window=self.window_name,
            pane=pane,
        )

    def target_owned_by(
        self,
        session: "TmuxSession",
        target: PaneTarget,
        instance_id: str,
    ) -> bool:
        if target.pane is None:
            return False
        if session.dry_run:
            return self._panes.get(instance_id) == target.pane
        pane_exists, owner = self._pane_owner(session, str(target.pane))
        return pane_exists and owner == instance_id

    def owned_pane_pid(
        self, session: "TmuxSession", target: PaneTarget, instance_id: str,
    ) -> str:
        """Bind the final owner check and PID to one tmux server snapshot."""
        if session.dry_run or target.pane is None:
            return ""
        result = session._run(
            ["tmux", "display-message", "-p", "-t", target.address(),
             "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}"],
            check=False, capture=True,
        )
        if result.returncode != 0 or result.stderr.strip():
            return ""
        fields = result.stdout.rstrip("\r\n").split("\t")
        if len(fields) != 3:
            return ""
        pane, owner, pid = fields
        if pane != str(target.pane) or owner.strip() != instance_id:
            return ""
        pid = pid.strip()
        return pid if pid.isdigit() else ""

    def owned_pane_process_status(
        self, session: "TmuxSession", target: PaneTarget, instance_id: str,
    ) -> tuple[str, str]:
        """Read owner, PID and foreground command in the same tmux query."""
        if session.dry_run or target.pane is None:
            return "", ""
        result = session._run(
            ["tmux", "display-message", "-p", "-t", target.address(),
             "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}\t#{pane_current_command}"],
            check=False, capture=True,
        )
        if result.returncode != 0 or result.stderr.strip():
            return "", ""
        fields = result.stdout.rstrip("\r\n").split("\t")
        if len(fields) != 4:
            return "", ""
        pane, owner, pid, command = fields
        if pane != str(target.pane) or owner.strip() != instance_id:
            return "", ""
        pid = pid.strip()
        return (pid, command.strip()) if pid.isdigit() else ("", "")

    def owned_pane_process_status_for_instance(
        self, session: "TmuxSession", instance_id: str,
    ) -> tuple[str, str]:
        """Validate a cached address with a fresh owner/PID/command snapshot."""
        pane = self._panes.get(instance_id)
        if pane is not None and not session.dry_run:
            target = PaneTarget(session.session_name, self.window_name, pane)
            status = self.owned_pane_process_status(session, target, instance_id)
            if status[0]:
                return status
        # Cold/stale/invalid observations retain normal binding invalidation,
        # discovery and the final ownership guard; no liveness result is cached.
        return super().owned_pane_process_status_for_instance(session, instance_id)

    def pane_liveness_snapshot(
        self,
        session: "TmuxSession",
        instance_id: str,
        *,
        lines: int = 120,
    ) -> tuple[str, str, str] | None:
        """Read fresh ownership/process state and bounded context in one tmux call.

        ``None`` deliberately preserves the legacy resolver path.  The result
        is never cached: callers must treat it as one observation only.
        """
        if (
            session.dry_run
            or not self._SAFE_FORMAT_INSTANCE.fullmatch(instance_id)
            or lines <= 0
        ):
            return None
        pane = self._panes.get(instance_id)
        if not isinstance(pane, str) or not re.fullmatch(r"%[0-9]+", pane):
            return None
        target = PaneTarget(session.session_name, self.window_name, pane)
        guard = (
            "#{&&:#{==:#{pane_id}," + str(pane) + "},"
            "#{==:#{@zf_instance_id}," + instance_id + "}}"
        )
        capture = shlex.join(["display-message", "-p", self._CAPTURE_SENTINEL])
        capture += " ; " + shlex.join([
            "capture-pane", "-p", "-t", target.address(), "-S", str(-lines),
        ])
        command = shlex.join([
            "display-message", "-p", "-t", target.address(),
            "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}\t#{pane_current_command}",
        ]) + " ; " + shlex.join([
            "if-shell", "-F", "-t", target.address(), guard, capture,
            "display-message -p __ZF_LIVENESS_REJECTED__",
        ])
        result = session._run(
            [
                "tmux", "if-shell", "-F", "-t", target.address(), guard,
                command, "display-message -p __ZF_LIVENESS_REJECTED__",
            ],
            check=False,
            capture=True,
        )
        if result.returncode != 0 or result.stderr.strip():
            return None
        header, separator, body = result.stdout.partition("\n")
        if not separator or not body.startswith(self._CAPTURE_SENTINEL + "\n"):
            return None
        fields = header.split("\t")
        if len(fields) != 4:
            return None
        observed_pane, owner, pid, command_name = fields
        if observed_pane != pane or owner.strip() != instance_id or not pid.isdigit():
            return None
        return pid, command_name.strip(), body[len(self._CAPTURE_SENTINEL) + 1:]

    def pane_liveness_and_log_snapshot(
        self,
        session: "TmuxSession",
        instance_id: str,
        *,
        liveness_lines: int = 120,
        log_lines: int = 200,
    ) -> tuple[str, str, str, str] | None:
        """Read the watchdog's two independent windows in one tmux client."""
        if (
            session.dry_run
            or not self._SAFE_FORMAT_INSTANCE.fullmatch(instance_id)
            or liveness_lines <= 0
            or log_lines <= 0
        ):
            return None
        pane = self._panes.get(instance_id)
        if not isinstance(pane, str) or not re.fullmatch(r"%[0-9]+", pane):
            return None
        target = PaneTarget(session.session_name, self.window_name, pane)
        guard = (
            "#{&&:#{==:#{pane_id}," + str(pane) + "},"
            "#{==:#{@zf_instance_id}," + instance_id + "}}"
        )
        marker_120 = "__ZF_CAPTURE_120_OK__"
        marker_200 = "__ZF_CAPTURE_200_OK__"
        marker_end = "__ZF_CAPTURE_PAIR_END__"
        capture_120 = shlex.join(["display-message", "-p", marker_120])
        capture_120 += " ; " + shlex.join([
            "capture-pane", "-p", "-t", target.address(),
            "-S", str(-liveness_lines),
        ])
        capture_200 = shlex.join(["display-message", "-p", marker_200])
        capture_200 += " ; " + shlex.join([
            "capture-pane", "-p", "-t", target.address(),
            "-S", str(-log_lines),
        ])
        capture_200 += " ; " + shlex.join(["display-message", "-p", marker_end])
        command = shlex.join([
            "display-message", "-p", "-t", target.address(),
            "#{pane_id}\t#{@zf_instance_id}\t#{pane_pid}\t#{pane_current_command}",
        ])
        command += " ; " + shlex.join([
            "if-shell", "-F", "-t", target.address(), guard,
            capture_120, "display-message -p __ZF_CAPTURE_120_REJECTED__",
        ])
        command += " ; " + shlex.join([
            "if-shell", "-F", "-t", target.address(), guard,
            capture_200, "display-message -p __ZF_CAPTURE_200_REJECTED__",
        ])
        result = session._run(
            [
                "tmux", "if-shell", "-F", "-t", target.address(), guard,
                command, "display-message -p __ZF_LIVENESS_REJECTED__",
            ],
            check=False,
            capture=True,
        )
        if result.returncode != 0 or result.stderr.strip():
            return None
        header, separator, remainder = result.stdout.partition("\n")
        fields = header.split("\t")
        if not separator or len(fields) != 4:
            return None
        observed_pane, owner, pid, command_name = fields
        if observed_pane != pane or owner.strip() != instance_id or not pid.isdigit():
            return None
        marker120 = marker_120 + "\n"
        marker200 = marker_200 + "\n"
        end = marker_end + "\n"
        # A pane can print protocol-looking text. Reject ambiguous framing
        # instead of silently cutting context or assigning it to the log.
        if (
            not remainder.startswith(marker120)
            or not remainder.endswith(end)
            or any(remainder.count(marker) != 1 for marker in (marker120, marker200, end))
        ):
            return None
        context_and_log = remainder[len(marker120):-len(end)]
        context, marker_separator, log = context_and_log.partition(marker200)
        if not marker_separator:
            return None
        return pid, command_name.strip(), context, log

    _SAFE_FORMAT_INSTANCE = re.compile(r"^[A-Za-z0-9_.-]+$")
    _CAPTURE_SENTINEL = "__ZF_CAPTURE_OK__"

    def capture_owned_pane(
        self,
        session: "TmuxSession",
        instance_id: str,
        *,
        lines: int,
    ) -> str | None:
        """Fresh server-side guard; None retains normal resolver recovery."""
        if session.dry_run or not self._SAFE_FORMAT_INSTANCE.fullmatch(instance_id):
            return None
        pane = self._panes.get(instance_id)
        if not isinstance(pane, str) or not re.fullmatch(r"%[0-9]+", pane):
            return None
        target = PaneTarget(session.session_name, self.window_name, pane)
        guard = (
            "#{&&:#{==:#{pane_id}," + str(pane) + "},"
            "#{==:#{@zf_instance_id}," + instance_id + "}}"
        )
        sentinel = self._CAPTURE_SENTINEL
        capture = shlex.join(["display-message", "-p", sentinel]) + " ; "
        capture += shlex.join([
            "capture-pane", "-p", "-t", target.address(), "-S", str(-lines),
        ])
        result = session._run(
            [
                "tmux", "if-shell", "-F", "-t", target.address(), guard,
                capture, "display-message -p __ZF_CAPTURE_REJECTED__",
            ],
            check=False,
            capture=True,
        )
        if result.returncode != 0 or result.stderr.strip():
            return None
        marker = sentinel + "\n"
        output = result.stdout
        if not output.startswith(marker):
            return None
        return output[len(marker):]

    def record_cwd(
        self,
        session: "TmuxSession",
        instance_id: str,
        cwd: Path | str | None,
    ) -> None:
        """Persist the expected workdir for a role instance.

        The pane binding is useful after an orchestrator restart; the cwd
        binding lets transport fail closed if a recovered pane points at the
        wrong worktree.
        """
        if cwd is None:
            return
        value = str(cwd)
        self._expected_cwds[instance_id] = value
        pane = self._panes.get(instance_id)
        if pane is not None:
            self._record_binding(session, instance_id, pane, cwd=value)

    def expected_cwd(self, instance_id: str) -> str:
        if instance_id in self._expected_cwds:
            return self._expected_cwds[instance_id]
        entry = self._binding_entry(instance_id)
        cwd = str(entry.get("cwd") or "") if entry else ""
        if cwd:
            self._expected_cwds[instance_id] = cwd
        return cwd

    def _allocate_dry_run_index(self) -> int:
        if self._free:
            return self._free.pop(0)
        idx = self._next
        self._next += 1
        return idx

    def _current_pane_id(self, session: "TmuxSession") -> str:
        result = session._run(
            [
                "tmux", "display-message",
                "-p", "-t", self._window_target(session),
                "#{pane_id}",
            ],
            capture=True,
        )
        return result.stdout.strip()

    @staticmethod
    def _stale_target_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "can't find window",
                "can't find session",
                "can't find pane",
            )
        )

    def _recreate_shared_window(
        self,
        session: "TmuxSession",
        instance_id: str,
    ) -> str:
        self._forget_all_bindings()
        self._panes.clear()
        self._expected_cwds.clear()
        self._free.clear()
        self._next = 0
        self._window_id = None
        self._window_created = False

        session.create_window(self.window_name)
        self._window_created = True
        self._window_id = self._find_shared_window_id(session)
        pane = self._current_pane_id(session)
        self._set_pane_identity(session, pane, instance_id)
        return pane

    def _find_shared_window_id(self, session: "TmuxSession") -> str | None:
        result = session._run(
            [
                "tmux", "list-windows",
                "-t", session.session_name,
                "-F", "#{window_id}\t#{window_name}\t#{window_panes}",
            ],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            return None
        candidates: list[tuple[int, str]] = []
        for line in result.stdout.splitlines():
            window_id, window_name, pane_count = (line.split("\t") + ["", "", ""])[:3]
            if window_name != self.window_name or not window_id:
                continue
            try:
                count = int(pane_count)
            except ValueError:
                count = 0
            candidates.append((count, window_id))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][1]

    def _existing_target(
        self,
        session: "TmuxSession",
        instance_id: str,
    ) -> PaneTarget | None:
        pane = self._panes.get(instance_id)
        if pane is None:
            pane = self._pane_from_binding(session, instance_id)
        if pane is None and not session.dry_run:
            pane = self._find_pane_by_instance(session, instance_id)
            if pane is not None:
                self._panes[instance_id] = pane
                self._window_created = True
                self._set_pane_identity(session, pane, instance_id)
                self._record_binding(session, instance_id, pane)
        if pane is None:
            return None
        return PaneTarget(
            session=session.session_name,
            window=self.window_name,
            pane=pane,
        )

    def _set_pane_identity(
        self,
        session: "TmuxSession",
        pane: int | str,
        instance_id: str,
    ) -> None:
        address = PaneTarget(
            session=session.session_name,
            window=self.window_name,
            pane=pane,
        ).address()
        session._run(
            [
                "tmux", "select-pane",
                "-t", address,
                "-T", instance_id,
            ],
            check=False,
        )
        session._run(
            [
                "tmux", "set-option",
                "-p",
                "-t", address,
                "@zf_instance_id", instance_id,
            ],
            check=False,
        )

    def _pane_from_binding(
        self,
        session: "TmuxSession",
        instance_id: str,
    ) -> int | str | None:
        entry = self._binding_entry(instance_id)
        if not entry:
            return None
        pane = entry.get("pane")
        if pane is None:
            pane = entry.get("pane_id")
        if pane is None:
            return None
        if not session.dry_run and not self._pane_binding_still_valid(
            session, str(pane), instance_id,
        ):
            self._forget_binding(instance_id)
            return None
        self._panes[instance_id] = pane
        cwd = str(entry.get("cwd") or "")
        if cwd:
            self._expected_cwds[instance_id] = cwd
        self._window_created = True
        return pane

    def _pane_binding_still_valid(
        self,
        session: "TmuxSession",
        pane: str,
        instance_id: str,
    ) -> bool:
        exists, owner = self._pane_owner(session, pane)
        return exists and owner == instance_id

    def _pane_owner(
        self,
        session: "TmuxSession",
        pane: str,
    ) -> tuple[bool, str]:
        result = session._run(
            [
                "tmux", "display-message",
                "-p", "-t", pane,
                "#{pane_id}\t#{@zf_instance_id}",
            ],
            check=False,
            capture=True,
        )
        if result.returncode != 0 or result.stderr.strip():
            return False, ""
        observed_pane, separator, owner = result.stdout.strip().partition("\t")
        if not separator or observed_pane != pane:
            return False, ""
        return True, owner.strip()

    def _binding_entry(self, instance_id: str) -> dict[str, object]:
        if self.binding_path is None:
            return {}
        data = self._read_bindings()
        roles = data.get("roles")
        if not isinstance(roles, dict):
            return {}
        entry = roles.get(instance_id)
        return entry if isinstance(entry, dict) else {}

    def _read_bindings(self) -> dict[str, object]:
        if self.binding_path is None or not self.binding_path.exists():
            return {}
        try:
            data = json.loads(self.binding_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_bindings(self, data: dict[str, object]) -> None:
        if self.binding_path is None:
            return
        try:
            self.binding_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.binding_path.with_suffix(self.binding_path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            tmp.replace(self.binding_path)
        except Exception:
            return

    def _record_binding(
        self,
        session: "TmuxSession",
        instance_id: str,
        pane: int | str,
        *,
        cwd: str = "",
    ) -> None:
        if self.binding_path is None:
            return
        data = self._read_bindings()
        roles = data.setdefault("roles", {})
        if not isinstance(roles, dict):
            roles = {}
            data["roles"] = roles
        previous = roles.get(instance_id)
        previous_cwd = ""
        if isinstance(previous, dict):
            previous_cwd = str(previous.get("cwd") or "")
        roles[instance_id] = {
            "pane": pane,
            "session": session.session_name,
            "window": self.window_name,
            "cwd": cwd or previous_cwd,
        }
        data["session"] = session.session_name
        data["window"] = self.window_name
        self._write_bindings(data)

    def _forget_binding(self, instance_id: str) -> None:
        if self.binding_path is None:
            return
        data = self._read_bindings()
        roles = data.get("roles")
        if isinstance(roles, dict) and instance_id in roles:
            roles.pop(instance_id, None)
            self._write_bindings(data)

    def _forget_all_bindings(self) -> None:
        if self.binding_path is None:
            return
        data = self._read_bindings()
        roles = data.get("roles")
        if isinstance(roles, dict) and roles:
            roles.clear()
            self._write_bindings(data)

    def _find_pane_by_instance(
        self,
        session: "TmuxSession",
        instance_id: str,
    ) -> str | None:
        result = session._run(
            [
                "tmux", "list-panes",
                "-t", self._window_target(session),
                "-F",
                "#{pane_id}\t#{@zf_instance_id}\t#{pane_title}\t#{pane_current_path}",
            ],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            return None
        workdir_marker = f"/.zf/workdirs/{instance_id}/"
        for line in result.stdout.splitlines():
            parts = line.split("\t", 3)
            while len(parts) < 4:
                parts.append("")
            pane_id, opt_instance, pane_title, pane_path = parts
            if not pane_id:
                continue
            if opt_instance == instance_id:
                return pane_id
            if pane_title == instance_id:
                return pane_id
            if workdir_marker in pane_path:
                return pane_id
        return None

    def _find_pane_by_title(
        self,
        session: "TmuxSession",
        title: str,
    ) -> str | None:
        return self._find_pane_by_instance(session, title)
