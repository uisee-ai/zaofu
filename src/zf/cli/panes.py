"""zf panes — operator commands for pane-grid bindings."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError, load_config
from zf.runtime.pane_bindings import PaneBindingManager


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("panes", help="Manage pane-grid bindings")
    sub = parser.add_subparsers(dest="panes_cmd")

    doctor = sub.add_parser("doctor", help="Check pane-grid role bindings")
    doctor.set_defaults(func=_run_doctor)

    repair = sub.add_parser("repair", help="Repair pane-grid role bindings")
    repair.set_defaults(func=_run_repair)

    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    print("Usage: zf panes doctor | zf panes repair", file=sys.stderr)
    return 2


def _run_doctor(args: argparse.Namespace) -> int:
    try:
        manager = _load_manager()
        issues = manager.doctor()
    except (ConfigError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not issues:
        print("OK: panes")
        return 0
    print("Pane issues:")
    for issue in issues:
        print(f"  - {issue}")
    return 1


def _run_repair(args: argparse.Namespace) -> int:
    try:
        manager = _load_manager()
        actions = manager.repair()
    except (ConfigError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("Pane repair:")
    for action in actions:
        print(f"  - {action}")
    return 0


def _load_manager() -> PaneBindingManager:
    project_root = Path.cwd()
    config = load_config(project_root / "zf.yaml")
    raw_state = Path(config.project.state_dir)
    state_dir = raw_state if raw_state.is_absolute() else project_root / raw_state
    return PaneBindingManager(
        project_root=project_root,
        state_dir=state_dir,
        config=config,
    )
