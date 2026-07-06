"""Compatibility wrapper for historical Hermes report imports."""

from __future__ import annotations

from pathlib import Path

from zf.runtime.run_closeout_report import (
    build_run_closeout_report,
    write_run_closeout_report,
)


def build_hermes_run_report(
    *,
    state_dir: Path,
    title: str = "Hermes Refactor Run Closeout",
) -> str:
    return build_run_closeout_report(state_dir=state_dir, title=title)


def write_hermes_run_report(
    *,
    state_dir: Path,
    out: Path,
    title: str = "Hermes Refactor Run Closeout",
) -> Path:
    return write_run_closeout_report(state_dir=state_dir, out=out, title=title)
