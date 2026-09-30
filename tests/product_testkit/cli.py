"""Product-agnostic CLI contract probes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class CliResult:
    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


def run_cli(argv: list[str] | tuple[str, ...], *, cwd: Path, timeout_seconds: float = 30.0) -> CliResult:
    if not argv:
        raise ValueError("CLI argv must not be empty")
    try:
        completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        return CliResult(tuple(str(x) for x in argv), None, exc.stdout or "", exc.stderr or "", True)
    return CliResult(tuple(str(x) for x in argv), completed.returncode, completed.stdout, completed.stderr)


def assert_cli_result(result: CliResult, *, expected_exit: int, required_output: str | None = None) -> None:
    assert not result.timed_out, f"CLI timed out: {result.argv}"
    assert result.exit_code == expected_exit, (result.argv, result.exit_code, result.stderr)
    if required_output is not None:
        assert required_output in result.stdout, (result.argv, result.stdout)

