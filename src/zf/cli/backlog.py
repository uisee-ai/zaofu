"""Backlog/task drift audit helpers."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from zf.core.config.project_context import resolve_project_context


_ACTIVE_STATUSES = {"planning", "pending", "proposed", "todo", "not implemented"}
_DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")
_STATUS_RE = re.compile(r"(?:Status|\u72b6\u6001)\**\s*:?\s*(?P<status>[^\n|>]+)", re.I)


@dataclass(frozen=True)
class BacklogAuditFinding:
    path: str
    status: str
    age_days: int
    reason: str


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "backlog",
        help="Backlog/task document maintenance helpers",
    )
    sub = parser.add_subparsers(dest="backlog_cmd")
    audit = sub.add_parser("audit", help="Report stale active backlog/task docs")
    audit.add_argument("--root", type=Path, default=Path.cwd())
    audit.add_argument("--max-age-days", type=int, default=7)
    audit.add_argument("--json", action="store_true", dest="as_json")
    audit.set_defaults(func=_run_audit)

    wnd = sub.add_parser(
        "why-not-done",
        help="Explain why a task is not terminally done",
    )
    wnd.add_argument("task_id")
    wnd.add_argument("--state-dir", default=None)
    wnd.add_argument("--json", action="store_true", dest="as_json")
    wnd.set_defaults(func=_run_why_not_done)

    resume = sub.add_parser(
        "resume-packet",
        help="Build a runtime-generated resume packet for a task",
    )
    resume.add_argument("task_id")
    resume.add_argument("--state-dir", default=None)
    resume.add_argument("--dispatch-id", default="")
    resume.add_argument("--write", action="store_true")
    resume.add_argument("--json", action="store_true", dest="as_json")
    resume.set_defaults(func=_run_resume_packet)

    integration = sub.add_parser(
        "integration",
        help="Project a feature integration item",
    )
    integration.add_argument("feature_id")
    integration.add_argument("--state-dir", default=None)
    integration.add_argument("--json", action="store_true", dest="as_json")
    integration.set_defaults(func=_run_integration)

    workpad = sub.add_parser(
        "workpad",
        help="Project task workpad/progress facts from runtime state",
    )
    workpad.add_argument("task_id")
    workpad.add_argument("--state-dir", default=None)
    workpad.add_argument("--json", action="store_true", dest="as_json")
    workpad.set_defaults(func=_run_workpad)

    retry = sub.add_parser(
        "retry-metadata",
        help="Project retry/continuation metadata for a task",
    )
    retry.add_argument("task_id")
    retry.add_argument("--state-dir", default=None)
    retry.add_argument("--json", action="store_true", dest="as_json")
    retry.set_defaults(func=_run_retry_metadata)

    goal = sub.add_parser(
        "goal",
        help="Project a feature goal and its mapped work units",
    )
    goal.add_argument("feature_id")
    goal.add_argument("--state-dir", default=None)
    goal.add_argument("--json", action="store_true", dest="as_json")
    goal.set_defaults(func=_run_goal)
    parser.set_defaults(func=_run_help)


def audit_backlog_status(
    root: Path,
    *,
    max_age_days: int = 7,
    today: date | None = None,
) -> list[BacklogAuditFinding]:
    today = today or date.today()
    findings: list[BacklogAuditFinding] = []
    for folder in ("backlogs", "tasks"):
        base = root / folder
        if not base.exists():
            continue
        for path in sorted(base.glob("*.md")):
            status = _extract_status(path)
            if status.lower() not in _ACTIVE_STATUSES:
                continue
            doc_date = _extract_date(path.name)
            if doc_date is None:
                continue
            age_days = (today - doc_date).days
            if age_days <= max_age_days:
                continue
            findings.append(BacklogAuditFinding(
                path=str(path.relative_to(root)),
                status=status,
                age_days=age_days,
                reason=(
                    f"status {status!r} is still active after {age_days} days; "
                    "append an Implementation Status Update or link a successor backlog"
                ),
            ))
    return findings


def _run_help(args: argparse.Namespace) -> int:
    print("Usage: zf backlog audit [--json] [--max-age-days N]")
    return 0


def _run_audit(args: argparse.Namespace) -> int:
    findings = audit_backlog_status(
        Path(args.root),
        max_age_days=max(0, int(args.max_age_days)),
    )
    if args.as_json:
        print(json.dumps([asdict(item) for item in findings], ensure_ascii=False, indent=2))
        return 0
    if not findings:
        print("backlog audit: no stale active docs")
        return 0
    print("backlog audit: stale active docs")
    for item in findings:
        print(f"- {item.path}: status={item.status} age_days={item.age_days}")
    return 0


def _context(args: argparse.Namespace):
    return resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
        load_config_with_explicit=True,
    )


def _run_why_not_done(args: argparse.Namespace) -> int:
    from zf.runtime.long_horizon import project_why_not_done

    ctx = _context(args)
    projection = project_why_not_done(
        ctx.state_dir,
        args.task_id,
        config=ctx.config,
        project_root=ctx.project_root,
    )
    data = projection.to_dict()
    if args.as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"Task {data['task_id']} [{data['state']}]")
    reasons = data.get("why_not_done") or []
    if not reasons:
        print("done gate: no blocking why-not-done reason")
        return 0
    for item in reasons:
        print(
            f"- {item.get('severity', '')}: {item.get('kind', '')} — "
            f"{item.get('message', '')}"
        )
    action = data.get("recommended_action") or {}
    print(
        "recommended: "
        f"{action.get('kind', '')}"
        f" role={action.get('role', '')}"
        f" reason={action.get('reason', '')}"
    )
    return 0


def _run_resume_packet(args: argparse.Namespace) -> int:
    from zf.runtime.long_horizon import build_resume_packet, write_resume_packet

    ctx = _context(args)
    packet = build_resume_packet(
        ctx.state_dir,
        args.task_id,
        dispatch_id=args.dispatch_id,
        config=ctx.config,
        project_root=ctx.project_root,
    )
    written = ""
    if args.write:
        written = str(
            write_resume_packet(
                ctx.state_dir,
                packet,
                dispatch_id=args.dispatch_id,
            )
        )
    if args.as_json:
        if written:
            packet = {**packet, "written_path": written}
        print(json.dumps(packet, ensure_ascii=False, indent=2))
        return 0
    print(f"Resume packet for {args.task_id}")
    print(f"state: {packet.get('current_state', '')}")
    print(f"next: {packet.get('next_required_action', '')}")
    if written:
        print(f"written: {written}")
    return 0


def _run_integration(args: argparse.Namespace) -> int:
    from zf.runtime.long_horizon import build_integration_item

    ctx = _context(args)
    item = build_integration_item(
        ctx.state_dir,
        args.feature_id,
        project_root=ctx.project_root,
    ).to_dict()
    if args.as_json:
        print(json.dumps(item, ensure_ascii=False, indent=2))
        return 0
    print(f"Integration {item['id']} feature={item['feature_id']}")
    print(f"work_units: {len(item['work_units'])}")
    print(f"changed_files: {len(item['changed_files'])}")
    print(f"conflict_risk: {item['conflict_risk'].get('level', '')}")
    return 0


def _run_workpad(args: argparse.Namespace) -> int:
    from zf.runtime.long_horizon import project_workpad

    ctx = _context(args)
    item = project_workpad(
        ctx.state_dir,
        args.task_id,
        config=ctx.config,
        project_root=ctx.project_root,
    ).to_dict()
    if args.as_json:
        print(json.dumps(item, ensure_ascii=False, indent=2))
        return 0
    print(f"Workpad {item['task_id']} profile={item.get('effective_profile', '')}")
    print(f"plan_items: {len(item.get('plan') or [])}")
    print(f"validation_items: {len(item.get('validation') or [])}")
    print(f"blockers: {len(item.get('blockers') or [])}")
    return 0


def _run_retry_metadata(args: argparse.Namespace) -> int:
    from zf.runtime.long_horizon import project_retry_metadata

    ctx = _context(args)
    item = project_retry_metadata(ctx.state_dir, args.task_id).to_dict()
    if args.as_json:
        print(json.dumps(item, ensure_ascii=False, indent=2))
        return 0
    print(f"Retry metadata {item['task_id']}")
    print(f"attempt: {item.get('attempt', 0)}")
    print(f"worker: {item.get('worker', '')}")
    print(f"dispatch_id: {item.get('dispatch_id', '')}")
    print(f"stale: {item.get('stale', False)}")
    return 0


def _run_goal(args: argparse.Namespace) -> int:
    from zf.runtime.long_horizon import map_goal_to_work_units

    ctx = _context(args)
    item = map_goal_to_work_units(
        ctx.state_dir,
        args.feature_id,
        config=ctx.config,
    )
    if args.as_json:
        print(json.dumps(item, ensure_ascii=False, indent=2))
        return 0
    print(f"Goal {item['feature_id']}")
    print(f"work_units: {len(item.get('work_units') or [])}")
    return 0


def _extract_status(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    latest = ""
    for line in lines:
        match = _STATUS_RE.search(line.replace("`", ""))
        if match:
            raw = match.group("status").strip().strip("* ")
            parts = raw.split()
            if parts:
                latest = parts[0]
    return latest


def _extract_date(name: str) -> date | None:
    match = _DATE_RE.search(name)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group("date"))
    except ValueError:
        return None
