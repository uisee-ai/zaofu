"""Disposable PostgreSQL/Keycloak/Controller qualification; --codex is an explicit real-provider tier.

Run with both packages' optional dependencies and --controller-root.
Only fixture users are seeded; this is not an email, browser login or managed execution qualification.
"""

import argparse
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def private(path, value):
    path.write_text(value)
    path.chmod(0o600)


def realm(iam_secret):
    return {'realm': 'workspace-check', 'enabled': True, 'sslRequired': 'none',
        'roles': {'realm': [{'name': 'zf-connector'}]},
        'clientScopes': [{'name': 'zf-connector-audience', 'protocol': 'openid-connect',
            'protocolMappers': [{'name': 'control-audience', 'protocol': 'openid-connect',
                'protocolMapper': 'oidc-audience-mapper', 'config': {'included.client.audience': 'zf-control-api',
                    'access.token.claim': 'true', 'id.token.claim': 'false'}}]}],
        'scopeMappings': [{'clientScope': 'zf-connector-audience', 'roles': ['zf-connector']}],
        'clients': [
            {'clientId': 'zf-control-api', 'enabled': True, 'bearerOnly': True, 'protocol': 'openid-connect'},
            {'clientId': 'fixture-iam', 'enabled': True, 'publicClient': False, 'secret': iam_secret,
             'serviceAccountsEnabled': True, 'standardFlowEnabled': False, 'fullScopeAllowed': True,
             'defaultClientScopes': ['basic', 'roles', 'kc-admin-api']}],
        'users': [{'username': 'service-account-fixture-iam', 'enabled': True,
            'serviceAccountClientId': 'fixture-iam', 'clientRoles': {'realm-management': [
                'manage-clients', 'view-clients', 'manage-users', 'view-users', 'query-users', 'view-realm']}}]}


def configure_identity(client, base, admin_password, iam_secret):
    # Use the same Admin API bootstrap as Controller's qualified machine checker.
    # Creating the realm first preserves Keycloak's built-in basic/roles scopes.
    token = client.post(base + '/realms/master/protocol/openid-connect/token', data={
        'grant_type': 'password', 'client_id': 'admin-cli',
        'username': 'fixture-admin', 'password': admin_password}, timeout=15)
    token.raise_for_status()
    headers = {'Authorization': 'Bearer ' + token.json()['access_token']}

    def call(method, path, **kwargs):
        # Cold Realm creation can outlast the two-second readiness probe timeout.
        result = client.request(method, base + '/admin/realms' + path, headers=headers, timeout=15, **kwargs)
        result.raise_for_status()
        return result.json() if result.content else None

    fixture = realm(iam_secret)
    call('POST', '', json={name: fixture[name] for name in ('realm', 'enabled', 'sslRequired')})
    prefix = '/workspace-check'
    call('POST', prefix + '/roles', json={'name': 'zf-connector'})
    call('POST', prefix + '/client-scopes', json=fixture['clientScopes'][0])
    scopes = call('GET', prefix + '/client-scopes')
    scope = next(item for item in scopes if item['name'] == 'zf-connector-audience')
    role = call('GET', prefix + '/roles/zf-connector')
    call('POST', prefix + '/client-scopes/' + scope['id'] + '/scope-mappings/realm', json=[role])
    for value in fixture['clients']:
        call('POST', prefix + '/clients', json=value)
    iam = call('GET', prefix + '/clients', params={'clientId': 'fixture-iam'})[0]
    account = call('GET', prefix + '/clients/' + iam['id'] + '/service-account-user')
    management = call('GET', prefix + '/clients', params={'clientId': 'realm-management'})[0]
    roles = call('GET', prefix + '/clients/' + management['id'] + '/roles')
    allowed = fixture['users'][0]['clientRoles']['realm-management']
    call('POST', prefix + '/users/' + account['id'] + '/role-mappings/clients/' + management['id'],
         json=[value for value in roles if value['name'] in allowed])


def seed(app, settings):
    from zaofu_controller.access import Principal
    from zaofu_controller.control_schema import policies
    from zaofu_controller.database import auth_sessions, users, workspaces

    user, session, workspace = (str(uuid.uuid4()) for _ in range(3))
    now = int(time.time())
    with app.state.access.engine.begin() as db:
        db.execute(users.insert().values(id=user, issuer=settings.issuer, subject='fixture-owner',
                                          status='active', auth_version=1))
        db.execute(auth_sessions.insert().values(id=session, user_id=user, issuer=settings.issuer,
            subject='fixture-owner', sid='fixture-login', tokens=b'fixture-not-a-browser-token',
            roles=['zf-user'], acr='2', auth_time=now, checked_at=now, expires_at=now + 1800, revoked=False))
        db.execute(workspaces.insert().values(id=workspace, owner_id=user, status='pending',
            hosting='external', management='observed', is_default=False, generation=0, spec={'projects': 2}))
        db.execute(policies.insert().values(workspace_id=workspace, version=1, capabilities=['observe'],
            projects=[], limits={'projects': 2, 'management': 'observed'}, applied_version=0))
    return Principal(user, session, ('zf-user',), 1, '2', now), workspace


def run_cli(config, action, *extra):
    from zf.cli.main import main

    output = io.StringIO()
    with redirect_stdout(output):
        code = main(['controller', action, '--workspace-config', str(config), *extra])
    if code:
        print(json.dumps({'failed_action': action, 'result': json.loads(output.getvalue())}), flush=True)
        raise RuntimeError('Connector CLI failed: ' + action)
    return json.loads(output.getvalue())


def native_codex(context):
    from zf.core.config.schema import RoleConfig
    from zf.core.events import EventWriter
    from zf.core.events.factory import event_log_from_project
    from zf.runtime.backend_session_reader import CodexSessionReader
    from zf.runtime.provider_usage_reconciliation import build_disk_usage_event
    from zf.web.headless_agent import CodexHeadlessBackend

    backend = CodexHeadlessBackend()
    if not backend.available():
        raise RuntimeError('Codex CLI unavailable')
    # A new, read-only, bounded turn in the temporary project. No tool use is needed.
    result = backend.run_turn(prompt='Reply exactly WORKSPACE_USAGE_OK. Do not use tools or read files.',
        cwd=context.project_root, system_prompt='This is an isolated token accounting check. Reply briefly.',
        thread_id=str(uuid.uuid4()), provider_session_id='', on_session_id=lambda value: None,
        on_message=None, timeout_s=90, execution_deadline=time.monotonic() + 120,
        permission_profile='read_only')
    if not result.ok or not result.provider_session_id:
        raise RuntimeError('Codex did not complete the bounded read-only turn')
    root = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'sessions'
    paths = list(root.glob('*/*/*/rollout-*-' + result.provider_session_id + '.jsonl'))
    if len(paths) != 1:
        raise RuntimeError('exact native Codex receipt unavailable')
    reader = CodexSessionReader()
    if not reader._rollout_matches_project(paths[0], context.project_root):
        raise RuntimeError('native receipt project mismatch')
    report = reader.read_latest_usage(paths[0])
    if report is None or report.accounting_mode != 'subscription':
        raise RuntimeError('native receipt does not establish subscription accounting')
    log = event_log_from_project(context.state_dir, config=context.config)
    try:
        event = build_disk_usage_event(role=RoleConfig(name='qualification', backend='codex'), usage=report,
            config=context.config, usage_identity={'session_id': result.provider_session_id,
                'provider_session_id': result.provider_session_id}, capture_phase='qualification')
        EventWriter(log).append(event)
        EventWriter(log).emit('simulation.done', actor='qualification', payload={'purpose': 'workspace-connector'})
    finally:
        log.close()
    return {'native_session_id': result.provider_session_id, 'accounting_mode': report.accounting_mode,
            'model': report.model, 'tokens': report.raw, 'receipt_source': 'native_codex_disk',
            'billed_cost_usd': None}


def qualify(folder, root, settings, codex, managed=False):
    import uvicorn
    import yaml
    from sqlalchemy import select

    from zaofu_controller.app import create_app
    from zaofu_controller.control_models import EnrollmentInput, PolicyInput
    from zaofu_controller.credentials import RotationInput
    from zaofu_controller.database import engine_for, migrate
    from zaofu_controller.telemetry_schema import usage
    from zf.core.config.loader import load_config
    from zf.core.config.project_context import ProjectContext
    from zf.core.events import EventWriter
    from zf.core.events.factory import event_log_from_project
    from zf.core.workspace.registry import WorkspaceRegistry
    from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter

    engine = engine_for(settings.database_url)
    migrate(engine)
    engine.dispose()
    app = create_app(settings)
    owner, workspace = seed(app, settings)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=int(settings.public_origin.rsplit(':', 1)[1]),
                                           log_level='error', access_log=False, timeout_graceful_shutdown=3))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    config = folder / 'zf.yaml'
    raw = {'version': '1.0', 'kind': 'WorkspaceConfig', 'workspace': {
        'name': 'qualification', 'state_dir': 'connector-state', 'controller': {
            'management': 'observed', 'endpoint': settings.public_origin, 'issuer': settings.issuer,
            'workspace_id': workspace, 'development': True, 'projects': [], 'mode': 'self_managed_cli'}}}
    config.write_text(yaml.safe_dump(raw))
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.1)
        if not server.started:
            raise RuntimeError('Controller startup failed')
        print('Controller ready; qualifying installation CLI', flush=True)
        key = run_cli(config, 'installation-key')
        offer = app.state.enrollments.create(owner, workspace, EnrollmentInput(
            request_id='qualification-install', public_key=key['public_key'], projects=[]))
        code = folder / 'enrollment-code'
        private(code, offer['code'])
        first = run_cli(config, 'enroll', '--code-file', str(code))
        assert run_cli(config, 'enroll', '--code-file', str(code)) == first
        empty = run_cli(config, 'sync')
        assert empty['projects'] == []
        overview = app.state.observations.overview(owner, workspace_id=workspace)
        assert overview['online_deployments'] == 1 and overview['objects']['project'] == 0
        print('Empty-workspace enrollment and heartbeat passed', flush=True)

        contexts = []
        for number in range(2):
            project = folder / f'project-{number}'
            project.mkdir()
            path = project / 'zf.yaml'
            path.write_text(yaml.safe_dump({'version': '1.0', 'project': {
                'name': f'qualification-{number}', 'state_dir': 'custom-state'}}))
            context = ProjectContext(project, path, load_config(path), project / 'custom-state')
            entry = WorkspaceRegistry(workspace='qualification').upsert_context(context)
            contexts.append(context)
            raw['workspace']['controller']['projects'].append(entry.project_id)
        config.write_text(yaml.safe_dump(raw))
        policy = app.state.commands.policy(owner, workspace)
        app.state.commands.update_policy(owner, workspace, PolicyInput(request_id='qualification-projects',
            version=policy['version'], reason='isolated qualification scope', management='observed',
            projects=raw['workspace']['controller']['projects'], capabilities=['observe']))
        run_cli(config, 'sync')  # Establish opening boundaries before generating receipts.
        print('Project scopes applied; qualifying usage sources', flush=True)
        native = native_codex(contexts[0]) if codex and not managed else None
        for number, context in enumerate(contexts):
            if codex and not managed and number == 0:
                continue  # Never combine the native cumulative receipt with a synthetic turn receipt.
            log = event_log_from_project(context.state_dir, config=context.config)
            try:
                stream = AgentSessionStreamEmitter(writer=EventWriter(log), identity=AgentSessionIdentity(
                    run_id=f'fixture-turn-{number}', thread_id=f'fixture-session-{number}',
                    source='channel', actor='fixture', backend='codex', provider='openai'))
                stream.start()
                stream.complete(usage={'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 30})
                EventWriter(log).emit('simulation.done', actor='qualification', payload={'purpose': 'workspace-connector'})
            finally:
                log.close()
        run_cli(config, 'sync')
        before = app.state.observations.overview(owner, workspace_id=workspace)
        run_cli(config, 'sync')
        after = app.state.observations.overview(owner, workspace_id=workspace)
        assert before['usage'] == after['usage']
        assert after['objects']['project'] == 2 and after['objects']['session'] >= 1
        assert after['objects']['operation'] >= 1 and after['quarantined_records'] == 0
        if native:
            with app.state.access.engine.connect() as db:
                rows = list(db.execute(select(usage.c.payload)).scalars())
            row = next(value for value in rows if value.get('provider_session_id') == native['native_session_id'])
            assert row['accounting_mode'] == 'subscription' and row.get('baseline_only') is not True
            assert row['tokens']['input_tokens'] + row['tokens']['cache_read_tokens'] == native['tokens']['input_tokens']
            assert row['tokens']['output_tokens'] == native['tokens']['output_tokens']
            assert row.get('provider_reported_cost_usd') is None
        rotated = app.state.credentials.rotate(owner, workspace, RotationInput(
            request_id='qualification-rotation', public_key=key['public_key'], reason='isolated credential rotation'))
        delivery = folder / 'credentials.json'
        private(delivery, json.dumps(rotated))
        updated = run_cli(config, 'accept-credentials', '--credentials-file', str(delivery))
        assert updated['deployment_id'] == first['deployment_id']
        run_cli(config, 'sync')
        assert app.state.observations.overview(owner, workspace_id=workspace)['usage'] == after['usage']
        managed_result = None
        if managed:
            from check_controller_managed import qualify_managed
            managed_result = qualify_managed(app, owner, workspace, config, raw, codex)
        if managed:
            run_cli(config, 'drain')
            run_cli(config, 'sync')
        app.state.enrollments.revoke(owner, workspace)
        from zf.cli.main import main as cli
        with redirect_stdout(io.StringIO()):
            assert cli(['controller', 'sync', '--workspace-config', str(config)]) == 1
        if managed:
            for _ in range(20):
                drained = app.state.credentials.drain_revocations()
                assert drained['status'] in {'completed', 'idle'}
                if drained['status'] == 'idle':
                    break
            assert drained['status'] == 'idle'
            from zaofu_controller.control_schema import identity_revocations
            from zaofu_controller.operations_schema import leases
            from zaofu_controller.budget_schema import reservations
            with app.state.access.engine.connect() as c:
                print({'release_preflight': {'identity': drained,
                    'lease_states': list(c.execute(select(leases.c.status)).scalars()),
                    'budget_states': list(c.execute(select(reservations.c.status)).scalars()),
                    'revocation_states': list(c.execute(select(identity_revocations.c.status)).scalars())}}, flush=True)
            released = run_cli(config, 'unenroll')
            assert released['management'] == 'standalone'
            assert all(context.config_path.exists() for context in contexts)
            managed_result['verified'].append('drain, IdP revocation and signed local release')
        return {'result': 'passed', 'controller_head': subprocess.check_output(
                    ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
                'controller_worktree_dirty': bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).strip()),
                'real_services': ['PostgreSQL 17', 'Keycloak 26.8.2', 'Controller HTTP'],
                'verified': ['empty-workspace heartbeat', 'enrollment retry', 'two projects',
                    'native lifecycle fixtures', 'usage replay deduplication', 'credential rotation', 'revocation'],
                'objects': after['objects'], 'native_codex': native,
                'human_login': 'fixture owner; email/browser flow not qualified',
                'management': managed_result or 'observed; execution limits not qualified'}
    finally:
        server.should_exit = True
        thread.join(10)
        app.state.access.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller-root', type=Path, required=True)
    parser.add_argument('--codex', action='store_true', help='Run ONE real local Codex subscription turn')
    parser.add_argument('--managed', action='store_true', help='Exercise runtime grant and settlement boundaries')
    args = parser.parse_args()
    root = args.controller_root.resolve()
    sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'), str(root / 'src')]
    import httpx2
    import yaml
    from cryptography.fernet import Fernet
    from zaofu_controller.settings import Settings
    from zaofu_controller.app import create_app  # noqa: F401 -- fail before starting containers if dependencies are absent

    project = 'zf-workspace-check-' + uuid.uuid4().hex[:10]
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    with tempfile.TemporaryDirectory(prefix=f'zf-controller-workspace-{timestamp}-', dir='/tmp') as temporary:
        folder = Path(temporary)
        os.environ['ZF_WORKSPACE_HOME'] = str(folder / 'registry')
        for name in ('ZF_PROJECT_ROOT', 'ZF_STATE_DIR', 'ZFC_RECOVERY_HOLD'):
            os.environ.pop(name, None)
        pg_port, kc_port, api_port = port(), port(), port()
        password, iam_secret, admin_password = (secrets.token_urlsafe(32) for _ in range(3))
        private(folder / 'keycloak.env', 'KC_BOOTSTRAP_ADMIN_USERNAME=fixture-admin\nKC_BOOTSTRAP_ADMIN_PASSWORD='
                + admin_password + '\n')
        private(folder / 'postgres.env', 'POSTGRES_PASSWORD=' + password + '\nPOSTGRES_DB=qualification\n')
        compose = folder / 'compose.yaml'
        compose.write_text(yaml.safe_dump({'services': {
            'postgres': {'image': 'postgres:17', 'env_file': [str(folder / 'postgres.env')],
                'network_mode': 'host', 'command': ['postgres', '-c', f'port={pg_port}', '-c', 'listen_addresses=127.0.0.1'], 'mem_limit': '256m',
                'tmpfs': ['/var/lib/postgresql/data']},
            'keycloak': {'image': 'quay.io/keycloak/keycloak:26.8.2',
                'network_mode': 'host', 'mem_limit': '768m',
                'environment': {'JAVA_OPTS_KC_HEAP': '-Xms64m -Xmx256m'},
                'env_file': [str(folder / 'keycloak.env')],
                'command': ['start-dev', '--health-enabled=false', '--http-host=127.0.0.1', f'--http-port={kc_port}']}}}))
        command = ['docker', 'compose', '-p', project, '-f', str(compose)]
        try:
            subprocess.run([*command, 'up', '-d'], check=True, capture_output=True, timeout=90)
            issuer = f'http://127.0.0.1:{kc_port}/realms/workspace-check'
            with httpx2.Client(trust_env=False, timeout=2) as client:
                for _ in range(90):
                    try:
                        if client.get(f'http://127.0.0.1:{kc_port}/realms/master/.well-known/openid-configuration').status_code == 200:
                            break
                    except httpx2.HTTPError:
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError('isolated Keycloak startup timeout')
                configure_identity(client, f'http://127.0.0.1:{kc_port}', admin_password, iam_secret)
            print('Isolated Compose services ready', flush=True)
            settings = Settings(database_url=f'postgresql+psycopg://postgres:{password}@127.0.0.1:{pg_port}/qualification',
                session_key=Fernet.generate_key().decode(), client_secret='fixture-no-human-login', issuer=issuer,
                public_origin=f'http://127.0.0.1:{api_port}', development=True,
                iam_client_id='fixture-iam', iam_client_secret=iam_secret, managed_runtime_versions=('0.0.7',))
            print(json.dumps(qualify(folder, root, settings, args.codex, args.managed), ensure_ascii=False))
        finally:
            subprocess.run([*command, 'down', '--volumes', '--remove-orphans'], capture_output=True, timeout=60, check=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'result': 'failed', 'error_type': type(error).__name__,
                          'where': [f'{Path(frame.filename).name}:{frame.lineno}' for frame in traceback.extract_tb(error.__traceback__)],
                          'detail': 'Isolated qualification failed; secret-bearing exception bodies suppressed'}))
        raise SystemExit(1) from None
