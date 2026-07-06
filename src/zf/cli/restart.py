"""zf restart — restart the harness or a single role."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events import EventLog, ZfEvent
from zf.runtime.transport import make_transport
from zf.runtime.backend import get_adapter
from zf.runtime.injection import generate_role_instructions


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("restart", help="Restart the harness or a single role")
    parser.add_argument("role", nargs="?", default=None, help="Role to restart (omit for full restart)")
    parser.add_argument("--dry-run", action="store_true", help="Record commands without executing tmux")
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Runtime state dir (default: project.state_dir from zf.yaml)",
    )
    parser.add_argument(
        "--preserve-run-manager",
        action="store_true",
        help="Preserve a dedicated resident Run Manager tmux session during full restart",
    )
    parser.add_argument(
        "--include-run-manager",
        action="store_true",
        help="Also restart the dedicated resident Run Manager session",
    )
    parser.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    dry_run = getattr(args, "dry_run", False)

    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
            require_config=True,
            load_config_with_explicit=True,
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    config = context.config
    state_dir = context.state_dir
    if config is None:
        print("Error: zf.yaml not found. To fix: run 'zf init'", file=sys.stderr)
        return 1

    transport = make_transport(config, dry_run=dry_run)
    event_log = EventLog(state_dir / "events.jsonl") if state_dir.exists() else None

    if args.role:
        if not _run_contract_restart_allowed(
            config,
            event_log,
            state_dir,
            context.project_root,
            context.config_path,
        ):
            print("Error: run contract drift detected; restart blocked for strict run.", file=sys.stderr)
            return 1
        return _restart_role(
            args.role,
            config,
            transport,
            event_log,
            state_dir,
            context.project_root,
            dry_run,
        )
    else:
        if not _run_contract_restart_allowed(
            config,
            event_log,
            state_dir,
            context.project_root,
            context.config_path,
        ):
            print("Error: run contract drift detected; full restart blocked for strict run.", file=sys.stderr)
            return 1
        return _restart_full(
            config,
            transport,
            event_log,
            state_dir,
            dry_run,
            preserve_run_manager=getattr(args, "preserve_run_manager", False),
            include_run_manager=getattr(args, "include_run_manager", False),
        )


def _run_contract_restart_allowed(
    config,
    event_log,
    state_dir: Path,
    project_root: Path,
    config_path: Path,
) -> bool:
    try:
        from zf.runtime.run_contract import evaluate_run_contract_resume_policy

        policy = evaluate_run_contract_resume_policy(
            config,
            config_path=config_path,
            project_root=project_root,
            state_dir=state_dir,
        )
    except Exception:
        return True
    if policy.get("status") in {"STOP", "WARN"} and event_log is not None:
        event_log.append(ZfEvent(
            type="config.run_contract.resume_checked",
            actor="zf-cli",
            payload=policy,
        ))
    return policy.get("status") != "STOP"


def _restart_role(
    role_name: str,
    config,
    transport,
    event_log,
    state_dir: Path,
    project_root: Path,
    dry_run: bool,
) -> int:
    """Restart a single role's pane."""
    role = next((r for r in config.roles if r.name == role_name), None)
    if role is None:
        print(f"Error: Role '{role_name}' not found. Available: {[r.name for r in config.roles]}", file=sys.stderr)
        return 1

    # Tear down and respawn
    transport.terminate(role_name)
    adapter = get_adapter(role.backend)
    transport.spawn(role, adapter.build_command(role))

    instructions = generate_role_instructions(
        config,
        role,
        state_dir_ref=state_dir,
        project_root=project_root,
    )
    instructions_dir = state_dir / "instructions"
    instructions_dir.mkdir(parents=True, exist_ok=True)
    (instructions_dir / f"{role_name}.md").write_text(instructions)

    if event_log:
        event_log.append(ZfEvent(
            type="worker.restarted", actor="zf-cli",
            payload={"role": role_name},
        ))

    print(f"Restarted role: {role_name}")
    return 0


def _restart_full(
    config,
    transport,
    event_log,
    state_dir,
    dry_run: bool,
    *,
    preserve_run_manager: bool = False,
    include_run_manager: bool = False,
) -> int:
    """Full restart: stop + start sequence."""
    from zf.cli.stop import run as stop_run
    from zf.cli.start import run as start_run

    # Stop
    stop_args = argparse.Namespace(
        force=False,
        fast=False,
        preserve_run_manager=preserve_run_manager,
        include_run_manager=include_run_manager,
    )
    stop_run(stop_args)

    # Start
    start_args = argparse.Namespace(dry_run=dry_run)
    result = start_run(start_args)

    if result == 0:
        print("Full restart completed.")
    return result
