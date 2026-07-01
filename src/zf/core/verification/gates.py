"""Verification gates — run checks and capture results."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GateResult:
    name: str
    passed: bool
    exit_code: int
    output: str


class CommandGate:
    def __init__(self, name: str, command: str, timeout: int = 30) -> None:
        self.name = name
        self.command = command
        self.timeout = timeout

    def run(self) -> GateResult:
        try:
            result = subprocess.run(
                self.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return GateResult(
                name=self.name,
                passed=result.returncode == 0,
                exit_code=result.returncode,
                output=(result.stdout + result.stderr).strip(),
            )
        except subprocess.TimeoutExpired:
            return GateResult(
                name=self.name,
                passed=False,
                exit_code=-1,
                output=f"Timeout after {self.timeout}s",
            )


class FileExistsGate:
    def __init__(self, name: str, paths: list[str]) -> None:
        self.name = name
        self.paths = paths

    def run(self) -> GateResult:
        missing = [p for p in self.paths if not Path(p).exists()]
        if missing:
            return GateResult(
                name=self.name,
                passed=False,
                exit_code=1,
                output=f"Missing files: {', '.join(missing)}",
            )
        return GateResult(
            name=self.name,
            passed=True,
            exit_code=0,
            output=f"All {len(self.paths)} files exist",
        )
