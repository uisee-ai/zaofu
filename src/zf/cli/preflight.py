"""zf preflight — static dispatch-readiness checks before a real launch.

Catches the bug classes that silently brick an entire long-horizon run
(dispatch-prompt signature drift, broken dispatch-chain imports, unknown role
backends) in milliseconds instead of ~45min-per-round live discovery. doc 78 W4.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
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
    parser.add_argument(
        "--skip-provider-auth",
        action="store_true",
        help="Skip local provider login readiness checks",
    )
    parser.add_argument("--json", action="store_true", help="Wrap output in zf.cli.result.v1")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    path = Path(args.path) if getattr(args, "path", None) else Path("zf.yaml")
    try:
        config = load_config(path)
    except ConfigError as exc:
        if getattr(args, "json", False):
            from zf.cli.output import print_result

            print_result(
                command="preflight",
                data=None,
                ok=False,
                error_code="config_invalid",
                error=str(exc),
                next_actions=("Fix zf.yaml and rerun `zf preflight`.",),
            )
            return 1
        print(f"✗ config: {exc}")
        return 1

    results = run_preflight_checks(
        config,
        check_provider_auth=not bool(getattr(args, "skip_provider_auth", False)),
    )
    ok = preflight_ok(results)
    if getattr(args, "json", False):
        from zf.cli.output import print_result
        from zf.core.config.project_context import resolve_project_context

        context = resolve_project_context(cwd=path.parent)
        print_result(
            command="preflight",
            data={"checks": [asdict(result) for result in results]},
            context=context,
            ok=ok,
            error_code="preflight_failed" if not ok else "",
            error="one or more readiness checks failed" if not ok else "",
            next_actions=(
                ("Resolve failed checks before `zf start`.",) if not ok else ()
            ),
        )
        return 0 if ok else 1
    for result in results:
        mark = "✓" if result.ok else "✗"
        print(f"{mark} {result.name}: {result.detail}")
    print("\npreflight: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zf preflight")
    parser.add_argument("--path", type=str, default=None)
    parser.add_argument("--skip-provider-auth", action="store_true")
    parser.add_argument("--json", action="store_true")
    return run(parser.parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
