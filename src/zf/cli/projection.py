"""zf projection — inspect and rebuild read-side projections."""

from __future__ import annotations

import argparse
import json
import sys

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("projection", help="Inspect and rebuild read models")
    parser.add_argument(
        "--state-dir",
        type=str,
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    sub = parser.add_subparsers(dest="projection_command", required=True)

    status = sub.add_parser("status", help="Show read-model status")
    status.add_argument("--json", action="store_true", help="Print JSON")
    status.add_argument(
        "--count-source",
        action="store_true",
        help="Count source log rows to compute exact lag",
    )
    status.set_defaults(func=run_status)

    rebuild = sub.add_parser("rebuild", help="Rebuild read model from events.jsonl")
    rebuild.add_argument(
        "--projection",
        choices=["all", "event-index", "task-timeline", "channel-inbox", "delivery-loop", "heavy"],
        default="all",
        help="Projection name. Non-all names currently rebuild the shared read model.",
    )
    rebuild.add_argument("--json", action="store_true", help="Print JSON")
    rebuild.set_defaults(func=run_rebuild)

    doctor = sub.add_parser("doctor", help="Diagnose read-model freshness and schema")
    doctor.add_argument("--json", action="store_true", help="Print JSON")
    doctor.set_defaults(func=run_doctor)


def run_status(args: argparse.Namespace) -> int:
    context = _context(args)
    if context is None:
        return 1
    from zf.web.projections import read_model

    status = read_model.projection_status(
        context.state_dir,
        count_source=bool(getattr(args, "count_source", False)),
    )
    if getattr(args, "json", False):
        print(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    _print_status(status)
    return 0


def run_rebuild(args: argparse.Namespace) -> int:
    context = _context(args)
    if context is None:
        return 1
    from zf.web.projections import read_model

    result = read_model.rebuild(context.state_dir, config=context.config)
    result["projection"] = getattr(args, "projection", "all")
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(f"projection: {result['projection']}")
    print(f"state:      {result.get('projection_state', '')}")
    print(f"source_seq: {result.get('source_seq', 0)}")
    print(f"inserted:   {result.get('inserted', 0)}")
    return 0


def run_doctor(args: argparse.Namespace) -> int:
    context = _context(args)
    if context is None:
        return 1
    from zf.web.projections import read_model

    status = read_model.projection_status(context.state_dir, count_source=True)
    findings: list[dict[str, str]] = []
    if status.get("projection_state") == "missing":
        findings.append({
            "severity": "warning",
            "code": "read_model_missing",
            "message": "read model database is missing; run `zf projection rebuild`",
        })
    if status.get("projected_manifest_digest") and (
        status.get("projected_manifest_digest") != status.get("manifest_digest")
    ):
        findings.append({
            "severity": "info",
            "code": "manifest_drift",
            "message": "event log changed after the last read-model catch-up",
        })
    lag = int(status.get("projection_lag") or 0)
    if lag > 0:
        findings.append({
            "severity": "warning" if lag > 1000 else "info",
            "code": "projection_lag",
            "message": f"read model is {lag} event(s) behind source",
        })
    result = {
        "schema_version": "projection-doctor.v1",
        "status": "ok" if not findings else "attention",
        "findings": findings,
        "projection": status,
    }
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if not any(item["severity"] == "error" for item in findings) else 2
    _print_status(status)
    if not findings:
        print("doctor:     ok")
        return 0
    print("doctor:     attention")
    for item in findings:
        print(f"- [{item['severity']}] {item['code']}: {item['message']}")
    return 0


def _context(args: argparse.Namespace):
    try:
        return resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None


def _print_status(status: dict) -> None:
    print(f"schema:     {status.get('schema_version', '')}")
    print(f"state:      {status.get('projection_state', '')}")
    print(f"db:         {status.get('db_path', '')}")
    print(f"source_seq: {status.get('source_seq', 0)}")
    print(f"projected:  {status.get('projected_seq', 0)}")
    print(f"lag:        {status.get('projection_lag')}")
    print(f"segments:   {status.get('segment_count', 0)}")
    print(f"updated_at: {status.get('updated_at', '') or '-'}")
