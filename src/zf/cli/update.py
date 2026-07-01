"""zf update — refresh ZaoFu-managed files (currently AGENTS.md block).

Subcommand-style CLI. Today only ``zf update agents-md`` exists. The shape
is set up to grow (``zf update workflow``, etc.) without renaming.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from zf.core.agents_md import (
    AgentsMdError,
    ZF_MARKER_END,
    ZF_MARKER_START,
    extract_managed_block,
    render_canonical_block,
    replace_managed_block,
)


_AGENTS_MD = "AGENTS.md"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "update",
        help="Refresh ZaoFu-managed files (AGENTS.md managed block, etc.)",
    )
    update_subs = parser.add_subparsers(dest="update_target")

    agents_md_parser = update_subs.add_parser(
        "agents-md",
        help="Update AGENTS.md kernel-managed block (<!-- ZF:START/END -->)",
    )
    agents_md_parser.add_argument(
        "--write",
        action="store_true",
        help="Apply changes (otherwise prints diff dry-run)",
    )
    agents_md_parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if AGENTS.md block is out of sync (for CI hooks)",
    )
    agents_md_parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help=f"Path to AGENTS.md (default: ./{_AGENTS_MD} in cwd)",
    )
    agents_md_parser.set_defaults(func=run_agents_md)

    parser.set_defaults(func=_dispatch_default)


def _dispatch_default(args: argparse.Namespace) -> int:
    """When no subcommand given, print help and exit non-zero."""
    print("Error: `zf update` requires a target. Available: agents-md",
          file=sys.stderr)
    return 2


def _resolve_agents_md_path(args: argparse.Namespace) -> Path:
    explicit = getattr(args, "path", None)
    return explicit if explicit is not None else Path.cwd() / _AGENTS_MD


def run_agents_md(args: argparse.Namespace) -> int:
    """Implementation of ``zf update agents-md [--write|--check]``."""
    path = _resolve_agents_md_path(args)
    if not path.exists():
        if args.check:
            print(
                f"Error: {path} not found; run `zf update agents-md --write` to "
                f"create it.",
                file=sys.stderr,
            )
            return 1
        if not args.write:
            print(
                f"Error: {path} not found; run `zf update agents-md --write` to "
                f"create it.",
                file=sys.stderr,
            )
            return 1
        # --write on missing file: create with just the managed block
        current_text = ""
    else:
        current_text = path.read_text(encoding="utf-8")

    try:
        current_inside = extract_managed_block(current_text) if current_text else None
    except AgentsMdError as exc:
        print(f"Error parsing {path}: {exc}", file=sys.stderr)
        return 1

    canonical_inside = render_canonical_block().rstrip("\n")
    new_text = replace_managed_block(current_text, canonical_inside)

    if new_text == current_text:
        if args.check:
            return 0
        print(f"{path}: managed block is already up to date.")
        return 0

    if args.check:
        # Out of sync — CI signal.
        print(
            f"{path}: managed block is out of sync. "
            f"Run `zf update agents-md --write`.",
            file=sys.stderr,
        )
        return 1

    if not args.write:
        # Dry run — print unified diff
        diff = difflib.unified_diff(
            current_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=str(path),
            tofile=f"{path} (after update)",
            n=3,
        )
        sys.stdout.write("".join(diff))
        sys.stdout.write(
            f"\n(dry-run — pass --write to apply, or --check for CI exit code)\n"
        )
        return 0

    # --write: atomic write via tempfile + rename
    tmp_path = path.with_suffix(path.suffix + ".zfupdate.tmp")
    try:
        tmp_path.write_text(new_text, encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    if current_inside is None:
        print(f"{path}: created managed block.")
    else:
        print(f"{path}: managed block updated.")
    return 0


def update_agents_md(path: Path | None = None, *, write: bool = False) -> int:
    """Library entry-point — same as ``run_agents_md`` but without argparse.

    Provided so that other tooling (test fixtures, automation) can update
    AGENTS.md without constructing a fake argparse Namespace.
    """
    args = argparse.Namespace(
        path=path,
        write=write,
        check=False,
    )
    return run_agents_md(args)
