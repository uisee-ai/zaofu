"""zf state — deterministic runtime-state maintenance commands."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.safety import PathGuard, PathGuardError


_TRUTH_FILES = (
    "events.jsonl",
    "kanban.json",
    "feature_list.json",
    "session.yaml",
    "role_sessions.yaml",
)
_REBUILDABLE_DIRS = (
    "briefings",
    "diagnostics",
    "instructions",
    "logs",
    "skills",
    "workdirs",
)
_REBUILDABLE_FILES = (
    "cost.jsonl",
    "progress.md",
    "skills.lock.json",
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("state", help="Inspect or clean runtime state")
    sub = parser.add_subparsers(dest="state_cmd")

    clean = sub.add_parser("clean", help="Clean rebuildable runtime projections")
    clean.add_argument("--dry-run", action="store_true", help="Only print actions")
    clean.add_argument("--confirm", action="store_true", help="Apply safe cleanup")
    clean.add_argument("--archive", action="store_true", help="Assert archive intent")
    clean.add_argument(
        "--preserve-config",
        action="store_true",
        help="Kept for command compatibility; zf.yaml is always preserved",
    )
    clean.set_defaults(func=_run_clean)

    reconcile = sub.add_parser(
        "reconcile",
        help="Detect kanban ⇄ tmux pane desync (in_progress without live worker)",
    )
    reconcile.add_argument(
        "--reset", action="store_true",
        help="Reset orphaned in_progress tasks back to `ready`. "
             "Without this flag, only reports.",
    )
    reconcile.add_argument(
        "--dry-run", action="store_true",
        help="Print intended resets without applying. Implies report mode.",
    )
    reconcile.add_argument(
        "--state-dir", default=None,
        help="Override project state_dir (default: from zf.yaml).",
    )
    reconcile.set_defaults(func=_run_reconcile)

    parser.set_defaults(func=_run_help)


def _run_help(args: argparse.Namespace) -> int:
    print("Usage: zf state <clean>", file=sys.stderr)
    return 2


def _run_clean(args: argparse.Namespace) -> int:
    try:
        report = _build_clean_report(archive_requested=bool(args.archive))
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    dry_run = bool(args.dry_run) or not bool(args.confirm)
    _print_clean_report(report, dry_run=dry_run)
    if dry_run:
        return 0 if not report["hard_blockers"] else 1

    if report["hard_blockers"] or report["archive_blockers"]:
        print("Refusing to clean until blockers are resolved.", file=sys.stderr)
        return 1

    removed: list[str] = []
    try:
        for path_text in report["delete_candidates"]:
            path = Path(path_text)
            PathGuard.assert_under(path, Path(report["state_dir"]))
            PathGuard.assert_safe_to_delete(path)
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            removed.append(path_text)
    except (OSError, PathGuardError) as exc:
        print(f"Error: unsafe cleanup path: {exc}", file=sys.stderr)
        return 1

    print("Clean completed:")
    for item in removed:
        print(f"  - removed {item}")
    if not removed:
        print("  - nothing to remove")
    return 0


def _build_clean_report(*, archive_requested: bool) -> dict[str, Any]:
    context = resolve_project_context(require_config=True)
    state_dir = context.state_dir
    report: dict[str, Any] = {
        "project_root": str(context.project_root),
        "state_dir": str(state_dir),
        "archive_requested": archive_requested,
        "truth_files": [],
        "delete_candidates": [],
        "hard_blockers": [],
        "archive_blockers": [],
    }

    if not state_dir.exists():
        report["hard_blockers"].append(f"state_dir missing: {state_dir}")
        return report

    lock_path = state_dir / "loop.lock"
    if lock_path.exists():
        report["hard_blockers"].append(f"harness appears running: {lock_path}")

    for name in _TRUTH_FILES:
        path = state_dir / name
        if path.exists():
            report["truth_files"].append(str(path))
            if _has_content(path) and not archive_requested:
                report["archive_blockers"].append(
                    f"{name} has content; use --archive after archiving evidence"
                )

    for name in _REBUILDABLE_DIRS:
        path = state_dir / name
        if path.exists():
            report["delete_candidates"].append(str(path))
    for name in _REBUILDABLE_FILES:
        path = state_dir / name
        if path.exists():
            report["delete_candidates"].append(str(path))
    return report


def _has_content(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        return True


def _print_clean_report(report: dict[str, Any], *, dry_run: bool) -> None:
    mode = "dry-run" if dry_run else "confirm"
    print(f"State clean ({mode})")
    print(f"Project: {report['project_root']}")
    print(f"State: {report['state_dir']}")
    if report["truth_files"]:
        print("Truth files preserved:")
        for item in report["truth_files"]:
            print(f"  - {item}")
    if report["delete_candidates"]:
        print("Rebuildable delete candidates:")
        for item in report["delete_candidates"]:
            print(f"  - {item}")
    else:
        print("No rebuildable delete candidates.")
    if report["hard_blockers"]:
        print("Hard blockers:")
        for item in report["hard_blockers"]:
            print(f"  - {item}")
    if report["archive_blockers"]:
        print("Archive blockers:")
        for item in report["archive_blockers"]:
            print(f"  - {item}")


# ---------------------------------------------------------------------------
# zf state reconcile — detect kanban ⇄ tmux pane desync
# (backlog P2 #10, 2026-05-14)
#
# Scenario: an orchestrator crash / tmux kill / OS reboot can leave
# kanban tasks marked `in_progress` (assigned_to=<role>) while the
# corresponding tmux pane is gone. Without intervention, the orchestrator
# never finds anyone to "rework" the task (the pane is dead), and WIP=1
# slots silently leak. `reconcile` surfaces these.
# ---------------------------------------------------------------------------

_INFLIGHT_STATUSES = {"in_progress", "dispatched", "ready"}


def _run_reconcile(args: argparse.Namespace) -> int:
    import json
    from zf.core.task.store import TaskStore
    from zf.core.events import event_log_from_project
    from zf.core.events.writer import EventWriter
    from zf.core.events.factory import EventSigningConfigError

    try:
        ctx = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    state_dir = ctx.state_dir
    if not state_dir.exists():
        print(f"Error: state dir does not exist: {state_dir}", file=sys.stderr)
        return 1

    task_store = TaskStore(state_dir / "kanban.json")
    inflight = [t for t in task_store.list_all() if t.status in _INFLIGHT_STATUSES]
    if not inflight:
        print("OK: no in-flight tasks; nothing to reconcile.")
        return 0

    live_panes = _live_tmux_panes(ctx.config)

    orphans: list[tuple[str, str, str, str]] = []  # (task_id, status, assignee, reason)
    healthy = 0
    for task in inflight:
        assignee = (task.assigned_to or "").strip()
        if not assignee:
            if task.status == "in_progress":
                orphans.append((task.id, task.status, "<none>",
                                "in_progress but no assignee"))
            else:
                healthy += 1
            continue
        if assignee in live_panes:
            healthy += 1
        else:
            orphans.append((
                task.id, task.status, assignee,
                f"assignee {assignee!r} has no live tmux pane",
            ))

    print(f"reconcile: {len(inflight)} in-flight task(s)")
    print(f"  healthy:  {healthy}")
    print(f"  orphaned: {len(orphans)}")
    print(f"  live panes: {sorted(live_panes) if live_panes else '(no tmux session)'}")
    if not orphans:
        return 0

    print()
    print("Orphaned tasks:")
    for tid, status, assignee, reason in orphans:
        print(f"  - {tid:14s}  status={status:12s}  assignee={assignee:12s}  {reason}")

    if not getattr(args, "reset", False) or getattr(args, "dry_run", False):
        print()
        print("Run `zf state reconcile --reset` to push these tasks back to `ready`")
        print("(removes assignee, clears dispatched_at; safe — events are append-only).")
        return 0 if not orphans else 2  # 2 = state inconsistent, no action taken

    # Reset: status → ready, assignee → None, dispatched_at → None
    try:
        event_log = event_log_from_project(state_dir, config=ctx.config)
    except EventSigningConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    writer = EventWriter(event_log)

    reset_count = 0
    for tid, _status, assignee, reason in orphans:
        task_store.update(
            tid, status="ready", assigned_to=None, dispatched_at=None,
            active_dispatch_id="",
        )
        writer.emit(
            "task.status_changed",
            actor="zf-cli",
            task_id=tid,
            payload={
                "from_status": _status,
                "to_status": "ready",
                "from_assignee": assignee,
                "to_assignee": None,
                "source": "state_reconcile",
                "reason": reason,
            },
        )
        reset_count += 1

    print(f"\nreset {reset_count} task(s) back to `ready`.")
    return 0


def _live_tmux_panes(config) -> set[str]:
    """Return the set of role instance_ids currently visible as tmux panes.

    Matches the 3-signal logic in
    ``src/zf/runtime/tmux_layout.py:_find_pane_by_instance``:
      1. ``@zf_instance_id`` user option set on the pane
      2. ``pane_title`` equals the instance_id (rarely survives — claude/
         codex overwrite the title with their activity)
      3. ``pane_current_path`` contains ``/.zf/workdirs/<instance_id>/``

    Returns empty set if tmux is not running OR the session does not
    exist (the reconcile path treats that as "no live workers", i.e.
    every in-flight task is potentially orphaned — operator decides).
    """
    import subprocess

    session_name = getattr(config.session, "tmux_session", "") if config else ""
    if not session_name:
        return set()
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-t", session_name, "-a",
             "-F", "#{@zf_instance_id}\t#{pane_title}\t#{pane_current_path}"],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()

    known: set[str] = set()
    if config is not None and getattr(config, "roles", None):
        # The loader already expands replicas into per-instance RoleConfig
        # entries with distinct ``instance_id`` (e.g. dev-1..dev-4),
        # so we read that field directly instead of guessing replica suffixes.
        for role in config.roles:
            inst = getattr(role, "instance_id", "") or role.name
            known.add(inst)

    found: set[str] = set()
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t", 2)
        while len(parts) < 3:
            parts.append("")
        opt_instance, title, path = (p.strip() for p in parts)
        if opt_instance and opt_instance in known:
            found.add(opt_instance)
            continue
        if title and title in known:
            found.add(title)
            continue
        for inst in known:
            if f"/.zf/workdirs/{inst}/" in path:
                found.add(inst)
                break
    return found
