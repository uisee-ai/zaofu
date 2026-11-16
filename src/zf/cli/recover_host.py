"""Host supervision checks lifecycle eligibility without changing business state."""

import json

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.runtime.host_lifecycle import host_restart_eligibility


def register_recover_host(subparsers):
    parser = subparsers.add_parser("host-check", help="Read-only host restart eligibility (exit 1 means do not restart)")
    parser.add_argument("--state-dir", default=None)
    parser.set_defaults(func=run)
    unit = subparsers.add_parser("host-unit", help="Render or install an opt-in user systemd supervisor")
    unit.add_argument("--state-dir", default=None)
    unit.add_argument("--name", required=True)
    unit.add_argument("--install", action="store_true")
    unit.set_defaults(func=unit_run)


def run(args):
    try:
        ctx = resolve_project_context(explicit_state_dir=args.state_dir, load_config_with_explicit=True)
        log = event_log_from_project(ctx.state_dir, config=ctx.config)
        result = host_restart_eligibility(ctx.state_dir, log.read_all())
    except (ConfigError, OSError, ValueError) as exc:
        result = {"eligible": False, "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["eligible"] else 1


def unit_run(args):
    import os
    from pathlib import Path
    import sys
    from zf.runtime.host_supervision import install_user_unit, render_user_unit
    try:
        ctx = resolve_project_context(explicit_state_dir=args.state_dir, load_config_with_explicit=True)
        body = render_user_unit(project_root=ctx.project_root, state_dir=ctx.state_dir,
            python=Path(sys.executable), source_root=Path(__file__).resolve().parents[3],
            environment=dict(os.environ), unit_name=args.name)
        if not args.install:
            print(body, end='')
        else:
            path = install_user_unit(args.name, body)
            print(json.dumps({'unit_file': str(path), 'enabled': True, 'started': False,
                'next': 'start the authorized workflow, then systemctl --user start ' + args.name,
                'boot_requirement': 'user systemd must start at boot (check loginctl show-user "$USER" -p Linger)'}))
    except (ConfigError, OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'reason': str(exc)}))
        return 1
    return 0
