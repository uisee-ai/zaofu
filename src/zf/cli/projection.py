"""zf projection — inspect and rebuild read-side projections."""

from __future__ import annotations

import argparse
import json
import sys

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context


_PROJECTION_CHOICES = (
    "all",
    "event-index",
    "artifact-catalog",
    "task-timeline",
    "channel-inbox",
    "delivery-loop",
    "heavy",
)


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
    status.add_argument(
        "--projection",
        choices=_PROJECTION_CHOICES,
        default="all",
        help="Projection component to inspect.",
    )
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
        choices=_PROJECTION_CHOICES,
        default="all",
        help="Projection name. Non-all names currently rebuild the shared read model.",
    )
    rebuild.add_argument("--json", action="store_true", help="Print JSON")
    rebuild.set_defaults(func=run_rebuild)

    doctor = sub.add_parser("doctor", help="Diagnose read-model freshness and schema")
    doctor.add_argument(
        "--projection",
        choices=_PROJECTION_CHOICES,
        default="all",
        help="Projection component to diagnose.",
    )
    doctor.add_argument("--json", action="store_true", help="Print JSON")
    doctor.set_defaults(func=run_doctor)


def run_status(args: argparse.Namespace) -> int:
    context = _context(args)
    if context is None:
        return 1
    status = _status(
        context,
        target=str(getattr(args, "projection", "all") or "all"),
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
    target = str(getattr(args, "projection", "all") or "all")
    result = _rebuild(context, target=target)
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    print(f"projection: {result['projection']}")
    print(f"state:      {result.get('overall_state', result.get('projection_state', ''))}")
    for name, item in (result.get("projections") or {}).items():
        print(
            f"- {name}: {item.get('projection_state', '')} "
            f"(source_seq={item.get('source_seq', item.get('projected_seq', 0))})"
        )
    return 0


def run_doctor(args: argparse.Namespace) -> int:
    context = _context(args)
    if context is None:
        return 1
    status = _status(
        context,
        target=str(getattr(args, "projection", "all") or "all"),
        count_source=True,
    )
    findings: list[dict[str, str]] = []
    for name, item in (status.get("projections") or {}).items():
        state = str(item.get("projection_state") or "")
        if state in {"missing", "stale", "corrupt"}:
            findings.append({
                "severity": "error" if state == "corrupt" else "warning",
                "code": f"{name.replace('-', '_')}_{state}",
                "message": (
                    f"{name} is {state}; run `zf projection rebuild "
                    f"--projection {name}`"
                ),
            })
        lag = int(item.get("projection_lag") or 0)
        if lag > 0:
            findings.append({
                "severity": "warning" if lag > 1000 else "info",
                "code": f"{name.replace('-', '_')}_lag",
                "message": f"{name} is {lag} event(s) behind source",
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
    print(f"projection: {status.get('projection', '')}")
    print(f"state:      {status.get('overall_state', '')}")
    for name, item in (status.get("projections") or {}).items():
        print(f"[{name}]")
        print(f"  state:      {item.get('projection_state', '')}")
        print(f"  db:         {item.get('db_path', status.get('db_path', ''))}")
        print(f"  source_seq: {item.get('source_seq', 0)}")
        print(f"  projected:  {item.get('projected_seq', 0)}")
        print(f"  lag:        {item.get('projection_lag')}")
        print(f"  updated_at: {item.get('updated_at', '') or '-'}")


def _normalized_target(target: str) -> str:
    return (
        target
        if target in {"all", "event-index", "artifact-catalog"}
        else "event-index"
    )


def _status(context, *, target: str, count_source: bool) -> dict:
    from zf.runtime.artifact_query.store import catalog_status
    from zf.web.projections import read_model

    target = _normalized_target(target)
    projections: dict[str, dict] = {}
    if target in {"all", "event-index"}:
        event_index = read_model.projection_status(
            context.state_dir,
            count_source=count_source,
        )
        if (
            event_index.get("projection_state") == "stale"
            and not event_index.get("projected_manifest_digest")
            and not event_index.get("updated_at")
        ):
            # The artifact catalog shares the SQLite file and initializes the
            # event tables, but that does not mean the event index was built.
            event_index["projection_state"] = "missing"
        projections["event-index"] = event_index
    if target in {"all", "artifact-catalog"}:
        catalog = catalog_status(context.state_dir)
        catalog["db_path"] = str(
            context.state_dir / "projections" / "read_model.sqlite"
        )
        projections["artifact-catalog"] = catalog
    states = [
        str(item.get("projection_state") or "missing")
        for item in projections.values()
    ]
    overall = next(
        (
            state for state in ("corrupt", "stale", "missing")
            if state in states
        ),
        "ready",
    )
    return {
        "schema_version": "projection-status.v2",
        "projection": target,
        "overall_state": overall,
        "projections": projections,
    }


def _rebuild(context, *, target: str) -> dict:
    from zf.runtime.artifact_query.store import rebuild_catalog
    from zf.web.projections import read_model

    target = _normalized_target(target)
    projections: dict[str, dict] = {}
    if target in {"all", "event-index"}:
        projections["event-index"] = read_model.rebuild(
            context.state_dir,
            config=context.config,
        )
    if target in {"all", "artifact-catalog"}:
        projections["artifact-catalog"] = rebuild_catalog(
            context.state_dir,
            project_root=context.project_root,
            config=context.config,
            force=True,
        )
    states = [
        str(item.get("projection_state") or "")
        for item in projections.values()
    ]
    return {
        "schema_version": "projection-rebuild.v2",
        "projection": target,
        "overall_state": "ready" if states and set(states) == {"ready"} else "attention",
        "projections": projections,
    }
