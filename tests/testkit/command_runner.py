"""Shell-free command execution and receipts for ZaoFu tests."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Mapping, Sequence
import os
import re
import shlex
import subprocess
import time

from .cleanup import terminate_process_group
from .evidence import sha256_text, write_json_atomic


@dataclass(frozen=True)
class CommandReceipt:
    command_id: str
    command: str
    command_digest: str
    cwd: str
    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    stdout_ref: str
    stderr_ref: str
    environment_error: bool = False
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def _argv(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        argv = shlex.split(command)
    else:
        argv = [str(item) for item in command]
    if not argv:
        raise ValueError("command must not be empty")
    return argv


def _validate_command_id(command_id: str) -> None:
    """Require a stable flat identifier for immutable command evidence."""
    if not command_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", command_id):
        raise ValueError(
            "command_id must be a non-empty filename-safe identifier "
            "starting with an alphanumeric character"
        )


def run_command(
    command: str | Sequence[str],
    *,
    command_id: str,
    cwd: Path,
    output_dir: Path,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = 900,
) -> CommandReceipt:
    _validate_command_id(command_id)
    argv = _argv(command)
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / f"{command_id}.stdout.log"
    stderr_path = output_dir / f"{command_id}.stderr.log"
    receipt_path = output_dir / f"{command_id}.receipt.json"
    claim_path = output_dir / f".{command_id}.claim"
    try:
        claim_fd = os.open(
            claim_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except FileExistsError as exc:
        raise FileExistsError(
            f"command evidence is already claimed: {command_id} in {output_dir}"
        ) from exc
    try:
        os.write(claim_fd, f"command_id={command_id}\n".encode("ascii"))
    finally:
        os.close(claim_fd)
    for evidence_path in (stdout_path, stderr_path, receipt_path):
        if evidence_path.exists() or evidence_path.is_symlink():
            raise FileExistsError(f"command evidence already exists: {evidence_path}")
    started = time.monotonic()
    timed_out = False
    exit_code: int | None = None
    environment_error = False
    error = ""
    # Redirect directly to files instead of PIPEs. A descendant can inherit a
    # pipe descriptor after the leader exits and make an otherwise-complete
    # timeout cleanup block forever in communicate().
    with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_file:
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env={**os.environ, **dict(env or {})},
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=(os.name == "posix"),
                text=True,
            )
        except OSError as exc:
            # A missing executable, invalid cwd, or inaccessible process
            # boundary is a harness/environment failure. Preserve it in the
            # same receipt contract as ordinary command outcomes so callers
            # can classify it without catching an unstructured exception.
            environment_error = True
            error = f"{type(exc).__name__}: {exc}"
            stderr_file.write(error + "\n")
        else:
            try:
                process.wait(timeout=timeout_seconds)
                exit_code = process.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                terminate_process_group(process, timeout=1.0)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=1.0)
    receipt = CommandReceipt(
        command_id=command_id,
        command=" ".join(argv),
        command_digest=sha256_text("\0".join(argv)),
        cwd=str(cwd.resolve()),
        exit_code=exit_code,
        timed_out=timed_out,
        duration_seconds=round(time.monotonic() - started, 6),
        stdout_ref=str(stdout_path),
        stderr_ref=str(stderr_path),
        environment_error=environment_error,
        error=error,
    )
    write_json_atomic(receipt_path, asdict(receipt))
    return receipt
