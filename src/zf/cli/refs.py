"""zf refs — git ref diagnostics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError, load_config
from zf.runtime.ref_verify import RefVerifier


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("refs", help="Verify ZaoFu git refs")
    sub = parser.add_subparsers(dest="refs_cmd")

    verify = sub.add_parser("verify", help="Verify task and candidate refs")
    verify.set_defaults(func=_run_verify)

    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    print("Usage: zf refs verify", file=sys.stderr)
    return 2


def _run_verify(args: argparse.Namespace) -> int:
    try:
        project_root, state_dir, config = _load_runtime()
        result = RefVerifier(
            state_dir=state_dir,
            project_root=project_root,
            config=config,
        ).verify()
    except (ConfigError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if result.ok:
        print("OK: refs")
        return 0
    print("Ref issues:")
    for issue in result.issues:
        print(f"  - {issue}")
    return 1


def _load_runtime():
    project_root = Path.cwd()
    config = load_config(project_root / "zf.yaml")
    raw_state = Path(config.project.state_dir)
    state_dir = raw_state if raw_state.is_absolute() else project_root / raw_state
    return project_root, state_dir, config
