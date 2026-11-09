"""Authenticated nonterminal work record publish/read using submit credentials."""
import json
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.runtime.result_submit import SemanticResultSubmitService, credential_from_environment
from zf.runtime.work_record import publish_work_record, current_work_record, authorize_work_record
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def register(sub):
    parser = sub.add_parser('work-record', help='Publish or read current worker progress without completing a task')
    modes = parser.add_subparsers(dest='work_record_command', required=True)
    for mode in ('publish','show'):
        child = modes.add_parser(mode)
        child.add_argument('--operation', required=True)
        child.add_argument('--state-dir', default=None)
        if mode == 'publish':
            child.add_argument('--file', required=True)
            child.add_argument('--request-id', required=True)
        else:
            child.add_argument('--evidence', type=int, help='Read an immutable evidence envelope by zero-based index')
            child.add_argument('--page-start', type=int, default=0)
            child.add_argument('--digest', help='Pin continuation pages to the first returned record digest')
        child.set_defaults(func=run)


def run(args):
    try:
        ctx = resolve_project_context(explicit_state_dir=args.state_dir)
        log = event_log_from_project(ctx.state_dir, config=ctx.config)
        role, credential = credential_from_environment()
        if args.work_record_command == 'publish':
            result = publish_work_record(state_dir=ctx.state_dir, event_log=log, operation_id=args.operation,
                role=role, credential=credential, record_file=Path(args.file), request_id=args.request_id)
        else:
            service = SemanticResultSubmitService(state_dir=ctx.state_dir, event_log=log, event_writer=EventWriter(log))
            _, manifest, _ = authorize_work_record(service, args.operation, role, credential)
            record = current_work_record(ctx.state_dir, log.read_all(), manifest, role)
            if record['status'] != 'bound':
                raise ValueError('current work record unavailable')
            descriptor = record['work_record_ref']
            body = hydrate_sidecar_ref(ctx.state_dir, descriptor).payload
            if args.evidence is not None:
                if not 0 <= args.evidence < len(body['evidence_refs']):
                    raise ValueError('invalid evidence index')
                descriptor = body['evidence_refs'][args.evidence]
                evidence = hydrate_sidecar_ref(ctx.state_dir, descriptor).payload
                import base64
                content = base64.b64decode(evidence['body_base64'], validate=True).decode('utf-8')
            else:
                content = json.dumps(body, ensure_ascii=False)
            if args.page_start and not args.digest or args.digest and args.digest != descriptor['sha256']:
                raise ValueError('work record continuation requires the same digest')
            if not (0 <= args.page_start < len(content) or args.page_start == 0 and not content):
                raise ValueError('invalid work record page offset')
            end = min(len(content), args.page_start+1024)
            result = {'content':content[args.page_start:end], 'sha256':descriptor['sha256'],
                'page_start':args.page_start,'page_end':end,'page_total':len(content),
                'next_page_start':end if end < len(content) else None,'authorizes_execution':False}
    except (ConfigError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
