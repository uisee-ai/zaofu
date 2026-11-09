"""zf controller — explicit observation delivery, with no standalone auth dependency."""

import json
import time

from zf.core.config.project_context import load_project_env, resolve_project_context


def register(subparsers):
    parser = subparsers.add_parser('controller', help='Deliver Controller observation records')
    parser.add_argument('action', choices=('sync', 'status'))
    parser.add_argument('--watch', action='store_true')
    parser.set_defaults(func=run)


def run(args):
    context = resolve_project_context(require_config=True)
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
