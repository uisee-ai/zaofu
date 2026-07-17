"""zf web — start the local Web dashboard (F-WEB-MVP-01).

Optional install: ``pip install -e ".[web]"``
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.project_context import ProjectContext, resolve_project_context


# doc 78 O-7 fix: single canonical dotenv loader (was duplicated here +
# in feishu.py). Alias keeps existing call sites unchanged.
from zf.core.config.project_context import (  # noqa: E402
    load_env_file as _load_env_file,
    load_project_env as _load_project_env,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "web",
        help="Start a local Web dashboard for the current .zf project",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Bind host (default 127.0.0.1; only set 0.0.0.0 if you trust the network)",
    )
    parser.add_argument("--port", type=int, default=8001, help="Bind port")
    parser.add_argument(
        "--state-dir", type=str, default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    parser.add_argument(
        "--reload", action="store_true",
        help="Enable uvicorn reload for development",
    )
    parser.add_argument(
        "--workspace-only",
        action="store_true",
        help="Start the Workspace shell without binding a default Project",
    )
    parser.set_defaults(func=run)


def _resolve_configured_state_dir(project_root: Path, state_dir: str | Path) -> Path:
    path = Path(state_dir)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve(strict=False)


def _context_from_explicit_state_dir(state_dir: Path) -> ProjectContext | None:
    """Find the zf.yaml that owns an explicit runtime state directory.

    `zf web --state-dir /tmp/project/.zf` is often launched from another
    repository while debugging a target run. In that case the current cwd is
    not the project root, so we walk upward from the state dir and accept the
    first zf.yaml whose project.state_dir resolves to the explicit path.
    """
    state_dir = state_dir.resolve(strict=False)
    for project_root in [state_dir.parent, *state_dir.parent.parents]:
        config_path = project_root / "zf.yaml"
        if not config_path.exists():
            continue
        config = load_config(config_path)
        configured_state_dir = _resolve_configured_state_dir(
            project_root,
            config.project.state_dir,
        )
        if configured_state_dir == state_dir:
            return ProjectContext(
                project_root=project_root.resolve(strict=False),
                config_path=config_path.resolve(strict=False),
                config=config,
                state_dir=state_dir,
            )
    return None


def _resolve_web_context(args: argparse.Namespace) -> ProjectContext:
    explicit_state_dir = getattr(args, "state_dir", None)
    context = resolve_project_context(explicit_state_dir=explicit_state_dir)
    if explicit_state_dir is None:
        return context
    return _context_from_explicit_state_dir(context.state_dir) or context


def run(args: argparse.Namespace) -> int:
    try:
        context = _resolve_web_context(args)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    _load_project_env(context.project_root)
    state_dir = context.state_dir
    explicit_state_dir = getattr(args, "state_dir", None)
    workspace_shell = bool(getattr(args, "workspace_only", False))
    if not state_dir.exists() and explicit_state_dir is not None:
        print(
            f"error: state dir {state_dir} not found — run `zf init` first or pass --state-dir",
            file=sys.stderr,
        )
        return 2
    if not state_dir.exists() and explicit_state_dir is None:
        workspace_shell = True

    try:
        import uvicorn
    except ImportError:
        print(
            "error: web dependencies not installed.\n"
            "       install with: pip install -e \".[web]\"",
            file=sys.stderr,
        )
        return 2

    # Lazy import so the web module's fastapi dep doesn't break base zf
    from zf.web.server import create_app, validate_trusted_session_host

    try:
        validate_trusted_session_host(args.host)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    app = create_app(
        state_dir,
        config=context.config,
        project_root=context.project_root,
        default_project_enabled=(context.config is not None and not getattr(args, "workspace_only", False)),
    )
    mode = "workspace" if workspace_shell else "project"
    print(
        f"zaofu dashboard → http://{args.host}:{args.port}/"
        f"  (mode={mode}, state_dir={state_dir})"
    )
    uvicorn.run(
        app, host=args.host, port=args.port, log_level="warning",
        reload=args.reload,
        # Long-lived SSE streams never close on their own; without a bound,
        # graceful shutdown waits on them forever and `zf web` shrugs off
        # SIGTERM (needed SIGKILL twice on 2026-07-16). Five seconds drains
        # normal requests, then open streams are forced shut.
        timeout_graceful_shutdown=5,
    )
    return 0
