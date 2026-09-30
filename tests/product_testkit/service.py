"""Lifecycle runner for a declared product service entrypoint."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import shlex
import subprocess
import time
from typing import Any, Mapping

from tests.testkit.cleanup import terminate_process_group
from tests.testkit.evidence import sha256_text, write_json_atomic

from .health import HealthResult, check_http_health


@dataclass
class ServiceHandle:
    process: subprocess.Popen[object]
    command: str
    health: HealthResult | None = None
    entrypoint_id: str = ""
    owner: str = ""
    health_check: str = ""
    cwd: Path | None = None
    stdout_ref: Path | None = None
    stderr_ref: Path | None = None
    health_stdout_ref: Path | None = None
    health_stderr_ref: Path | None = None
    receipt_ref: Path | None = None
    health_attempts: int = 0
    started_at: str = ""
    ready_at: str = ""
    _receipt: ServiceReceipt | None = None

    def stop(self) -> ServiceReceipt | None:
        if self._receipt is not None:
            return self._receipt
        exited_before_stop = self.process.poll() is not None
        terminate_process_group(self.process, timeout=0.2)
        if not self.entrypoint_id:
            return None
        receipt = ServiceReceipt(
            entrypoint_id=self.entrypoint_id,
            owner=self.owner,
            start_command=self.command,
            start_command_digest=_command_digest(self.command),
            health_check=self.health_check,
            health_check_digest=_command_digest(self.health_check),
            cwd=str((self.cwd or Path.cwd()).resolve()),
            healthy=bool(self.health and self.health.ok),
            health_attempts=self.health_attempts,
            started_at=self.started_at,
            ready_at=self.ready_at,
            stopped=self.process.poll() is not None,
            exit_code=self.process.returncode,
            stdout_ref=str(self.stdout_ref or ""),
            stderr_ref=str(self.stderr_ref or ""),
            health_stdout_ref=str(self.health_stdout_ref or ""),
            health_stderr_ref=str(self.health_stderr_ref or ""),
            exited_before_stop=exited_before_stop,
            error="service exited before TestKit cleanup" if exited_before_stop else "",
        )
        self._receipt = receipt
        if self.receipt_ref is not None:
            _write_receipt(receipt, self.receipt_ref)
        return receipt


@dataclass(frozen=True)
class ServiceReceipt:
    entrypoint_id: str
    owner: str
    start_command: str
    start_command_digest: str
    health_check: str
    health_check_digest: str
    cwd: str
    healthy: bool
    health_attempts: int
    started_at: str
    ready_at: str
    stopped: bool
    exit_code: int | None
    stdout_ref: str
    stderr_ref: str
    health_stdout_ref: str
    health_stderr_ref: str
    exited_before_stop: bool = False
    timed_out: bool = False
    environment_error: bool = False
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.healthy and self.stopped and not self.timed_out and not self.environment_error and not self.error


class ServiceError(RuntimeError):
    """A declared product service could not be started or made healthy."""

    def __init__(self, message: str, *, receipt: ServiceReceipt | None = None) -> None:
        super().__init__(message)
        self.receipt = receipt


def start_entrypoint(
    entrypoint: Mapping[str, Any],
    *,
    cwd: Path,
    output_dir: Path,
    env: Mapping[str, str] | None = None,
    probe_interval_seconds: float = 0.05,
) -> ServiceHandle:
    entrypoint_id = str(entrypoint["entrypoint_id"])
    command = str(entrypoint["start_command"])
    health_check = str(entrypoint["health_check"])
    owner = str(entrypoint["owner"])
    timeout_seconds = float(entrypoint["timeout_seconds"])
    start_argv = shlex.split(command)
    health_argv = shlex.split(health_check)
    if not start_argv or not health_argv:
        raise ServiceError("service and health commands must not be empty")

    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"entrypoint.{entrypoint_id}"
    stdout_path = output_dir / f"{prefix}.stdout.log"
    stderr_path = output_dir / f"{prefix}.stderr.log"
    health_stdout_path = output_dir / f"{prefix}.health.stdout.log"
    health_stderr_path = output_dir / f"{prefix}.health.stderr.log"
    receipt_path = output_dir / f"{prefix}.receipt.json"
    for path in (stdout_path, stderr_path, health_stdout_path, health_stderr_path, receipt_path):
        if path.exists() or path.is_symlink():
            raise ServiceError(f"service evidence already exists: {path}")

    started_at = _now()
    process: subprocess.Popen[object] | None = None
    process_env = {**os.environ, **dict(env or {})}
    with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_file:
        try:
            process = subprocess.Popen(
                start_argv,
                cwd=cwd,
                env=process_env,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=(os.name == "posix"),
                text=True,
            )
        except OSError as exc:
            stderr_file.write(f"{type(exc).__name__}: {exc}\n")

    if process is None:
        receipt = _failed_receipt(
            entrypoint_id=entrypoint_id,
            owner=owner,
            command=command,
            health_check=health_check,
            cwd=cwd,
            paths=(stdout_path, stderr_path, health_stdout_path, health_stderr_path),
            started_at=started_at,
            environment_error=True,
            error="service start failed",
        )
        _write_receipt(receipt, receipt_path)
        raise ServiceError("service start failed", receipt=receipt)

    handle = ServiceHandle(
        process=process,
        command=command,
        entrypoint_id=entrypoint_id,
        owner=owner,
        health_check=health_check,
        cwd=cwd,
        stdout_ref=stdout_path,
        stderr_ref=stderr_path,
        health_stdout_ref=health_stdout_path,
        health_stderr_ref=health_stderr_path,
        receipt_ref=receipt_path,
        started_at=started_at,
    )
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    with health_stdout_path.open("w", encoding="utf-8") as health_stdout, health_stderr_path.open(
        "w", encoding="utf-8"
    ) as health_stderr:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                last_error = f"service exited before health check: {process.returncode}"
                break
            handle.health_attempts += 1
            remaining = max(0.01, deadline - time.monotonic())
            try:
                probe = subprocess.run(
                    health_argv,
                    cwd=cwd,
                    env=process_env,
                    capture_output=True,
                    text=True,
                    timeout=min(1.0, remaining),
                    check=False,
                )
            except OSError as exc:
                last_error = f"health check failed to execute: {type(exc).__name__}: {exc}"
                health_stderr.write(last_error + "\n")
                handle.stop()
                receipt = _replace_receipt(
                    handle._receipt,
                    environment_error=True,
                    error=last_error,
                )
                handle._receipt = receipt
                _write_receipt(receipt, receipt_path)
                raise ServiceError(last_error, receipt=receipt) from exc
            except subprocess.TimeoutExpired as exc:
                health_stdout.write(str(exc.stdout or ""))
                health_stderr.write(str(exc.stderr or ""))
                last_error = "health check attempt timed out"
            else:
                health_stdout.write(probe.stdout)
                health_stderr.write(probe.stderr)
                health_stdout.flush()
                health_stderr.flush()
                if probe.returncode == 0:
                    handle.health = HealthResult(health_check, True, 0)
                    handle.ready_at = _now()
                    return handle
                last_error = f"health check exited with {probe.returncode}"
            time.sleep(probe_interval_seconds)

    handle.stop()
    timed_out = process.returncode is None or last_error.startswith("health check")
    message = "service health check timed out" if timed_out else last_error
    receipt = _replace_receipt(
        handle._receipt,
        timed_out=timed_out,
        error=message,
    )
    handle._receipt = receipt
    _write_receipt(receipt, receipt_path)
    raise ServiceError(message, receipt=receipt)


def _command_digest(command: str) -> str:
    return sha256_text("\0".join(shlex.split(command)))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_receipt(receipt: ServiceReceipt, path: Path) -> None:
    payload = asdict(receipt)
    payload["passed"] = receipt.passed
    write_json_atomic(path, payload)


def _failed_receipt(
    *,
    entrypoint_id: str,
    owner: str,
    command: str,
    health_check: str,
    cwd: Path,
    paths: tuple[Path, Path, Path, Path],
    started_at: str,
    timed_out: bool = False,
    environment_error: bool = False,
    error: str = "",
) -> ServiceReceipt:
    stdout_path, stderr_path, health_stdout_path, health_stderr_path = paths
    health_stdout_path.touch(exist_ok=True)
    health_stderr_path.touch(exist_ok=True)
    return ServiceReceipt(
        entrypoint_id=entrypoint_id,
        owner=owner,
        start_command=command,
        start_command_digest=_command_digest(command),
        health_check=health_check,
        health_check_digest=_command_digest(health_check),
        cwd=str(cwd.resolve()),
        healthy=False,
        health_attempts=0,
        started_at=started_at,
        ready_at="",
        stopped=True,
        exit_code=None,
        stdout_ref=str(stdout_path),
        stderr_ref=str(stderr_path),
        health_stdout_ref=str(health_stdout_path),
        health_stderr_ref=str(health_stderr_path),
        timed_out=timed_out,
        environment_error=environment_error,
        error=error,
    )


def _replace_receipt(receipt: ServiceReceipt | None, **changes: Any) -> ServiceReceipt:
    if receipt is None:
        raise RuntimeError("service receipt is unavailable")
    values = asdict(receipt)
    values.update(changes)
    return ServiceReceipt(**values)


def start_service(*, command: str, cwd: Path, health_url: str, timeout_seconds: float = 15.0) -> ServiceHandle:
    argv = shlex.split(command)
    if not argv:
        raise ServiceError("service command must not be empty")
    try:
        process = subprocess.Popen(
            argv, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        raise ServiceError(f"service start failed: {exc}") from exc
    handle = ServiceHandle(process, command)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            handle.stop()
            raise ServiceError(f"service exited before health check: {process.returncode}")
        result = check_http_health(health_url, timeout_seconds=1.0)
        if result.ok:
            handle.health = result
            return handle
        time.sleep(0.05)
    handle.stop()
    raise ServiceError("service health check timed out")

