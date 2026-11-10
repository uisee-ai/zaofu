"""Caller, maintenance and long-connection invariants for managed installations."""

import asyncio
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import zipfile

import pytest
from tests.test_controller_managed import managed as managed, workspace as workspace, gateway_token

from zf.integrations.controller.binding import ManagedDenied
from zf.integrations.controller.execution import Execution
from zf.integrations.controller.ledger import Ledger


def test_subprocess_helper_reports_usage_and_lifecycle_at_actual_call_boundary(managed):
    from zf.integrations.controller.helper import run_provider
    from zf.integrations.controller.workspace import synchronize_workspace
    config, peer, context, _ = managed
    result = run_provider([sys.executable, '-c', 'print(\'{"type":"turn.completed","usage":{"input_tokens":17,"output_tokens":2}}\')'],
        backend='codex', state_dir=context.state_dir, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    synchronize_workspace(config)
    assert len(peer.settlements) == 1
    assert peer.settlements[0]['tokens']['input_tokens'] == 17
    records = [row['payload'] for row in peer.records.values()]
    assert any(row['kind'] == 'operation' and row['status'] == 'completed' for row in records)


def test_export_excludes_credentials_and_restarts_uncommitted_partial(managed):
    from zf.integrations.controller.exports import build_export
    from zf.integrations.controller.installation import Installation
    config, _, context, credentials = managed
    (context.project_root / 'keep.txt').write_text('project source')
    (context.project_root / '.env').write_text(credentials['client_secret'])
    (context.project_root / '.codex').mkdir()
    (context.project_root / '.codex' / 'auth.json').write_text('personal')
    job = {'id': 'export-a', 'request': {'projects': [context.config.observability.controller.project_id]}}
    root = Installation(config).path / 'exports'
    root.mkdir()
    (root / 'export-a.partial').write_bytes(b'crashed')
    manifest = build_export(config, credentials, [context], job)
    assert manifest['size_bytes'] > 0
    with zipfile.ZipFile(root / 'export-a.zip') as archive:
        assert any(name.endswith('keep.txt') for name in archive.namelist())
        assert not any('.env' in name or 'auth.json' in name for name in archive.namelist())
    (context.project_root / 'leak.txt').write_text(credentials['client_secret'])
    with pytest.raises(ManagedDenied, match='credential'):
        build_export(config, credentials, [context], job)


def test_export_rejects_symlink_parent_race_and_hardlinks(managed, tmp_path):
    from zf.integrations.controller.exports import read_source
    import os
    destination = tmp_path / 'destination'
    destination.mkdir()
    (destination / 'secret').write_text('outside')
    link = tmp_path / 'link'
    link.symlink_to(destination, target_is_directory=True)
    with pytest.raises(OSError):
        read_source(link / 'secret')
    os.link(destination / 'secret', destination / 'copy')
    with pytest.raises(ManagedDenied):
        read_source(destination / 'copy')


def test_capacity_reservations_are_shared_and_remain_after_unknown_call(managed):
    config, peer, context, credentials = managed
    execution = Execution((config, credentials, context), operation_id='unknown', provider='codex', model='m').begin(lambda: None)
    execution.stop.set()
    execution.thread.join(2)
    with pytest.raises(ManagedDenied, match='unresolved'):
        Execution((config, credentials, context), operation_id='unknown', provider='codex', model='m').begin(lambda: None)
    assert Ledger(config.state_dir).get(execution.id)['status'] == 'running'


def test_receipt_write_failure_recovers_without_repeating_provider(managed, monkeypatch):
    from zf.core.events import EventWriter
    from zf.integrations.controller.execution import reconcile_executions
    from zf.integrations.controller.workspace_transport import WorkspaceTransport
    from zf.integrations.controller.workspace import synchronize_workspace
    config, peer, context, credentials = managed
    execution = Execution((config, credentials, context), operation_id='receipt', provider='codex', model='m').begin(lambda: None)
    original = EventWriter.emit
    def fail(self, name, **kwargs):
        if name == 'controller.usage.receipt':
            raise OSError('disk failure fixture')
        return original(self, name, **kwargs)
    monkeypatch.setattr(EventWriter, 'emit', fail)
    with pytest.raises(OSError):
        execution.finish({'input_tokens': 2, 'output_tokens': 1}, status='completed')
    assert execution.ledger.get(execution.id)['status'] == 'receipt_pending'
    monkeypatch.setattr(EventWriter, 'emit', original)
    with WorkspaceTransport(config, credentials) as transport:
        reconcile_executions(config, credentials, transport, [context])
    synchronize_workspace(config)
    assert execution.ledger.get(execution.id)['status'] == 'settled'
    assert len(peer.budgets) == 1 and len(peer.settlements) == 1


@pytest.mark.asyncio
async def test_websocket_current_authorization_revocation_closes_active_handler(managed, monkeypatch):
    from zf.integrations.controller.access import ManagedAccessMiddleware
    config, peer, _, _ = managed
    token = gateway_token(config, peer, method='WEBSOCKET', path='/stream')
    messages, closed = [], asyncio.Event()
    async def application(scope, receive, send):
        await send({'type': 'websocket.accept'})
        peer.access_allowed = False
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    original = asyncio.wait
    async def immediate(tasks, timeout):
        return await original(tasks, timeout=.01)
    monkeypatch.setattr(asyncio, 'wait', immediate)
    async def send(message):
        messages.append(message)
    await ManagedAccessMiddleware(application, config)({'type': 'websocket', 'path': '/stream',
        'headers': [(b'x-zf-access-context', token.encode()), (b'origin', config.gateway_origin.encode())]}, None, send)
    assert closed.is_set()
    assert messages[-1] == {'type': 'websocket.close', 'code': 4403}


def test_managed_drain_denies_new_calls_and_unenroll_cannot_self_authorize(managed, monkeypatch):
    from zf.integrations.controller.lifecycle import drain, unenroll
    from zf.integrations.controller.binding import managed_config
    import subprocess
    config, _, context, credentials = managed
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    assert drain(config)['new_execution'] == 'closed'
    with pytest.raises(ManagedDenied, match='draining'):
        Execution((config, credentials, context), operation_id='new', provider='codex', model='m').begin(lambda: None)
    with pytest.raises(Exception):
        unenroll(config)
    assert managed_config().workspace_id == config.workspace_id
    assert context.config_path.exists()


def test_cloud_creation_cannot_escape_private_project_or_state_directory(managed, monkeypatch, tmp_path):
    from dataclasses import replace
    from zf.integrations.controller import binding
    config, _, _, _ = managed
    config = replace(config, hosting='cloud', project_root=tmp_path / 'private')
    monkeypatch.setattr(binding, 'managed_config', lambda: config)
    for root, state in [(tmp_path / 'other', '.zf'), (config.project_root / 'a', '../../shared')]:
        with pytest.raises(ManagedDenied):
            binding.project_directory(root, 'default', state)
    assert binding.project_directory(config.project_root / 'a', 'default', 'custom-state') == config.workspace


def test_zero_project_managed_web_serve_wires_background_connector_and_uvicorn(managed, monkeypatch):
    from zf.integrations.controller.lifecycle import serve
    from zf.integrations.controller import lifecycle
    import uvicorn
    config, peer, _, _ = managed
    peer.policy.update(projects=[], version=3)
    lifecycle.synchronize_workspace(config)
    seen = []
    monkeypatch.setattr(lifecycle, 'synchronize_workspace', lambda config: seen.append('sync'))
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kw: seen.append((app, kw)))
    serve(config, host='127.0.0.1', port=8002)
    assert seen[0] == 'sync' and seen[1][1]['port'] == 8002
    assert any(middleware.cls.__name__ == 'ManagedAccessMiddleware' for middleware in seen[1][0].user_middleware)


def test_cloud_bootstrap_authenticates_before_sealing_and_removes_raw_secret(managed, monkeypatch):
    from zf.integrations.controller.installation import Installation
    import os
    config, _, _, credentials = managed
    mapping = {'issuer': 'ZF_CONTROLLER_ISSUER', 'controller': 'ZF_CONTROLLER_URL',
        'workspace_id': 'ZFC_WORKSPACE_ID', 'deployment_id': 'ZF_CONTROLLER_DEPLOYMENT_ID',
        'client_id': 'ZF_CONTROLLER_CLIENT_ID', 'client_secret': 'ZF_CONTROLLER_CLIENT_SECRET'}
    for key, name in mapping.items():
        monkeypatch.setenv(name, credentials[key])
    installation = Installation(config)
    assert installation.bootstrap()['online_verified']
    assert installation.credentials() == credentials
    assert 'ZF_CONTROLLER_CLIENT_SECRET' not in os.environ
    assert credentials['client_secret'] not in (installation.path / 'binding.json').read_text()


def test_foreground_runtime_holds_project_slot_and_releases_after_verified_exit(managed, monkeypatch):
    from zf.integrations.controller.project_runtime import managed_runtime
    config, peer, context, _ = managed
    monkeypatch.chdir(context.project_root)
    observed = []
    @managed_runtime
    def runtime(args):
        observed.extend(peer.leases)
        return 0
    assert runtime(SimpleNamespace(dry_run=False, no_watch=False)) == 0
    assert observed[-1]['kind'] == 'project'
    assert Ledger(config.state_dir).get('project:' + context.config.observability.controller.project_id)['status'] == 'unloaded'
    with pytest.raises(ManagedDenied, match='foreground'):
        runtime(SimpleNamespace(dry_run=False, no_watch=True))


def test_actual_stream_transport_admits_each_dispatch_and_blocks_after_quota(managed, monkeypatch):
    from tests.test_transport_stream_json import _make_fake_query, FakeResultMessage
    from zf.core.config.schema import RoleConfig
    from zf.core.state.role_sessions import RoleSessionRegistry
    from zf.runtime.transport_stream_json import StreamJsonTransport
    config, peer, context, _ = managed
    monkeypatch.chdir(context.project_root)
    registry = RoleSessionRegistry(context.state_dir / 'role_sessions.yaml', context.project_root)
    transport = StreamJsonTransport(context.state_dir, registry, query_fn=_make_fake_query([
        FakeResultMessage(usage={'input_tokens': 5, 'output_tokens': 2})]))
    transport.spawn(RoleConfig(name='dev'), argv=[], cwd=context.project_root)
    transport.send_task('dev', briefing_path=context.state_dir / 'briefing.md', prompt='fixture')
    assert len(peer.budgets) == 1
    assert Ledger(config.state_dir).rows('execution')[0]['status'] == 'settlement_pending'
    peer.deny_budget = True
    import httpx2
    with pytest.raises(httpx2.HTTPStatusError):
        transport.send_task('dev', briefing_path=context.state_dir / 'briefing.md', prompt='fixture denied')
    assert len(peer.budgets) == 1
    transport.shutdown()


def test_actual_tmux_dispatch_and_compaction_cannot_paste_without_budget(managed, monkeypatch):
    import httpx2
    import yaml
    from zf.core.config.loader import load_config
    from zf.runtime.transport import TmuxTransport
    config, peer, context, _ = managed
    raw = yaml.safe_load(context.config_path.read_text())
    raw['roles'] = [{'name': 'dev', 'backend': 'codex'}]
    context.config_path.write_text(yaml.safe_dump(raw))
    sent = []
    transport = TmuxTransport(SimpleNamespace(dry_run=False, send_keys=lambda *args: sent.append(args)))
    transport._expected_cwds['dev'] = context.project_root
    peer.deny_budget = True
    for call in [lambda: transport.send_task('dev', context.state_dir / 'briefing.md', 'fixture'),
                 lambda: transport.compact_context('dev', '/compact')]:
        with pytest.raises(httpx2.HTTPStatusError):
            call()
    assert not sent


def test_actual_terminal_create_reuse_and_stop_preserve_one_budget_responsibility(managed):
    from tests.test_web_terminal_runtime import FakeBackend, _config
    from zf.web.terminal_service import TerminalService
    config, peer, context, _ = managed
    backend = FakeBackend()
    service = TerminalService(project_id=context.config.observability.controller.project_id,
        project_root=context.project_root, state_dir=context.state_dir, config=_config(),
        allowed_providers=['codex'], backend=backend, usage_binding_wait_seconds=0)
    record = service.create_session(slot='terminal-1', provider='codex', title='fixture')
    assert len(peer.budgets) == 1 and backend.created == 1
    assert service.create_session(slot='terminal-1', provider='codex', title='fixture').session_id == record.session_id
    assert len(peer.budgets) == 1
    service.stop_session(record.session_id)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and Ledger(config.state_dir).rows('execution')[0]['status'] in {'running', 'receipt_pending'}:
        time.sleep(.1)
    row = Ledger(config.state_dir).rows('execution')[0]
    assert row['status'] == 'settlement_pending'
    assert row['body']['receipt']['usage'] == {}  # No native receipt must not become zero usage.


def test_scope_removal_closes_new_calls_but_preserves_last_usage_receipt(managed):
    from zf.integrations.controller.workspace import synchronize_workspace
    from zf.integrations.controller.binding import project_binding
    config, peer, context, credentials = managed
    execution = Execution((config, credentials, context), operation_id='last', provider='codex', model='m').begin(lambda: None)
    peer.policy.update(projects=[], version=3, applied_version=3)
    synchronize_workspace(config)
    with pytest.raises(ManagedDenied, match='outside'):
        project_binding(context.project_root)
    execution.finish({'input_tokens': 9, 'output_tokens': 1}, status='cancelled')
    synchronize_workspace(config)
    assert Ledger(config.state_dir).get(execution.id)['status'] == 'settled'
    assert peer.settlements[-1]['tokens']['input_tokens'] == 9


def test_price_uses_exact_local_catalog_and_never_prices_subscription(managed):
    from dataclasses import replace
    from zf.integrations.controller.pricing import cost_fields
    config, _, context, _ = managed
    configured = replace(config, price_version='2026-08-11')
    usage = {'input_tokens': 1000, 'output_tokens': 100}
    fields = cost_fields(context, configured, 'claude-code', 'claude-sonnet-4-6', usage, 'api', True)
    assert fields['estimated_cost_usd'] == '0.004500000000'
    assert cost_fields(context, configured, 'claude-code', 'claude-sonnet-4-6', usage, 'subscription', True) == {}
    assert cost_fields(context, config, 'claude-code', 'claude-sonnet-4-6', usage, 'api', True) == {}


def test_approved_unenroll_removes_platform_key_but_retains_project_and_personal_login(managed, monkeypatch):
    import httpx2
    from zf.integrations.controller.lifecycle import unenroll
    from zf.integrations.controller.binding import managed_config
    from zf.integrations.controller.installation import Installation
    config, peer, context, credentials = managed
    installation = Installation(config)
    with installation.locked():
        installation.write('draining.json', {'started_at': time.time()})
    personal = context.project_root / '.codex' / 'auth.json'
    personal.parent.mkdir()
    personal.write_text('personal fixture')
    original = httpx2.Client.post
    def post(client, url, **kwargs):
        if str(url).endswith('/enrollment/v1/release-status'):
            proof = kwargs['json']
            assert proof['deployment_id'] == credentials['deployment_id'] and proof['signature']
            return httpx2.Response(200, request=httpx2.Request('POST', url), json={
                **{key: proof[key] for key in ('workspace_id', 'deployment_id', 'generation', 'nonce')},
                'protocol': 'zfc.control.v1', 'allowed': True})
        return original(client, url, **kwargs)
    monkeypatch.setattr(httpx2.Client, 'post', post)
    assert unenroll(config)['management'] == 'standalone'
    assert managed_config() is None
    assert personal.read_text() == 'personal fixture' and context.config_path.exists()
    assert not (installation.path / 'private.pem').exists()
