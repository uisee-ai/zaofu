"""Managed entrypoints, grant failures and receipt recovery against a strict wire peer."""

import json
import time
from types import SimpleNamespace

import httpx2
import pytest
import yaml
from tests.test_controller_workspace import workspace as workspace, enrolled, add_project, configure_projects

from zf.core.config.controller_workspace import load_controller_workspace
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.integrations.controller.binding import bind, managed_config, ManagedDenied
from zf.integrations.controller.execution import Execution, managed_turn, reconcile_executions
from zf.integrations.controller.installation import Installation
from zf.integrations.controller.ledger import Ledger
from zf.integrations.controller.workspace import synchronize_workspace, project_contexts
from zf.integrations.controller.workspace_transport import WorkspaceTransport
from zf.runtime.provider_usage import ProviderCallNotStarted


@pytest.fixture
def managed(workspace, tmp_path, monkeypatch):
    config, peer, code = workspace
    enrolled(workspace)
    project_id, context = add_project(config, tmp_path / 'project')
    config = configure_projects(config, peer, [project_id])
    raw = yaml.safe_load(config.path.read_text())
    raw['workspace']['controller'].update(management='managed', gateway_origin='https://workspace.example.test')
    config.path.write_text(yaml.safe_dump(raw))
    config = load_controller_workspace(config.path)
    peer.policy.update(capabilities=['observe', 'gateway', 'commands', 'budget'], limits={'management': 'managed'})
    peer.policy.update(applied_version=2, applied_deployment=peer.deployment_id)
    old_handle = peer.handle
    peer.leases, peer.budgets, peer.settlements = [], [], []
    peer.deny_budget = False
    peer.access_allowed, peer.command_receipts, peer.commands = True, [], []

    def handle(request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == '/__zf/jwks.json':
            return httpx2.Response(200, json=peer.jwks)
        if path == '/internal/v1/me/exports':
            return httpx2.Response(200, json=[])
        if path == '/internal/v1/me/activity':
            return httpx2.Response(200, json={'status': 'recorded'})
        if peer.revoked:
            return httpx2.Response(403, json={'error': 'revoked'})
        if path == '/internal/v1/me/execution-readiness':
            return httpx2.Response(200, json={**body, 'protocol': 'zfc.control.v1', 'executable': not peer.deny_budget,
                'deployment_id': peer.deployment_id, 'generation': peer.generation})
        if path == '/internal/v1/me/execution-leases':
            assert set(body) == {'request_id', 'execution_id', 'kind', 'project_id', 'policy_version', 'seconds'}
            result = {**body, 'id': 'lease-' + str(len(peer.leases)), 'executable': True,
                      'expires_at': int(time.time()) + body['seconds']}
            peer.leases.append(result)
            return httpx2.Response(200, json=result)
        if path.startswith('/internal/v1/me/execution-leases/'):
            assert 'protocol' not in body
            lease = next(row for row in peer.leases if path.endswith('/' + row['id']))
            return httpx2.Response(200, json={**lease, 'status': 'released' if body['action'] == 'release' else 'held',
                'expires_at': int(time.time()) + 30})
        if path == '/internal/v1/me/budgets/reservations':
            if peer.deny_budget:
                return httpx2.Response(409, json={'error': 'budget_exhausted'})
            result = {'input': body, 'id': 'budget-' + str(len(peer.budgets)), 'executable': True,
                      'expires_at': int(time.time()) + body['expires_in']}
            peer.budgets.append(result)
            return httpx2.Response(200, json=result)
        if path.endswith('/settle'):
            records = [r['payload'] for r in peer.records.values() if r['payload'].get('record_id') == body['record_id']]
            assert len(records) == 1
            assert records[0]['tokens'] is not None
            peer.settlements.append(records[0])
            return httpx2.Response(200, json={'status': 'settled'})
        if path == '/internal/v1/me/access/check':
            if not peer.access_allowed:
                return httpx2.Response(403, json={'error': 'session_revoked'})
            return httpx2.Response(200, json={**body, 'authorized': True,
                'workspace_id': config.workspace_id, 'deployment_id': peer.deployment_id, 'generation': peer.generation})
        if path == '/internal/v1/me/control':
            return httpx2.Response(200, json={'protocol': 'zfc.control.v1', 'policy': peer.policy,
                'deployment_id': peer.deployment_id, 'generation': peer.generation, 'commands': peer.commands})
        if path.endswith('/authorize'):
            return httpx2.Response(200, json={'protocol': 'zfc.control.v1', 'authorized': True, 'command': peer.commands[0]})
        if '/control/commands/' in path:
            peer.command_receipts.append(body)
            return httpx2.Response(200, json={'status': body['status']})
        return old_handle(request)

    monkeypatch.setattr(peer, 'handle', handle)
    # The original fixture captured a bound handler in each Client construction; delegate dynamically.
    original = httpx2.Client.__init__
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self._transport = httpx2.MockTransport(handle)
    monkeypatch.setattr(httpx2.Client, '__init__', initialize)
    bind(config)
    synchronize_workspace(config)
    credentials = Installation(config).credentials()
    ctx = project_contexts(config, credentials, [project_id])[0]
    return config, peer, ctx, credentials


def test_managed_binding_cannot_be_disabled_by_project_or_environment(managed, monkeypatch):
    config, peer, context, credentials = managed
    assert managed_config().workspace_id == config.workspace_id
    raw = yaml.safe_load(config.path.read_text())
    raw['workspace']['controller']['management'] = 'observed'
    config.path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ManagedDenied):
        managed_config()


def test_managed_lost_credentials_denies_backend_before_provider_launch(managed):
    config, _, context, _ = managed
    (Installation(config).path / 'binding.json').unlink()
    calls = []
    class Backend:
        backend_id = 'fixture'
        @managed_turn
        def run_turn(self, **kwargs):
            calls.append(kwargs)
    with pytest.raises(FileNotFoundError):
        Backend().run_turn(cwd=context.project_root, thread_id='thread', provider_session_id='', timeout_s=30)
    assert not calls


def test_backend_reuse_is_admitted_each_turn_and_receipts_are_not_duplicated(managed):
    config, peer, context, credentials = managed
    calls = []
    class Backend:
        backend_id = 'fixture'
        @managed_turn
        def run_turn(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status='completed', provider_session_id='native-fixture',
                                   usage={'input_tokens': 12, 'output_tokens': 3})
    backend = Backend()
    for _ in range(2):
        result = backend.run_turn(cwd=context.project_root, thread_id='thread', provider_session_id='native-fixture', timeout_s=30)
        assert result.usage['_controller_accounted']
    assert len(peer.leases) == len(peer.budgets) == len(calls) == 2
    assert len({row['input']['request_id'] for row in peer.budgets}) == 2
    synchronize_workspace(config)
    with WorkspaceTransport(config, credentials) as transport:
        assert reconcile_executions(config, credentials, transport, [context]) == 0
    assert len(peer.settlements) == 2
    assert sum(row['tokens']['input_tokens'] + row['tokens']['output_tokens'] for row in peer.settlements) == 30
    peer.deny_budget = True
    with pytest.raises(ProviderCallNotStarted) as denied:
        backend.run_turn(cwd=context.project_root, thread_id='thread', provider_session_id='native-fixture', timeout_s=30)
    assert isinstance(denied.value.__cause__, httpx2.HTTPStatusError)
    assert len(calls) == 2


def test_managed_turn_uses_public_session_identity_for_receipts_and_remote_fences(managed):
    config, peer, context, _ = managed
    project_id = context.config.observability.controller.project_id
    writer = EventWriter(event_log_from_project(context.state_dir, config=context.config))
    writer.emit('agent.session.run.started', actor='web', payload={
        'thread_id': 'visible-thread', 'run_id': 'turn-1', 'source': 'kanban'})
    calls = []

    class Backend:
        backend_id = 'fixture'

        @managed_turn
        def run_turn(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(status='completed', provider_session_id='native-thread',
                                   usage={'input_tokens': 12, 'output_tokens': 3})

    backend = Backend()
    args = dict(cwd=context.project_root, thread_id='internal-thread-binding',
                run_thread_id='visible-thread', provider_session_id='', timeout_s=30)
    result = backend.run_turn(**args)
    writer.emit('agent.session.run.completed', actor='web', payload={
        'thread_id': 'visible-thread', 'run_id': 'turn-1', 'source': 'kanban',
        'provider_session_id': 'native-thread', 'usage': result.usage})
    synchronize_workspace(config)
    rows = [r['payload'] for r in peer.records.values()]
    assert {r['session_id'] for r in rows if r['kind'] == 'session'} == {'visible-thread'}
    assert {r['session_id'] for r in rows if r['kind'] == 'usage'} == {'visible-thread'}
    assert calls[0]['thread_id'] == 'internal-thread-binding'
    assert Ledger(config.state_dir).rows('execution')[0]['body']['native_session_id'] == 'native-thread'

    peer.commands = [{'id': 'stop-visible-thread', 'workspace_id': config.workspace_id,
        'deployment_id': peer.deployment_id, 'generation': 1, 'policy_version': 2,
        'expires_at': int(time.time()) + 60, 'user_id': 'owner', 'session_id': 'login',
        'payload': {'action': 'session.stop', 'project_id': project_id,
                    'session_id': 'visible-thread', 'operation_id': None}}]
    synchronize_workspace(config)
    assert peer.command_receipts[-1]['status'] == 'completed'
    leases = len(peer.leases)
    with pytest.raises(ProviderCallNotStarted, match='admission is closed'):
        backend.run_turn(**args)
    assert len(calls) == 1 and len(peer.leases) == leases
    writer.event_log.close()


def test_pre_provider_denial_preserves_failure_without_missing_usage(managed):
    from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter

    config, peer, context, _ = managed
    writer = EventWriter(event_log_from_project(context.state_dir, config=context.config))
    peer.deny_budget = True
    calls = []

    class Backend:
        backend_id = 'fixture'

        @managed_turn
        def run_turn(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError('provider failed after invocation')

    for denied in (True, False):
        peer.deny_budget = denied
        stream = AgentSessionStreamEmitter(writer=writer, identity=AgentSessionIdentity(
            run_id='turn-' + str(denied), thread_id='visible', source='kanban', actor='web'))
        stream.start()
        try:
            Backend().run_turn(cwd=context.project_root, thread_id='visible', timeout_s=30)
        except Exception as exc:
            stream.fail(reason=exc)
        synchronize_workspace(config)
        events = writer.event_log.read_all()
        failed = [e for e in events if e.type == 'agent.session.run.failed'][-1]
        assert (failed.payload.get('provider_started') is False) is denied
        receipts = [r['payload'] for r in peer.records.values() if r['payload']['kind'] == 'usage']
        if denied:
            assert not calls and not peer.budgets and not receipts
            assert any(r['payload'].get('operation_id') == 'turn-True'
                       and r['payload'].get('status') == 'failed' for r in peer.records.values())
        else:
            assert len(calls) == 1 and len(peer.budgets) == 1
            assert receipts and all(r['tokens'] is None for r in receipts)
    writer.event_log.close()


def test_managed_event_factory_uses_deployment_binding_without_editing_project(managed):
    config, _, context, _ = managed
    from zf.core.config.loader import load_config
    original = load_config(context.config_path)
    assert not original.observability.controller.enabled
    writer = EventWriter(event_log_from_project(context.state_dir, config=original))
    writer.emit('agent.usage', actor='fixture', payload={'usage': {'input_tokens': 1, 'output_tokens': 2}})
    from zf.integrations.controller.collector import Collector
    assert any(row['payload']['kind'] == 'usage' for row in Collector(context.state_dir, context.config).outbox.pending(32))
    assert not load_config(context.config_path).observability.controller.enabled


def test_closed_session_fence_prevents_reused_session_before_any_grant(managed):
    config, peer, context, credentials = managed
    ledger = Ledger(config.state_dir)
    ledger.insert('stop', 'fence', 'closed', {'project_id': context.config.observability.controller.project_id,
                                           'session_id': 'closed-session'})
    with pytest.raises(ManagedDenied):
        Execution((config, credentials, context), operation_id='op', provider='codex', model='m',
                  session_id='closed-session').begin(lambda: None)
    assert not peer.leases


def test_outbox_retains_ack_replays_controller_restore_and_closes_quarantine(managed):
    _, _, context, _ = managed
    from zf.integrations.controller.collector import Collector, now
    from tests.test_controller_observation import ack
    outbox = Collector(context.state_dir, context.config).outbox
    outbox.enqueue('restore-fixture', now(), {'kind': 'usage', 'record_id': 'record'})
    messages = outbox.pending(32)
    outbox.acknowledge(messages, ack(messages))
    response = {'protocol': 'zfc.control.v1', 'recovery_epoch': 1, 'streams': [], 'dispositions': [],
                'streams_truncated': False, 'dispositions_truncated': False}
    outbox.reconcile(outbox.config.deployment_id, response)
    replay = outbox.pending(32)
    assert any(row['message_id'] == messages[-1]['message_id'] for row in replay)
    outbox.acknowledge(replay, ack(replay, 'rejected'))
    response['dispositions'] = [{'workspace_id': outbox.config.workspace_id,
                                'message_id': row['message_id'], 'action': 'close'} for row in replay]
    outbox.reconcile(outbox.config.deployment_id, response)
    assert outbox.status()['quarantined_records'] == 0
    assert outbox.pending(32) == []


def test_expired_lease_cancels_without_releasing_unknown_liability(managed):
    config, peer, context, credentials = managed
    execution = Execution((config, credentials, context), operation_id='op', provider='codex', model='m')
    cancelled = []
    execution.begin(lambda: cancelled.append(True))
    execution.stop.set()
    execution.thread.join(timeout=2)
    execution.stop.clear()
    execution.body['lease']['expires_at'] = time.time() - 1
    execution.watch(lambda: cancelled.append(True))
    assert cancelled
    assert execution.ledger.get(execution.id)['status'] == 'running'


def gateway_token(config, peer, *, path='/', method='GET', overrides=None):
    import base64
    from authlib.jose import JsonWebToken
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    peer.jwks = {'keys': [{'kty': 'OKP', 'crv': 'Ed25519', 'alg': 'EdDSA', 'use': 'sig', 'kid': 'fixture',
                          'x': base64.urlsafe_b64encode(public).decode().rstrip('=')}]}
    claims = {'iss': config.gateway_origin, 'origin': config.gateway_origin, 'aud': 'zf-workspace:' + config.workspace_id,
              'sub': 'owner', 'sid': 'login', 'auth_version': 1, 'policy_version': 2,
              'workspace_id': config.workspace_id, 'deployment_id': peer.deployment_id, 'generation': 1,
              'iat': int(time.time()), 'exp': int(time.time()) + 25, 'method': method, 'path': path,
              **(overrides or {})}
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return JsonWebToken(['EdDSA']).encode({'alg': 'EdDSA', 'kid': 'fixture'}, claims, pem).decode()


@pytest.mark.parametrize('overrides', [
    {'aud': 'zf-workspace:other'}, {'generation': 2}, {'origin': 'https://evil.test'},
    {'deployment_id': 'retired'}, {'exp': 1}, {'path': '/other'}, {'method': 'POST'},
])
def test_gateway_context_signature_is_insufficient_without_exact_binding(managed, overrides):
    config, peer, _, _ = managed
    from zf.integrations.controller.access import AccessVerifier
    token = gateway_token(config, peer, overrides=overrides)
    with pytest.raises(ManagedDenied):
        AccessVerifier(config).verify(token, 'GET', '/')


def test_actual_web_app_rejects_local_trusted_token_and_revoked_gateway_context(managed, monkeypatch):
    config, peer, context, _ = managed
    from fastapi.testclient import TestClient
    from zf.web.server import create_app
    monkeypatch.setenv('ZF_WEB_TRUSTED_SESSION', '1')
    monkeypatch.setenv('ZF_WEB_ACTION_TOKEN', 'local-test-only-token')
    app = create_app(context.state_dir, config=context.config, project_root=context.project_root)
    with TestClient(app, base_url=config.gateway_origin) as client:
        assert client.get('/', headers={'x-zf-web-token': 'local-test-only-token'}).status_code == 403
        token = gateway_token(config, peer)
        response = client.get('/', headers={'x-zf-access-context': token})
        assert response.status_code != 403
        peer.access_allowed = False
        assert client.get('/', headers={'x-zf-access-context': token}).status_code == 403


def test_command_retry_runs_controlled_action_once_and_preserves_kernel_state(managed):
    config, peer, context, _ = managed
    writer = EventWriter(event_log_from_project(context.state_dir, config=context.config))
    writer.emit('agent.session.run.started', actor='fixture', payload={'thread_id': 'session-a', 'run_id': 'run-a'})
    peer.commands = [{'id': 'command-a', 'workspace_id': config.workspace_id,
        'deployment_id': peer.deployment_id, 'generation': 1, 'policy_version': 2,
        'expires_at': int(time.time()) + 60, 'user_id': 'owner', 'session_id': 'login',
        'payload': {'action': 'session.stop', 'project_id': context.config.observability.controller.project_id,
                    'session_id': 'session-a', 'operation_id': None}}]
    synchronize_workspace(config)
    synchronize_workspace(config)
    attempts = [e for e in writer.event_log.read_all() if e.type == 'runtime.action.attempt.started']
    assert len(attempts) == 1
    assert [r['status'] for r in peer.command_receipts] == ['accepted', 'running', 'completed']
    assert not (context.state_dir / 'kanban.json').exists()
    assert Ledger(config.state_dir).rows('fence')[0]['status'] == 'closed'
