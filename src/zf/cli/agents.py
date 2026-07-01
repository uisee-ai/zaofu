"""zf agents — list detected agent CLIs."""

from __future__ import annotations

import argparse
import shutil


_KNOWN_AGENTS = {
    "claude": {"name": "Claude Code", "backend": "claude-code"},
    "codex": {"name": "Codex", "backend": "codex"},
}


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("agents", help="List detected agent CLIs")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    print("Detected agent CLIs:\n")
    found = 0
    for cmd, info in _KNOWN_AGENTS.items():
        path = shutil.which(cmd)
        if path:
            print(f"  {info['name']:15s}  backend={info['backend']:15s}  path={path}")
            found += 1
        else:
            print(f"  {info['name']:15s}  backend={info['backend']:15s}  (not found)")

    print(f"\n{found}/{len(_KNOWN_AGENTS)} agent CLIs available.")
    if found == 0:
        print("To fix: install at least one agent CLI (e.g., 'npm install -g @anthropic-ai/claude-code')")
    return 0
