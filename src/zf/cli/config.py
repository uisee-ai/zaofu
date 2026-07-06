"""zf config — inspect and render the effective canonical config."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.project_context import resolve_project_context
from zf.core.config.render import (
    build_config_inspection_report,
    write_rendered_config,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "config",
        help="Inspect/render the effective canonical config",
    )
    sub = parser.add_subparsers(dest="config_cmd")

    inspect = sub.add_parser("inspect", help="Inspect expanded config")
    inspect.add_argument("--config", type=Path, default=None)
    inspect.add_argument("--expanded", action="store_true")
    inspect.add_argument("--format", choices=["json", "md"], default="md")
    inspect.add_argument(
        "--json",
        action="store_true",
        help="Alias for --format json",
    )
    inspect.set_defaults(func=_run_inspect)

    render = sub.add_parser("render", help="Render expanded config and lock")
    render.add_argument("--config", type=Path, default=None)
    render.add_argument("--input", dest="config", type=Path, default=None)
    render.add_argument("--output", type=Path, default=None)
    render.add_argument("--lock", type=Path, default=None)
    render.add_argument(
        "--include-secrets",
        action="store_true",
        help="Write expanded config without redacting secret-like fields",
    )
    render.set_defaults(func=_run_render)

    parser.set_defaults(func=_run)


def _run(args: argparse.Namespace) -> int:
    print(
        "Usage: zf config inspect --expanded | zf config render",
        file=sys.stderr,
    )
    return 2


def _run_inspect(args: argparse.Namespace) -> int:
    try:
        config, config_path, project_root, state_dir = _load(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    report = build_config_inspection_report(
        config,
        config_path=config_path,
        project_root=project_root,
        state_dir=state_dir,
    )
    output_format = "json" if getattr(args, "json", False) else args.format
    if output_format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 1 if report.get("status") == "STOP" else 0


def _run_render(args: argparse.Namespace) -> int:
    try:
        config, config_path, project_root, state_dir = _load(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    output = args.output or state_dir / "config" / "rendered-zf.yaml"
    lock = args.lock or state_dir / "config" / "render-lock.json"
    render_lock = write_rendered_config(
        config,
        config_path=config_path,
        project_root=project_root,
        state_dir=state_dir,
        output=output.expanduser().resolve(),
        lock_path=lock.expanduser().resolve(),
        include_secrets=bool(args.include_secrets),
    )
    print(json.dumps(render_lock, ensure_ascii=False, indent=2))
    return 0


def _load(config_path: Path | None):
    if config_path is not None:
        path = config_path.expanduser().resolve()
        config = load_config(path)
        project_root = path.parent
        state_dir = Path(config.project.state_dir)
        if not state_dir.is_absolute():
            state_dir = project_root / state_dir
        return config, path, project_root, state_dir.resolve()
    context = resolve_project_context(require_config=True)
    if context.config is None:
        raise ConfigError(f"Config file not found: {context.config_path}")
    return (
        context.config,
        context.config_path,
        context.project_root,
        context.state_dir,
    )


def _print_report(report: dict) -> None:
    summary = report.get("summary", {})
    source = report.get("source", {})
    project = report.get("project", {})
    print("# Config Inspect")
    print("")
    print(f"- Status: `{report.get('status', 'GO')}`")
    print(f"- Project: `{project.get('name', '')}`")
    print(f"- Source: `{source.get('path', '')}`")
    print(f"- Source sha256: `{source.get('sha256', '')}`")
    print(f"- Roles: `{summary.get('roles', 0)}`")
    print(f"- Stages: `{summary.get('stages', 0)}`")
    print(f"- Pipelines: `{summary.get('pipelines', 0)}`")
    print(f"- Event schemas: `{summary.get('event_schemas', 0)}`")
    profiles = list(source.get("profiles", []) or [])
    print(f"- Profile sources: `{len(profiles)}`")
    for item in profiles[:8]:
        if item.get("kind") == "ProfileSource":
            print(f"  - `{item.get('path', '')}`")
        else:
            print(f"  - `{item.get('kind', '')}:{item.get('name', '')}`")
    if len(profiles) > 8:
        print(f"  - ... {len(profiles) - 8} more")
    coverage = report.get("coverage", {}) or {}
    skill_matrix = coverage.get("skill_matrix", {}) or {}
    if skill_matrix:
        covered = sum(1 for row in skill_matrix.values() if row.get("covered"))
        print(f"- Skill coverage: `{covered}/{len(skill_matrix)}` parity scopes covered")
    print("")
    print("## Diagnostics")
    diagnostics = list(report.get("diagnostics", []) or [])
    if not diagnostics:
        print("- OK")
    for item in diagnostics:
        print(
            f"- [{item.get('severity', 'INFO')}] "
            f"`{item.get('kind', '')}`: {item.get('message', '')}"
        )
        if item.get("fix_it"):
            print(f"  fix-it: {item.get('fix_it')}")
