"""Availability probes for optional product-test tools."""

from __future__ import annotations

from dataclasses import dataclass
import shutil


@dataclass(frozen=True)
class ToolProbe:
    name: str
    available: bool
    executable: str | None
    verdict: str


def probe_tool(name: str) -> ToolProbe:
    if not name.strip() or any(char.isspace() for char in name):
        raise ValueError("tool name must be a single non-empty token")
    executable = shutil.which(name)
    return ToolProbe(name, executable is not None, executable, "available" if executable else "not_run")

