"""zf workdir — operator workdir commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError, load_config
from zf.runtime.workdirs import WorkdirManager


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("workdir", help="Manage runtime workdirs")
    sub = parser.add_subparsers(dest="workdir_cmd")

    repair = sub.add_parser("repair", help="Repair a configured workdir")
    repair.add_argument("instance", help="role instance id, e.g. dev-1")
    repair.set_defaults(func=_run_repair)

    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    print("Usage: zf workdir repair <instance>", file=sys.stderr)
    return 2


def _run_repair(args: argparse.Namespace) -> int:
    try:
        project_root, state_dir, config = _load_runtime()
        actions = WorkdirManager(
            state_dir=state_dir,
            project_root=project_root,
            config=config,
        ).repair(args.instance)
    except (ConfigError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("Workdir repair:")
    for action in actions:
        print(f"  - {action}")
    return 0


def _load_runtime():
    project_root = Path.cwd()
    config = load_config(project_root / "zf.yaml")
    raw_state = Path(config.project.state_dir)
    state_dir = raw_state if raw_state.is_absolute() else project_root / raw_state
    return project_root, state_dir, config
