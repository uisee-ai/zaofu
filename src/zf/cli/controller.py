"""zf controller — explicit observation delivery, with no standalone auth dependency."""

import json
import time
from pathlib import Path

from zf.core.config.project_context import load_project_env, resolve_project_context


def register(subparsers):
    parser = subparsers.add_parser('controller', help='Deliver Controller observation records')
    parser.add_argument('action', choices=('sync', 'status', 'codex-usage', 'installation-key', 'enroll', 'accept-credentials',
                                         'bootstrap', 'takeover', 'drain', 'resume', 'unenroll', 'serve', 'usage-review'))
    parser.add_argument('--workspace-config', type=Path, help='Deployment-level zf.yaml with kind WorkspaceConfig')
    parser.add_argument('--code-file', type=Path, help='Owner-only file containing the installation enrollment code')
    parser.add_argument('--credentials-file', type=Path, help='Owner-only file containing Controller encrypted credential delivery')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--host', default='127.0.0.1', help='Managed serve bind address behind the qualified Gateway')
    parser.add_argument('--port', type=int, default=8002)
    parser.add_argument('--ssl-keyfile')
    parser.add_argument('--ssl-certfile')
    parser.add_argument('--session-id', default='', help='Exact native Codex session for read-only inspection')
    parser.add_argument('--execution-id', default='', help='Exact settled execution whose usage completeness was overstated')
    parser.add_argument('--reason', default='')
    parser.add_argument('--evidence-ref', default='', help='Review evidence reference ending in #sha256=<digest>')
    parser.add_argument('--limit', type=int, default=5, help='Maximum native sessions to inspect (1..50)')
    parser.set_defaults(func=run)


def run(args):
    if getattr(args, 'workspace_config', None) is not None:
        from zf.cli.controller_workspace import run_workspace

        return run_workspace(args)
    if args.action in {'installation-key', 'enroll', 'accept-credentials', 'bootstrap', 'takeover', 'drain', 'resume', 'unenroll', 'serve'}:
        print('This action requires --workspace-config pointing to a deployment zf.yaml')
        return 2
    if getattr(args, 'code_file', None) or getattr(args, 'credentials_file', None):
        print('Credential files require an explicit enrollment or credential update action')
        return 2
    context = resolve_project_context(require_config=True)
    if args.action == 'codex-usage':
        from zf.integrations.controller.local_codex import local_codex_usage

        if args.watch or not 1 <= args.limit <= 50:
            print('codex-usage is a read-only snapshot; limit must be 1..50')
            return 2
        print(json.dumps(local_codex_usage(context.project_root, context.state_dir,
            limit=args.limit, session_id=args.session_id), ensure_ascii=False, indent=2))
        return 0
    if args.action == 'usage-review':
        from zf.integrations.controller.binding import ManagedDenied, project_binding
        from zf.integrations.controller.usage_review import mark_partial
        binding = project_binding(context.project_root)
        if binding is None:
            raise ManagedDenied('usage review requires a managed project')
        print(json.dumps(mark_partial(binding[2], args.execution_id, reason=args.reason, evidence_ref=args.evidence_ref)))
        return 0
    cloud = context.config.observability.controller
    if not cloud.enabled:
        print('Controller observation is disabled; standalone behavior is unchanged')
        return 2
    from zf.integrations.controller.outbox import Outbox
    if args.action == 'status':
        print(json.dumps(Outbox(context.state_dir, cloud).status()))
        return 0
    load_project_env(context.project_root)
    while True:
        try:
            from zf.integrations.controller.client import synchronize
            print(json.dumps(synchronize(context)), flush=True)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            # HTTP and schema exceptions may embed credentials or source values.
            print(json.dumps({'status': 'delivery_failed', 'error_type': type(exc).__name__,
                              'detail': 'Records retained; check configuration and Controller availability'}), flush=True)
            if not args.watch:
                return 1
        if not args.watch:
            return 0
        try:
            time.sleep(cloud.interval_seconds)
        except KeyboardInterrupt:
            return 0
