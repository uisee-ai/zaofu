"""zf attach — put a human on a running role via the active transport."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from zf.core.config.loader import load_config, ConfigError
from zf.runtime.transport import make_transport, TmuxTransport
from zf.runtime.tmux import TmuxSession


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("attach", help="Attach to a running role")
    parser.add_argument("role", nargs="?", default=None, help="Role to focus")
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    config_path = Path.cwd() / "zf.yaml"

    if config_path.exists():
        try:
            config = load_config(config_path)
            transport = make_transport(config)
        except ConfigError:
            transport = TmuxTransport(TmuxSession(session_name="zf"))
    else:
        transport = TmuxTransport(TmuxSession(session_name="zf"))

    role = getattr(args, "role", None)
    handle = transport.attach_handle(role)
    if not handle.argv:
        print(
            f"Error: transport does not support live attach for role {role!r}.\n"
            f"  Tail logs instead: less +F .zf/logs/{role or '<role>'}.log",
            file=sys.stderr,
        )
        return 1

    print(f"Attaching: {handle.note or ' '.join(handle.argv)}")
    os.execvp(handle.argv[0], handle.argv)
    return 0  # unreachable after exec
