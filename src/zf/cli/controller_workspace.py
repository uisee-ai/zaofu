"""Explicit installation CLI. Errors never include HTTP bodies or machine credentials."""

import json
import time

from zf.core.config.controller_workspace import load_controller_workspace
from zf.integrations.controller.installation import Installation
from zf.integrations.controller.workspace import synchronize_workspace, workspace_status


def run_workspace(args) -> int:
    if (args.action == 'codex-usage' or (args.watch and args.action != 'sync')
            or bool(args.code_file) != (args.action == 'enroll')
            or bool(args.credentials_file) != (args.action == 'accept-credentials')
            or args.session_id):
        print('Invalid Workspace Connector action/options')
        return 2
    try:
        config = load_controller_workspace(args.workspace_config)
        installation = Installation(config)
        if args.action == 'installation-key':
            print(json.dumps(installation.public(), indent=2))
            return 0
        if args.action == 'enroll':
            print(json.dumps(installation.enroll(args.code_file)))
            return 0
        if args.action == 'accept-credentials':
            print(json.dumps(installation.accept_credentials(args.credentials_file)))
            return 0
        if args.action == 'status':
            print(json.dumps(workspace_status(config)))
            return 0
    except Exception as exc:
        print(json.dumps({'status': 'configuration_or_identity_error', 'error_type': type(exc).__name__,
            'detail': 'Check workspace zf.yaml, private file permissions and installation binding; credentials are not printed'}))
        return 2
    while True:
        try:
            # Re-read local consent on each iteration; never continue with a removed Project scope.
            config = load_controller_workspace(args.workspace_config)
            result = synchronize_workspace(config)
            print(json.dumps(result), flush=True)
            if not args.watch:
                return 0 if result['status'] == 'synchronized' else 1
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(json.dumps({'status': 'delivery_failed', 'error_type': type(exc).__name__,
                'detail': 'Sources and unconfirmed receipts retained; no change to management mode'}), flush=True)
            if not args.watch:
                return 1
        try:
            time.sleep(config.interval_seconds)
        except KeyboardInterrupt:
            return 0
