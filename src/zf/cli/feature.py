"""zf feature — manage L1 user-given features in .zf/feature_list.json."""

from __future__ import annotations

import argparse
import json
import sys

from zf.core.config.project_context import resolve_project_context
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("feature", help="Manage features (high-level user goals)")
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Runtime state dir (default: project.state_dir from zf.yaml)",
    )
    sub = parser.add_subparsers(dest="feature_command")

    add = sub.add_parser("add", help="Add a new feature")
    add.add_argument("title")
    add.add_argument("--message", "-m", default="", help="Original user message")
    add.add_argument("--priority", "-p", type=int, default=3, help="Priority 1 (highest) - 5 (lowest)")
    add.add_argument("--description", "-d", default="")
    add.add_argument(
        "--id-only",
        action="store_true",
        help="Print only the created feature id for scripts.",
    )
    add.add_argument(
        "--json",
        action="store_true",
        help="Print a machine-readable JSON object for scripts.",
    )
    add.set_defaults(func=_add)

    list_p = sub.add_parser("list", help="List features")
    list_p.add_argument("--status", default=None, help="Filter by status")
    list_p.set_defaults(func=_list)

    show = sub.add_parser("show", help="Show a feature in detail")
    show.add_argument("feature_id")
    show.set_defaults(func=_show)

    update = sub.add_parser("update", help="Update a feature")
    update.add_argument("feature_id")
    update.add_argument("--status", default=None)
    update.add_argument("--priority", type=int, default=None)
    update.add_argument("--title", default=None)
    update.set_defaults(func=_update)

    parser.set_defaults(func=lambda args: parser.print_help() or 0)


def _store(args: argparse.Namespace | None = None) -> FeatureStore:
    state_dir = resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
    ).state_dir
    return FeatureStore(state_dir / "feature_list.json")


def _add(args: argparse.Namespace) -> int:
    f = Feature(
        title=args.title,
        description=args.description,
        priority=args.priority,
        user_message=args.message or args.title,
    )
    _store(args).add(f)
    if getattr(args, "json", False):
        print(json.dumps({
            "feature_id": f.id,
            "id": f.id,
            "title": f.title,
            "status": f.status,
            "priority": f.priority,
        }, ensure_ascii=False))
    elif getattr(args, "id_only", False):
        print(f.id)
    else:
        print(f"Added {f.id}: {f.title} (feature_id={f.id})")
    return 0


def _list(args: argparse.Namespace) -> int:
    store = _store(args)
    features = store.filter(status=args.status) if args.status else store.list_all()
    if not features:
        print("(no features)")
        return 0
    for f in features:
        print(f"{f.id}  [{f.status}]  p{f.priority}  {f.title}")
    return 0


def _show(args: argparse.Namespace) -> int:
    f = _store(args).get(args.feature_id)
    if f is None:
        print(f"Error: feature {args.feature_id} not found", file=sys.stderr)
        return 1
    print(f"# {f.id}: {f.title}")
    print(f"Status: {f.status}")
    print(f"Priority: {f.priority}")
    print(f"Created: {f.created_at}")
    if f.completed_at:
        print(f"Completed: {f.completed_at}")
    if f.description:
        print(f"\nDescription: {f.description}")
    if f.user_message:
        print(f"\nUser message: {f.user_message}")
    return 0


def _update(args: argparse.Namespace) -> int:
    kwargs: dict = {}
    if args.status is not None:
        kwargs["status"] = args.status
    if args.priority is not None:
        kwargs["priority"] = args.priority
    if args.title is not None:
        kwargs["title"] = args.title
    if not kwargs:
        print("Error: nothing to update (provide --status / --priority / --title)", file=sys.stderr)
        return 1
    try:
        f = _store(args).update(args.feature_id, **kwargs)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if f is None:
        print(f"Error: feature {args.feature_id} not found", file=sys.stderr)
        return 1
    print(f"Updated {f.id}: {kwargs}")
    return 0
