"""Small Docker lifecycle adapter for product dependency fixtures.

The adapter intentionally uses the Docker CLI rather than importing a second
container framework.  Product projects can replace the command implementation
with Testcontainers while retaining the same receipt and verdict contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Mapping, Sequence

from tests.testkit.evidence import sha256_text, write_json_atomic


_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]*\Z")
_PORT = re.compile(r"[1-9][0-9]{0,4}:[1-9][0-9]{0,4}\Z")


def docker_container_command(
    *, name: str, image: str, command: str, ports: Sequence[str] = ()
) -> list[str]:
    if not _NAME.fullmatch(name):
        raise ValueError("container name must be a stable Docker identifier")
    if not image or any(char.isspace() for char in image):
        raise ValueError("image must be one Docker image token")
    if not command.strip() or any(char in command for char in "\x00\n\r"):
        raise ValueError("container command must be one non-empty line")
    validated_ports: list[str] = []
    for port in ports:
        if not isinstance(port, str) or not _PORT.fullmatch(port):
            raise ValueError("ports must be host:container mappings")
        validated_ports.extend(("-p", port))
    return [
        "docker", "run", "--detach", "--rm", "--name", name,
        *validated_ports, image, "sh", "-c", command,
    ]


def classify_container_probe(*, exit_code: int | None, timed_out: bool, stderr: str) -> dict[str, str]:
    if timed_out:
        return {"verdict": "environment_failure", "reason": "health_timeout"}
    if exit_code == 0:
        return {"verdict": "pass", "reason": "health_passed"}
    lowered = stderr.lower()
    if any(marker in lowered for marker in ("connection", "timeout", "not found", "docker daemon", "no such container")):
        return {"verdict": "environment_failure", "reason": "dependency_unavailable"}
    return {"verdict": "fail", "reason": "health_failed"}


def start_container(*, name: str, image: str, command: str, ports: Sequence[str] = ()) -> str:
    """Start a detached ephemeral container and return its Docker ID."""

    completed = subprocess.run(
        docker_container_command(name=name, image=image, command=command, ports=ports),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "docker run failed")
    container_id = completed.stdout.strip()
    if not container_id:
        raise RuntimeError("docker run returned no container id")
    return container_id


def exec_container(*, name: str, command: str, timeout_seconds: float = 10.0) -> subprocess.CompletedProcess[str]:
    if not _NAME.fullmatch(name):
        raise ValueError("container name must be a stable Docker identifier")
    if not command.strip() or any(char in command for char in "\x00\n\r"):
        raise ValueError("health command must be one non-empty line")
    return subprocess.run(
        ["docker", "exec", name, "sh", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def stop_container(*, name: str, timeout_seconds: float = 30.0) -> subprocess.CompletedProcess[str]:
    if not _NAME.fullmatch(name):
        raise ValueError("container name must be a stable Docker identifier")
    return subprocess.run(
        ["docker", "rm", "--force", name],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


@dataclass(frozen=True)
class ContainerReceipt:
    """Auditable final state for one ephemeral product dependency."""

    container_id: str
    name: str
    image: str
    start_command: str
    start_command_digest: str
    health_command: str
    health_command_digest: str
    ports: tuple[str, ...]
    healthy: bool
    health_attempts: int
    started: bool
    stopped: bool
    exit_code: int | None
    timed_out: bool
    environment_error: bool
    stdout_ref: str
    stderr_ref: str
    health_stdout_ref: str
    health_stderr_ref: str
    receipt_ref: str
    started_at: str
    ready_at: str
    error: str = ""

    @property
    def passed(self) -> bool:
        return (
            self.started and self.healthy and self.stopped
            and not self.timed_out and not self.environment_error and not self.error
        )


class ContainerError(RuntimeError):
    """A declared dependency could not start or pass its health contract."""

    def __init__(
        self,
        message: str,
        *,
        receipt: ContainerReceipt | None = None,
        environment_error: bool = False,
    ) -> None:
        super().__init__(message)
        self.receipt = receipt
        self.environment_error = environment_error


@dataclass
class ContainerHandle:
    """Live container handle; stopping it always emits a receipt."""

    container_id: str
    logical_id: str
    name: str
    image: str
    start_command: str
    health_command: str
    ports: tuple[str, ...]
    output_dir: Path
    health_attempts: int = 0
    healthy: bool = False
    ready_at: str = ""
    started_at: str = ""
    _receipt: ContainerReceipt | None = None

    @property
    def env(self) -> dict[str, str]:
        suffix = _env_suffix(self.logical_id)
        return {
            f"ZF_CONTAINER_{suffix}_NAME": self.name,
            f"ZF_CONTAINER_{suffix}_ID": self.container_id,
        }

    def stop(self, *, error: str = "", timed_out: bool = False, environment_error: bool = False) -> ContainerReceipt:
        if self._receipt is not None:
            return self._receipt
        stdout_path = self.output_dir / f"container.{self.name}.stdout.log"
        stderr_path = self.output_dir / f"container.{self.name}.stderr.log"
        health_stdout_path = self.output_dir / f"container.{self.name}.health.stdout.log"
        health_stderr_path = self.output_dir / f"container.{self.name}.health.stderr.log"
        receipt_path = self.output_dir / f"container.{self.name}.receipt.json"
        logs = subprocess.run(
            ["docker", "logs", self.name], capture_output=True, text=True,
            timeout=30, check=False,
        )
        stdout_path.write_text(logs.stdout, encoding="utf-8")
        stderr_path.write_text(logs.stderr, encoding="utf-8")
        removed = stop_container(name=self.name)
        not_found = "no such container" in removed.stderr.lower()
        stopped = removed.returncode == 0 or not_found
        final_error = error or (removed.stderr.strip() if not stopped else "")
        receipt = ContainerReceipt(
            container_id=self.container_id,
            name=self.name,
            image=self.image,
            start_command=self.start_command,
            start_command_digest=_command_digest(self.start_command),
            health_command=self.health_command,
            health_command_digest=_command_digest(self.health_command),
            ports=self.ports,
            healthy=self.healthy,
            health_attempts=self.health_attempts,
            started=True,
            stopped=stopped,
            exit_code=removed.returncode,
            timed_out=timed_out,
            environment_error=environment_error,
            stdout_ref=str(stdout_path),
            stderr_ref=str(stderr_path),
            health_stdout_ref=str(health_stdout_path),
            health_stderr_ref=str(health_stderr_path),
            receipt_ref=str(receipt_path),
            started_at=self.started_at,
            ready_at=self.ready_at,
            error=final_error,
        )
        write_json_atomic(receipt_path, {**receipt.__dict__, "passed": receipt.passed})
        self._receipt = receipt
        return receipt


def start_dependency_container(
    spec: Mapping[str, Any], *, output_dir: Path, name: str | None = None,
    probe_interval_seconds: float = 0.05,
) -> ContainerHandle:
    """Start and health-check one manifest dependency with bounded cleanup."""

    container_id_value = str(spec["id"])
    container_name = name or _container_name(container_id_value, output_dir)
    image = str(spec["image"])
    start_command = str(spec["command"])
    health_command = str(spec["health_command"])
    ports = tuple(str(port) for port in spec.get("ports", []))
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        output_dir / f"container.{container_name}.stdout.log",
        output_dir / f"container.{container_name}.stderr.log",
        output_dir / f"container.{container_name}.health.stdout.log",
        output_dir / f"container.{container_name}.health.stderr.log",
        output_dir / f"container.{container_name}.receipt.json",
    ]
    if any(path.exists() or path.is_symlink() for path in paths):
        raise ContainerError(f"container evidence already exists for {container_id_value}")
    started_at = _now()
    try:
        container_id = start_container(
            name=container_name, image=image, command=start_command, ports=ports,
        )
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        raise ContainerError(
            f"container {container_id_value} failed to start: {exc}",
            environment_error=True,
        ) from exc
    handle = ContainerHandle(
        container_id=container_id, logical_id=container_id_value, name=container_name, image=image,
        start_command=start_command, health_command=health_command, ports=ports,
        output_dir=output_dir, started_at=started_at,
    )
    health_stdout_path, health_stderr_path = paths[2], paths[3]
    timeout_seconds = float(spec["timeout_seconds"])
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    with health_stdout_path.open("w", encoding="utf-8") as health_out, health_stderr_path.open(
        "w", encoding="utf-8"
    ) as health_err:
        while time.monotonic() < deadline:
            handle.health_attempts += 1
            remaining = max(0.01, deadline - time.monotonic())
            try:
                probe = exec_container(
                    name=container_name, command=health_command,
                    timeout_seconds=min(1.0, remaining),
                )
            except subprocess.TimeoutExpired as exc:
                last_error = "container health check attempt timed out"
                health_out.write(str(exc.stdout or ""))
                health_err.write(str(exc.stderr or ""))
            except OSError as exc:
                last_error = f"container health check failed to execute: {type(exc).__name__}: {exc}"
                health_err.write(last_error + "\n")
                receipt = handle.stop(error=last_error, environment_error=True)
                raise ContainerError(last_error, receipt=receipt, environment_error=True) from exc
            else:
                health_out.write(probe.stdout)
                health_err.write(probe.stderr)
                health_out.flush()
                health_err.flush()
                if probe.returncode == 0:
                    handle.healthy = True
                    handle.ready_at = _now()
                    return handle
                last_error = probe.stderr.strip() or f"health command exited with {probe.returncode}"
            time.sleep(probe_interval_seconds)
    receipt = handle.stop(error=f"container health check timed out: {last_error}", timed_out=True)
    raise ContainerError(str(receipt.error), receipt=receipt)


def _container_name(container_id: str, output_dir: Path) -> str:
    digest = hashlib.sha256(str(output_dir.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"zf-product-{_env_suffix(container_id).lower()}-{digest}"


def _env_suffix(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", value).upper()


def _command_digest(command: str) -> str:
    return sha256_text("\0".join(command.split()))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

