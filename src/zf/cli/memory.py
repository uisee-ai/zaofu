"""zf memory — memory management CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from zf.core.config.project_context import resolve_state_dir
from zf.core.memory.store import MemoryStore
from zf.core.memory.staleness import StalenessChecker


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("memory", help="Memory management")
    parser.set_defaults(func=_run_show_shared)

    sub = parser.add_subparsers(dest="memory_cmd")

    show_p = sub.add_parser("show", help="Show memory entries")
    show_p.add_argument("role", nargs="?", default=None, help="Role name (omit for shared)")
    show_p.set_defaults(func=_run_show)

    add_p = sub.add_parser("add", help="Add a memory entry")
    add_p.add_argument("role", help="Role name (or 'shared')")
    add_p.add_argument("text", help="Memory content")
    add_p.add_argument("--type", default="decision", help="Memory type")
    add_p.set_defaults(func=_run_add)

    check_p = sub.add_parser("check", help="Check for stale entries")
    check_p.set_defaults(func=_run_check)


def _memory_dir() -> Path:
    return resolve_state_dir() / "memory"


def _store() -> MemoryStore:
    return MemoryStore(_memory_dir())


def _run_show_shared(args: argparse.Namespace) -> int:
    if getattr(args, "memory_cmd", None) is None:
        return _run_show(argparse.Namespace(role=None))
    return args.func(args)


def _run_show(args: argparse.Namespace) -> int:
    store = _store()
    role = getattr(args, "role", None)
    if role == "shared":
        role = None
    entries = store.get(role)
    if not entries:
        print(f"(no memory entries for {'shared' if role is None else role})")
        return 0
    for entry in entries:
        print(f"  [{entry.type}] {entry.content[:100]}")
    return 0


def _run_add(args: argparse.Namespace) -> int:
    store = _store()
    role = None if args.role == "shared" else args.role
    entry = store.add(role, args.type, args.text)
    print(f"Added [{entry.type}] to {'shared' if role is None else role}")
    return 0


def _run_check(args: argparse.Namespace) -> int:
    store = _store()
    checker = StalenessChecker(Path.cwd())

    total_stale = 0
    for md_file in _memory_dir().glob("*.md"):
        role = None if md_file.stem == "shared" else md_file.stem
        entries = store.get(role)
        stale = checker.check(entries)
        total_stale += len(stale)
        for s in stale:
            label = "shared" if role is None else role
            print(f"  STALE [{label}] {s.reason}: {s.entry.content[:60]}")

    if total_stale == 0:
        print("All memory entries are fresh.")
    else:
        print(f"\n{total_stale} stale entries found.")
    return 0
