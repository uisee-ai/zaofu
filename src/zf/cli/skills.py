"""zf skills — inspect enabled skill resolution and runtime projection."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.skills import build_skill_lock_entries


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("skills", help="Inspect configured skills")
    sub = parser.add_subparsers(dest="skills_cmd")

    list_p = sub.add_parser("list", help="List enabled role skills")
    list_p.add_argument("--json", action="store_true", help="Emit JSON")
    list_p.set_defaults(func=_run_list)

    doctor_p = sub.add_parser("doctor", help="Check enabled skill health")
    doctor_p.add_argument("--json", action="store_true", help="Emit JSON")
    doctor_p.set_defaults(func=_run_doctor)

    parser.set_defaults(func=_run_help)


def _run_help(args: argparse.Namespace) -> int:
    print("Usage: zf skills <list|doctor>", file=sys.stderr)
    return 2


def _run_list(args: argparse.Namespace) -> int:
    try:
        report = _build_report()
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    _print_report(report, include_ok=True)
    return 0


def _run_doctor(args: argparse.Namespace) -> int:
    try:
        report = _build_report()
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["issues"] else 0
    _print_report(report, include_ok=False)
    if report["issues"]:
        print("Skill issues:")
        for issue in report["issues"]:
            print(f"  - {issue}")
        return 1
    print("OK: skills")
    return 0


def _build_report() -> dict[str, Any]:
    context = resolve_project_context(require_config=True)
    config = context.config
    if config is None:
        raise ConfigError(f"Config file not found: {context.config_path}")

    manifests = _read_manifests(context.state_dir)
    entries: list[dict[str, Any]] = []
    issues: list[str] = []

    for role in config.roles:
        role_entries = build_skill_lock_entries(
            project_root=context.project_root,
            state_dir=context.state_dir,
            role=role,
            config=config,
            materialized_paths=_manifest_materialized_paths(
                manifests.get(role.instance_id, {})
            ),
        )
        for entry in role_entries:
            item = asdict(entry)
            manifest_item = _manifest_item(
                manifests.get(role.instance_id, {}),
                entry.name,
            )
            if manifest_item:
                item["manifest_status"] = manifest_item.get("status", "")
                item["materialized_to"] = (
                    item.get("materialized_to")
                    or manifest_item.get("materialized_to")
                )
            else:
                item["manifest_status"] = "not_materialized"
            entries.append(item)
            _collect_entry_issues(item, issues)

    return {
        "project_root": str(context.project_root),
        "state_dir": str(context.state_dir),
        "skill_sources": [
            {"name": source.name, "path": source.path, "mode": source.mode}
            for source in config.skill_sources
        ],
        "enabled": entries,
        "issues": issues,
    }


def _read_manifests(state_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    root = state_dir / "workdirs"
    if not root.exists():
        return out
    for path in sorted(root.glob("*/runtime/skills-manifest.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        instance_id = str(data.get("instance_id") or path.parents[1].name)
        out[instance_id] = data
    return out


def _manifest_item(manifest: dict[str, Any], skill_name: str) -> dict[str, Any]:
    for item in manifest.get("skills", []) or []:
        if item.get("name") == skill_name:
            return item
    return {}


def _manifest_materialized_paths(manifest: dict[str, Any]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for item in manifest.get("skills", []) or []:
        name = str(item.get("name") or "")
        materialized_to = str(item.get("materialized_to") or "")
        if name and materialized_to:
            out[name] = Path(materialized_to)
    return out


def _collect_entry_issues(item: dict[str, Any], issues: list[str]) -> None:
    role = item.get("instance_id") or item.get("role")
    name = item.get("name")
    status = item.get("status")
    if status in {"missing", "invalid"}:
        issues.append(f"{role}: {name} status={status}")
    if item.get("warnings"):
        issues.append(f"{role}: {name} warnings={'; '.join(item['warnings'])}")
    if item.get("collision_candidates"):
        issues.append(
            f"{role}: {name} collision candidates: "
            f"{', '.join(item['collision_candidates'])}"
        )


def _print_report(report: dict[str, Any], *, include_ok: bool) -> None:
    print(f"Project: {report['project_root']}")
    print(f"State: {report['state_dir']}")
    sources = report["skill_sources"]
    if sources:
        print("Skill sources:")
        for source in sources:
            print(f"  - {source['name']}: {source['path']} ({source['mode']})")
    enabled = report["enabled"]
    if not enabled:
        print("No enabled skills.")
        return
    print("Enabled skills:")
    for item in enabled:
        status = item.get("status") or "unknown"
        if not include_ok and status == "resolved" and not item.get("collision_candidates"):
            continue
        source = item.get("source_name") or "-"
        materialized = item.get("materialized_to") or "-"
        print(
            f"  - {item['instance_id']}::{item['name']} "
            f"status={status} source={source} materialized_to={materialized}"
        )
