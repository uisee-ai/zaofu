"""Herdr 0.8 public CLI/NDJSON adapter for Web terminal sessions."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Protocol, Sequence

from zf.web.terminal_backend import (
    HerdrProjectRuntime,
    HerdrTerminalResource,
    TerminalBridgeSpec,
    TerminalCapability,
    TerminalRuntimeError,
)
from zf.web.terminal_environment import terminal_subprocess_env


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]: ...

    def spawn(self, argv: Sequence[str]) -> subprocess.Popen[bytes]: ...


class SubprocessCommandRunner:
    def run(self, argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            env=terminal_subprocess_env(),
        )

    def spawn(self, argv: Sequence[str]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=terminal_subprocess_env(),
        )


def _version_tuple(raw: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?:^|\s)v?(\d+)\.(\d+)\.(\d+)(?:\s|$)", raw.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


class HerdrTerminalBackend:
    def __init__(
        self,
        binary: str = "herdr",
        *,
        minimum_version: str = "0.8.0",
        runner: CommandRunner | None = None,
    ) -> None:
        self.binary = binary
        self.minimum_version = minimum_version
        self.runner = runner or SubprocessCommandRunner()

    def _global(self, *args: str) -> list[str]:
        return [self.binary, *args]

    def _session(self, session_name: str, *args: str) -> list[str]:
        return [self.binary, "--session", session_name, *args]

    def _run(self, argv: Sequence[str], *, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner.run(argv, timeout=timeout)
        except FileNotFoundError as exc:
            raise TerminalRuntimeError(
                "herdr_unavailable",
                f"Herdr binary is unavailable: {Path(self.binary).name}",
                status_code=503,
            ) from exc
        except OSError as exc:
            raise TerminalRuntimeError(
                "herdr_unavailable",
                f"Herdr command could not start: {Path(self.binary).name}",
                status_code=503,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TerminalRuntimeError(
                "herdr_timeout",
                f"Herdr command timed out after {timeout:g}s",
                status_code=503,
            ) from exc

    def _run_json(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 15.0,
        allowed_error_codes: frozenset[str] = frozenset(),
    ) -> dict[str, object]:
        completed = self._run(argv, timeout=timeout)
        raw = (
            completed.stdout
            if completed.returncode == 0
            else completed.stderr or completed.stdout
        ).strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            if completed.returncode != 0:
                raise TerminalRuntimeError(
                    "herdr_command_failed",
                    raw or "Herdr command failed",
                    status_code=503,
                ) from exc
            raise TerminalRuntimeError(
                "herdr_protocol_error",
                "Herdr command did not return JSON",
                status_code=502,
            ) from exc
        if not isinstance(value, dict):
            raise TerminalRuntimeError(
                "herdr_protocol_error", "Herdr response must be an object", status_code=502
            )
        error = value.get("error")
        error_code = str(error.get("code") or "") if isinstance(error, dict) else ""
        if error_code in allowed_error_codes:
            return value
        if completed.returncode != 0:
            raise TerminalRuntimeError(
                "herdr_command_failed",
                raw or "Herdr command failed",
                status_code=503,
            )
        if error:
            raise TerminalRuntimeError(
                "herdr_command_failed", json.dumps(error, ensure_ascii=False), status_code=503
            )
        return value

    @staticmethod
    def _nested_text(value: dict[str, object], *path: str) -> str:
        current: object = value
        for key in path:
            if not isinstance(current, dict):
                return ""
            current = current.get(key)
        return str(current or "")

    def probe(self) -> TerminalCapability:
        resolved = self.binary
        if not os.path.isabs(resolved):
            found = shutil.which(resolved)
            if found is None:
                return TerminalCapability(
                    available=False,
                    binary=self.binary,
                    reason=f"Herdr binary is unavailable: {Path(self.binary).name}",
                )
            resolved = found
        elif not Path(resolved).is_file() or not os.access(resolved, os.X_OK):
            return TerminalCapability(
                available=False,
                binary=resolved,
                reason="configured Herdr path is not an executable file",
            )
        try:
            version_result = self._run(self._global("--version"), timeout=5.0)
            version_text = (version_result.stdout or version_result.stderr).strip()
            actual = _version_tuple(version_text)
            required = _version_tuple(self.minimum_version)
            if version_result.returncode != 0 or actual is None or required is None:
                return TerminalCapability(
                    available=False,
                    binary=resolved,
                    version=version_text,
                    reason="unable to parse Herdr version",
                )
            if actual < required:
                return TerminalCapability(
                    available=False,
                    binary=resolved,
                    version=".".join(map(str, actual)),
                    reason=f"Herdr >= {self.minimum_version} is required",
                )
            schema = self._run(self._global("api", "schema", "--json"), timeout=5.0)
            observe = self._run(
                self._global("terminal", "session", "observe", "--help"), timeout=5.0
            )
            control = self._run(
                self._global("terminal", "session", "control", "--help"), timeout=5.0
            )
            tab_rename = self._run(
                self._global("tab", "rename", "--help"), timeout=5.0
            )
        except TerminalRuntimeError as exc:
            return TerminalCapability(
                available=False,
                binary=resolved,
                reason=str(exc),
            )
        try:
            schema_value = json.loads(schema.stdout)
        except json.JSONDecodeError:
            schema_value = None
        schema_available = schema.returncode == 0 and isinstance(schema_value, dict)
        observe_bridge = observe.returncode == 0
        control_bridge = control.returncode == 0
        tab_rename_available = tab_rename.returncode == 0
        available = schema_available and observe_bridge and control_bridge
        return TerminalCapability(
            available=available,
            binary=resolved,
            version=".".join(map(str, actual)),
            schema_available=schema_available,
            observe_bridge=observe_bridge,
            control_bridge=control_bridge,
            tab_rename=tab_rename_available,
            reason="" if available else "Herdr terminal bridge capability probe failed",
        )

    def ensure_project_runtime(self, session_name: str) -> HerdrProjectRuntime:
        status = self._run(self._session(session_name, "workspace", "list"), timeout=3.0)
        if status.returncode == 0:
            return HerdrProjectRuntime(session_name=session_name)
        try:
            process = self.runner.spawn(self._session(session_name, "server"))
        except OSError as exc:
            raise TerminalRuntimeError(
                "herdr_unavailable",
                f"Herdr binary is unavailable: {Path(self.binary).name}",
                status_code=503,
            ) from exc
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                status = self._run(
                    self._session(session_name, "workspace", "list"),
                    timeout=min(2.0, max(deadline - time.monotonic(), 0.01)),
                )
            except TerminalRuntimeError as exc:
                if exc.code != "herdr_timeout":
                    raise
                # A cold sandbox may accept the socket before the server answers.
                # Retry only within the existing startup deadline, without spawning again.
                status = None
            if status is not None and status.returncode == 0:
                return HerdrProjectRuntime(session_name=session_name, server_pid=process.pid)
            if process.poll() is not None:
                break
            time.sleep(0.05)
        raise TerminalRuntimeError(
            "herdr_server_start_failed",
            f"Herdr named session {session_name!r} did not become ready",
            status_code=503,
        )

    def create_terminal(
        self,
        *,
        runtime: HerdrProjectRuntime,
        workspace_id: str,
        project_root: Path,
        label: str,
        agent_name: str,
        provider_kind: str,
        provider_args: tuple[str, ...],
        start_timeout_seconds: int,
    ) -> HerdrTerminalResource:
        if workspace_id:
            response = self._run_json(
                self._session(
                    runtime.session_name,
                    "tab",
                    "create",
                    "--workspace",
                    workspace_id,
                    "--cwd",
                    str(project_root),
                    "--label",
                    label,
                    "--no-focus",
                )
            )
            tab_id = self._nested_text(response, "result", "tab", "tab_id")
            pane_id = self._nested_text(response, "result", "root_pane", "pane_id")
        else:
            response = self._run_json(
                self._session(
                    runtime.session_name,
                    "workspace",
                    "create",
                    "--cwd",
                    str(project_root),
                    "--label",
                    "ZaoFu Web Terminal",
                    "--no-focus",
                )
            )
            workspace_id = self._nested_text(
                response, "result", "workspace", "workspace_id"
            )
            tab_id = self._nested_text(response, "result", "tab", "tab_id")
            pane_id = self._nested_text(response, "result", "root_pane", "pane_id")
        if not workspace_id or not tab_id or not pane_id:
            raise TerminalRuntimeError(
                "herdr_protocol_error",
                "Herdr create response omitted workspace/tab/pane identity",
                status_code=502,
            )
        try:
            start_argv = self._session(
                runtime.session_name,
                "agent",
                "start",
                agent_name,
                "--kind",
                provider_kind,
                "--pane",
                pane_id,
                "--timeout",
                str(start_timeout_seconds * 1000),
            )
            if provider_args:
                start_argv = (*start_argv, "--", *provider_args)
            agent = self._run_json(
                start_argv,
                timeout=start_timeout_seconds + 5,
                allowed_error_codes=frozenset({"agent_not_ready"}),
            )
            error = agent.get("error")
            if isinstance(error, dict) and error.get("code") == "agent_not_ready":
                # Herdr deliberately keeps a startup-blocked TUI alive and
                # named so a human controller can complete trust/onboarding.
                # Resolve the exact resource instead of treating that useful
                # interactive state as a provider crash.
                agent = self._run_json(
                    self._session(
                        runtime.session_name,
                        "agent",
                        "get",
                        agent_name,
                    )
                )
            terminal_id = self._nested_text(agent, "result", "agent", "terminal_id")
            if not terminal_id:
                raise TerminalRuntimeError(
                    "herdr_protocol_error",
                    "Herdr agent start response omitted terminal identity",
                    status_code=502,
                )
            resolved_pane_id = self._nested_text(agent, "result", "agent", "pane_id")
            resolved_name = self._nested_text(agent, "result", "agent", "name")
            if (resolved_pane_id and resolved_pane_id != pane_id) or (
                resolved_name and resolved_name != agent_name
            ):
                raise TerminalRuntimeError(
                    "herdr_protocol_error",
                    "Herdr blocked agent identity did not match the created terminal",
                    status_code=502,
                )
        except TerminalRuntimeError:
            # Topology was created before the provider readiness gate.  Close
            # only that exact tab so a failed binary/credential cannot leave an
            # unregistered shell resource behind.
            try:
                self._run(
                    self._session(runtime.session_name, "tab", "close", tab_id),
                    timeout=5.0,
                )
            except TerminalRuntimeError:
                pass
            raise
        return HerdrTerminalResource(
            workspace_id=workspace_id,
            tab_id=tab_id,
            pane_id=pane_id,
            terminal_id=terminal_id,
            agent_name=agent_name,
        )

    def terminal_exists(self, *, runtime: HerdrProjectRuntime, tab_id: str) -> bool:
        response = self._run(self._session(runtime.session_name, "tab", "get", tab_id))
        return response.returncode == 0

    def stop_terminal(self, *, runtime: HerdrProjectRuntime, tab_id: str) -> None:
        completed = self._run(self._session(runtime.session_name, "tab", "close", tab_id))
        if completed.returncode != 0:
            reason = (completed.stderr or completed.stdout or "Herdr tab close failed").strip()
            raise TerminalRuntimeError("herdr_stop_failed", reason, status_code=503)

    def rename_terminal(
        self,
        *,
        runtime: HerdrProjectRuntime,
        tab_id: str,
        title: str,
    ) -> None:
        completed = self._run(
            self._session(runtime.session_name, "tab", "rename", tab_id, title)
        )
        if completed.returncode != 0:
            reason = (completed.stderr or completed.stdout or "Herdr tab rename failed").strip()
            raise TerminalRuntimeError("herdr_rename_failed", reason, status_code=503)

    def bridge_spec(
        self,
        *,
        runtime: HerdrProjectRuntime,
        target: str,
        mode: str,
        takeover: bool,
        cols: int,
        rows: int,
    ) -> TerminalBridgeSpec:
        if mode not in {"observe", "control"}:
            raise TerminalRuntimeError("invalid_attachment_mode", f"invalid mode: {mode}")
        argv = self._session(
            runtime.session_name,
            "terminal",
            "session",
            mode,
            target,
        )
        if mode == "control" and takeover:
            argv.append("--takeover")
        argv.extend(("--cols", str(cols), "--rows", str(rows)))
        return TerminalBridgeSpec(argv=tuple(argv), mode=mode, cols=cols, rows=rows)
