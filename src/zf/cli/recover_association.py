"""Preview/admit an agent's scoped recovery association artifact."""

import json
from pathlib import Path

from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.runtime.recovery_association import admit_association


def register_recover_association(subparsers):
    parser = subparsers.add_parser("associate", help="Admit evidence-bound recovery case relationships")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--apply", action="store_true")
    parser.set_defaults(func=run)


def run(args):
    try:
        ctx = resolve_project_context(explicit_state_dir=args.state_dir, load_config_with_explicit=True)
        proposal = json.loads(args.file.read_text())
        if not isinstance(proposal, dict):
            raise ValueError("association proposal must be an object")
        result = admit_association(state_dir=ctx.state_dir,
            writer=EventWriter(event_log_from_project(ctx.state_dir, config=ctx.config)),
            config=ctx.config, actor=args.actor, proposal=proposal, apply=args.apply)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
