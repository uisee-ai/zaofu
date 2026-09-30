"""Bounded tmux shell handshake and provider readiness diagnostics."""

from __future__ import annotations

import shlex
import time
from typing import Any

from zf.runtime.provider_interactive_prompt import (
    provider_interactive_prompt_marker,
)
from zf.runtime.tmux import TmuxError


class TmuxReadinessMixin:
    """Provider launch handshake shared by the tmux transport."""

    _AGENT_LAUNCH_GRACE_SECONDS = 3.0
    _READY_POLL_INTERVAL_SECONDS = 0.1
    _SHELL_HANDSHAKE_TIMEOUT_SECONDS = 3.0
    _SHELL_HANDSHAKE_SUBMIT_RETRIES = 1
    _CODEX_READY_TIMEOUT_CAP_SECONDS = 20.0
    _LAUNCH_SUBMIT_RETRIES = 1

    def _wait_for_shell_handshake(self, role: Any) -> None:
        """Prove a fresh pane can execute one shell command before launch."""

        if not all(callable(getattr(self.tmux, name, None)) for name in (
            "pane_alive",
            "pane_current_command",
            "capture_pane",
        )):
            return
        deadline = time.monotonic() + self._SHELL_HANDSHAKE_TIMEOUT_SECONDS
        nonce = str(time.monotonic_ns())
        marker_prefix = "__ZF_SHELL_READY_"
        marker = f"{marker_prefix}{nonce}__"
        probe = (
            "printf '%s%s%s\\n' "
            f"{shlex.quote(marker_prefix)} {shlex.quote(nonce)} '__'"
        )
        submitted = False
        retries = 0
        while time.monotonic() < deadline:
            try:
                pane_alive = bool(self.tmux.pane_alive(role.instance_id))
                command = self.pane_current_command(role.instance_id).strip()
            except Exception:
                pane_alive = False
                command = ""
            leaf = command.rsplit("/", 1)[-1].strip().lower()
            if pane_alive and leaf in self._SHELL_COMMANDS:
                if not submitted:
                    self.tmux.send_keys(role.instance_id, probe)
                    submitted = True
                try:
                    screen = self.tmux.capture_pane(role.instance_id, lines=40)
                except Exception:
                    screen = ""
                if marker in screen:
                    self._readiness_failures.pop(role.instance_id, None)
                    return
                if retries < self._SHELL_HANDSHAKE_SUBMIT_RETRIES:
                    try:
                        self.tmux.send_keys(
                            role.instance_id,
                            "",
                            submit_delay_s=0.0,
                        )
                    except TypeError:
                        self.tmux.send_keys(role.instance_id, "")
                    retries += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self._READY_POLL_INTERVAL_SECONDS, remaining))
        diagnostics = self._record_readiness_failure(
            role.instance_id,
            failure_class="shell_handshake_timeout",
            extra={"backend": role.backend, "shell_submit_attempts": 1 + retries},
        )
        raise build_tmux_readiness_error(role.instance_id, diagnostics)

    def wait_ready(self, role_name: str, pattern: str, timeout: float) -> bool:
        """Wait for a live provider prompt without accepting stale scrollback."""

        requested_timeout = max(0.0, timeout)
        launch = self._launch_records.get(role_name, {})
        backend = str(launch.get("backend") or "")
        effective_timeout = (
            min(requested_timeout, self._CODEX_READY_TIMEOUT_CAP_SECONDS)
            if backend == "codex"
            else requested_timeout
        )
        deadline = time.monotonic() + effective_timeout
        launch_grace_deadline = min(
            deadline,
            time.monotonic() + self._AGENT_LAUNCH_GRACE_SECONDS,
        )
        provider_seen = False
        failure_class = "provider_ready_pattern_timeout"

        while time.monotonic() < deadline:
            if self._agent_process_alive(role_name):
                provider_seen = True
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    break
                if self.tmux.wait_for_prompt(
                    role_name,
                    pattern,
                    timeout=min(self._READY_POLL_INTERVAL_SECONDS, remaining),
                ):
                    blocker = self._interactive_prompt_blocker(role_name)
                    if blocker:
                        self._record_readiness_failure(
                            role_name,
                            failure_class={
                                "login_required": "provider_auth_interactive",
                                "trust_prompt": "provider_trust_interactive",
                                "usage_limit_reached": "provider_quota_interactive",
                            }.get(blocker, "provider_interactive_blocked"),
                            extra={
                                "backend": backend,
                                "interactive_prompt_marker": blocker,
                                "requested_timeout_seconds": requested_timeout,
                                "effective_timeout_seconds": effective_timeout,
                            },
                        )
                        return False
                    ready = self._agent_process_alive(role_name)
                    if ready:
                        self._readiness_failures.pop(role_name, None)
                    return ready
            else:
                if provider_seen:
                    failure_class = "provider_process_exited"
                    break
                if not self.tmux.pane_alive(role_name):
                    failure_class = "provider_pane_dead"
                    break
                if time.monotonic() >= launch_grace_deadline:
                    attempts = int(launch.get("launch_attempts") or 0)
                    command = self.pane_current_command(role_name).strip()
                    leaf = command.rsplit("/", 1)[-1].strip().lower()
                    if (
                        launch
                        and leaf in self._SHELL_COMMANDS
                        and attempts <= self._LAUNCH_SUBMIT_RETRIES
                    ):
                        try:
                            self.tmux.send_keys(
                                role_name,
                                "",
                                submit_delay_s=0.0,
                            )
                        except TypeError:
                            self.tmux.send_keys(role_name, "")
                        launch["launch_attempts"] = attempts + 1
                        launch_grace_deadline = min(
                            deadline,
                            time.monotonic() + self._AGENT_LAUNCH_GRACE_SECONDS,
                        )
                        continue
                    failure_class = "provider_launch_not_submitted"
                    break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self._READY_POLL_INTERVAL_SECONDS, remaining))
        self._record_readiness_failure(
            role_name,
            failure_class=failure_class,
            extra={
                "backend": backend,
                "requested_timeout_seconds": requested_timeout,
                "effective_timeout_seconds": effective_timeout,
            },
        )
        return False

    def _interactive_prompt_blocker(self, role_name: str) -> str:
        try:
            screen = self.tmux.capture_pane(role_name, lines=40)
        except Exception:
            return ""
        return provider_interactive_prompt_marker(str(screen))

    def readiness_diagnostics(self, role_name: str) -> dict[str, object]:
        existing = self._readiness_failures.get(role_name)
        if existing is not None:
            return dict(existing)
        return self._diagnostic_snapshot(role_name)

    def _record_readiness_failure(
        self,
        role_name: str,
        *,
        failure_class: str,
        extra: dict[str, object] | None = None,
    ) -> dict[str, object]:
        diagnostics = self._diagnostic_snapshot(role_name)
        diagnostics["failure_class"] = failure_class
        launch = self._launch_records.get(role_name, {})
        diagnostics["launch_attempts"] = int(launch.get("launch_attempts") or 0)
        diagnostics.update(extra or {})
        self._readiness_failures[role_name] = diagnostics
        return diagnostics

    def _diagnostic_snapshot(self, role_name: str) -> dict[str, object]:
        try:
            pane_alive = bool(self.tmux.pane_alive(role_name))
        except Exception:
            pane_alive = False
        try:
            current_command = self.pane_current_command(role_name).strip()
        except Exception:
            current_command = ""
        try:
            screen = self.tmux.capture_pane(role_name, lines=40)
        except Exception:
            screen = ""
        excerpt = "\n".join(str(screen).splitlines()[-20:])[-2000:]
        return {
            "pane_alive": pane_alive,
            "current_command": current_command,
            "process_probe": self._pane_process_probe(role_name),
            "last_screen_excerpt": excerpt,
        }


def build_tmux_readiness_error(
    role_name: str,
    diagnostics: dict[str, object],
    *,
    cause: BaseException | None = None,
) -> TmuxError:
    failure_class = str(
        diagnostics.get("failure_class") or "provider_ready_unproven"
    )
    current_command = str(diagnostics.get("current_command") or "unknown")
    error = TmuxError(
        f"{role_name} provider readiness failed: "
        f"failure_class={failure_class}, current_command={current_command}"
    )
    for key, value in diagnostics.items():
        setattr(error, key, value)
    if cause is not None:
        setattr(error, "cause_type", type(cause).__name__)
    return error


__all__ = ["TmuxReadinessMixin", "build_tmux_readiness_error"]
