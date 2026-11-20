"""Confirm receipt of the current operation's portable handoff."""
import json
import sys

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events.factory import event_log_from_project
from zf.runtime.result_submit import credential_from_environment
from zf.runtime.task_handoff_receipt import accept_task_handoff


def register(subparsers):
    parser = subparsers.add_parser("handoff-accept", help="Acknowledge a completely read current handoff, without completing the task")
    parser.add_argument("--operation", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--state-dir", default=None)
    parser.set_defaults(func=run)
    evidence = subparsers.add_parser("handoff-evidence", help="Read causally bound predecessor tool observations without accepting them")
    evidence.add_argument("--operation", required=True)
    evidence.add_argument("--source-operation", default="")
    evidence.add_argument("--event", default="")
    evidence.add_argument("--page-start", type=int, default=0)
    evidence.add_argument("--digest", default="")
    evidence.add_argument("--state-dir", default=None)
    evidence.set_defaults(func=run_evidence)


def run_evidence(args):
    from zf.core.events import EventWriter
    from zf.runtime.result_submit import SemanticResultSubmitService
    from zf.runtime.work_record import authorize_work_record
    from zf.runtime.task_execution_evidence import read_execution_evidence

    try:
        context = resolve_project_context(explicit_state_dir=args.state_dir)
        log = event_log_from_project(context.state_dir, config=context.config)
        role, credential = credential_from_environment()
        service = SemanticResultSubmitService(state_dir=context.state_dir, event_log=log, event_writer=EventWriter(log))
        binding, manifest, _ = authorize_work_record(service, args.operation, role, credential)
        ref, body = read_execution_evidence(context.state_dir, manifest, binding, args.source_operation, args.event)
        content = json.dumps(body, ensure_ascii=False)
        if (args.page_start and not args.digest) or (args.digest and args.digest != ref['sha256']):
            raise ValueError('evidence continuation requires the same digest')
        if not 0 <= args.page_start < len(content):
            raise ValueError('invalid evidence page offset')
        end = min(len(content), args.page_start + 1024)
        result = {'content': content[args.page_start:end], 'sha256': ref['sha256'], 'ref': ref,
            'page_start': args.page_start, 'page_end': end, 'page_total': len(content),
            'next_page_start': end if end < len(content) else None, 'authorizes_execution': False}
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


def run(args):
    try:
        context = resolve_project_context(explicit_state_dir=args.state_dir)
        log = event_log_from_project(context.state_dir, config=context.config)
        role, credential = credential_from_environment()
        result = accept_task_handoff(state_dir=context.state_dir, event_log=log,
            operation_id=args.operation, role=role, credential=credential, digest=args.digest)
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
