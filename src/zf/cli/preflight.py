"""zf preflight — static dispatch-readiness checks before a real launch.

Catches the bug classes that silently brick an entire long-horizon run
(dispatch-prompt signature drift, broken dispatch-chain imports, unknown role
backends) in milliseconds instead of ~45min-per-round live discovery. doc 78 W4.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError, load_config
from zf.runtime.preflight import preflight_ok, run_preflight_checks


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "preflight",
        help="Static dispatch-readiness checks before a real launch",
    )
    parser.add_argument("--path", type=str, default=None, help="Path to zf.yaml")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    path = Path(args.path) if getattr(args, "path", None) else Path("zf.yaml")
    try:
        config = load_config(path)
    except ConfigError as exc:
        print(f"✗ config: {exc}")
        return 1

    results = run_preflight_checks(config)
    for result in results:
        mark = "✓" if result.ok else "✗"
        print(f"{mark} {result.name}: {result.detail}")
    ok = preflight_ok(results)
    print("\npreflight: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zf preflight")
    parser.add_argument("--path", type=str, default=None)
    return run(parser.parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
