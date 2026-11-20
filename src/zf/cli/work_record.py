"""Authenticated nonterminal work record publish/read using submit credentials."""
import argparse
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
        child = modes.add_parser(
            mode,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=(
                "Publish an optional nonterminal checkpoint without completing a task or replacing acceptance."
                if mode == 'publish' else None
            ),
            epilog=(
                'Files and attachments must already be authorized regular files in the pinned workdir.\n'
                '--file accepts the JSON below, not a native CommandExecution object or raw log.\n'
                'Use --stdin to publish JSON without writing a checkpoint file.\n'
                'The four fields below are required; report_draft is optional (at most 8192 UTF-8 bytes).\n'
                'evidence_files are relative to the workdir, not the JSON file; no absolute paths or .. .\n'
                'Example (tests.log must exist and be authorized; readers may use an empty evidence_files list):\n'
                '{\n'
                '  "completed_actions": ["Ran and recorded checks; acceptance remains pending"],\n'
                '  "pending": ["Check remaining acceptance criteria"],\n'
                '  "next_step": "Bind verified evidence refs to the authorized result",\n'
                '  "evidence_files": ["tests.log"]\n'
                '}\n'
                'Method: zf-harness-evidence-collection/references/work-record.md.\n'
                'Existing qualified evidence refs may satisfy the result contract without an extra WRC.'
                if mode == 'publish' else None
            ),
        )
        child.add_argument('--operation', required=True)
        child.add_argument('--state-dir', default=None)
        if mode == 'publish':
            source = child.add_mutually_exclusive_group(required=True)
            source.add_argument('--file')
            source.add_argument('--stdin', action='store_true', help='Read bounded checkpoint JSON without a workspace write')
            child.add_argument('--request-id', required=True)
        else:
            child.add_argument('--predecessor', action='store_true', help='Read the predecessor record pinned in this operation handoff')
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
            body = None
            if args.stdin:
                raw = sys.stdin.read(16385)
                if len(raw.encode('utf-8')) > 16384:
                    raise ValueError('work record payload exceeds byte budget')
                body = json.loads(raw)
            result = publish_work_record(state_dir=ctx.state_dir, event_log=log, operation_id=args.operation,
                role=role, credential=credential, record_file=Path(args.file) if args.file else None,
                record_body=body, request_id=args.request_id)
        else:
            service = SemanticResultSubmitService(state_dir=ctx.state_dir, event_log=log, event_writer=EventWriter(log))
            binding, manifest, _ = authorize_work_record(service, args.operation, role, credential)
            if args.predecessor:
                from zf.runtime.task_handoff import predecessor_work_record
                descriptor, body = predecessor_work_record(ctx.state_dir, manifest, binding)
            else:
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
