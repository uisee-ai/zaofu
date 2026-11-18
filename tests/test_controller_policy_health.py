"""Remote settings and sanitized Provider health at real Connector/Provider boundaries."""

from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
import time

import pytest
import yaml
from tests.test_controller_managed import managed as managed, workspace as workspace

from zf.core.config.controller_workspace import load_controller_workspace
from zf.core.events import ZfEvent
from zf.integrations.controller.binding import ManagedDenied, project_binding
from zf.integrations.controller.collector import Collector
from zf.integrations.controller.execution import Execution, managed_turn
from zf.integrations.controller.health import capture, classify, deliver
from zf.integrations.controller.installation import Installation
from zf.integrations.controller.ledger import Ledger
from zf.integrations.controller.runtime_settings import applied
from zf.integrations.controller.workspace import synchronize_workspace
from zf.integrations.controller.workspace_transport import WorkspaceTransport


def settings(peer, **changes):
    body = dict(protocol='zfc.runtime-settings.v1', interval_seconds=2, batch_size=1,
        diagnostic_sample_rate=0.0, mode='self_managed_cli', request_tokens=123,
        request_money='0', price_version='unknown', request_seconds=60)
    body.update(changes)
    peer.policy.update(version=peer.policy['version']+1)
    peer.policy['limits'].update(runtime_settings=body, runtime_settings_digest=hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
    return body


def allow_api(config):
    raw = yaml.safe_load(config.path.read_text())
    raw['workspace']['controller']['allowed_modes'] = ['self_managed_cli', 'self_managed_api']
    config.path.write_text(yaml.safe_dump(raw))
    return load_controller_workspace(config.path)


def test_applied_settings_reach_transport_admission_and_actual_otlp(managed, monkeypatch):
    from zf.core.config.schema import OtlpExporterConfig
    from zf.core.events import EventLog
    from zf.runtime import otlp_exporter
    config, peer, ctx, credentials = managed
    settings(peer)
    result = synchronize_workspace(config)
    assert result['policy_error'] is None
    assert peer.receipts[-1]['settings_digest'] == peer.policy['limits']['runtime_settings_digest']
    effective = applied(load_controller_workspace(config.path))
    assert effective.interval_seconds == 2 and effective.batch_size == 1
    assert effective.target() == config.target()
    peer.policy['applied_version'] = peer.policy['version']
    execution = Execution(project_binding(ctx.project_root), operation_id='settings-call', provider='fixture', model='m').begin(lambda: None)
    assert peer.budgets[-1]['input']['max_tokens'] == 123
    assert peer.budgets[-1]['input']['expires_in'] == 60
    execution.finish({'input_tokens': 7, 'output_tokens': 1}, status='completed')
    rates = []
    original = otlp_exporter._build_pending_batch
    def build(*args, **kwargs):
        rates.append(kwargs['sample_rate'])
        return original(*args, **kwargs)
    monkeypatch.setattr(otlp_exporter, '_build_pending_batch', build)
    monkeypatch.setenv('ZF_POLICY_TEST_OTLP', 'http://localhost:9999/v1/traces')
    log = EventLog(ctx.state_dir / 'events.jsonl')
    exporter = otlp_exporter.OtlpExporter(state_dir=ctx.state_dir, event_log=log,
        config=OtlpExporterConfig(enabled=True, endpoint_env='ZF_POLICY_TEST_OTLP', healthy_sample_rate=1.0),
        sender=lambda *args: 200)
    exporter.run_once()
    log.close()
    assert rates == [0.0]
    batches = []
    request = WorkspaceTransport.request
    def record(self, method, path, body=None, **kwargs):
        if path.endswith('/batches'):
            batches.append(len(body['records']))
        return request(self, method, path, body, **kwargs)
    monkeypatch.setattr(WorkspaceTransport, 'request', record)
    for _ in range(8):
        synchronize_workspace(config)
    assert batches and max(batches) == 1
    receipts = [row['payload'] for row in peer.records.values() if row['payload']['kind'] == 'usage']
    assert sum(r['tokens']['input_tokens'] for r in receipts) == 7


def test_mode_change_waits_for_consent_and_settlement_and_preserves_late_usage(managed):
    config, peer, ctx, credentials = managed
    before = datetime.now(timezone.utc).isoformat()
    execution = Execution(project_binding(ctx.project_root), operation_id='old', provider='fixture', model='m').begin(lambda: None)
    settings(peer, mode='self_managed_api', batch_size=32)
    assert synchronize_workspace(config)['policy_error'] == 'local_consent_required'
    config = allow_api(config)
    # An unresolved execution is not made executable by a newer settings version.
    assert synchronize_workspace(config)['policy_error'] == 'execution_drain_required'
    execution.finish({'input_tokens': 9, 'output_tokens': 1}, status='completed')
    assert synchronize_workspace(config)['policy_error'] == 'execution_drain_required'
    stale = Execution(project_binding(ctx.project_root), operation_id='stale', provider='fixture', model='m')
    result = synchronize_workspace(config)
    assert result['policy_error'] is None
    assert applied(config).effective_mode == 'self_managed_api'
    with pytest.raises(ManagedDenied, match='changed'):
        stale.begin(lambda: None)
    peer.policy['applied_version'] = peer.policy['version']
    new = Execution(project_binding(ctx.project_root), operation_id='new', provider='fixture', model='m').begin(lambda: None)
    assert peer.budgets[-1]['input']['mode'] == 'self_managed_api'
    new.finish({'input_tokens': 3, 'output_tokens': 1}, status='completed')
    current = project_binding(ctx.project_root)[2]
    collector = Collector(current.state_dir, current.config)
    collector(ZfEvent(type='agent.usage', actor='late', payload={'usage_sample_id': 'late-old',
        'usage_timestamp': before, 'usage': {'input_tokens': 2, 'output_tokens': 1}}))
    late = next(r['payload'] for r in collector.outbox.pending(32) if r['payload'].get('record_id') == hashlib.sha256((collector.cloud.project_id + ':late-old').encode()).hexdigest())
    assert late['mode'] == 'self_managed_cli'
    synchronize_workspace(config)
    old = next(r['payload'] for r in peer.records.values() if r['payload'].get('budget_reservation_id') == peer.budgets[0]['id'])
    assert old['mode'] == 'self_managed_cli'


@pytest.mark.parametrize('change', ['digest', 'rollback', 'conflict', 'unsupported'])
def test_settings_reject_invalid_or_changed_policy_without_applying(managed, change):
    config, peer, _, _ = managed
    settings(peer)
    synchronize_workspace(config)
    original = (Installation(config).path / 'policy.json').read_bytes()
    if change == 'digest':
        peer.policy['version'] += 1
        peer.policy['limits']['runtime_settings_digest'] = '0'*64
    elif change == 'rollback':
        peer.policy['version'] -= 1
    elif change == 'conflict':
        peer.policy['limits']['runtime_settings']['batch_size'] = 3
    else:
        settings(peer, protocol='future')
    with pytest.raises(ValueError):
        synchronize_workspace(config)
    assert (Installation(config).path / 'policy.json').read_bytes() == original


@pytest.mark.parametrize('error,expected', [(None, 'healthy'), ({'status_code': 401}, 'credential_expired'),
    ({'status_code': 429}, 'rate_limited'), ({'error': {'code': 'insufficient_quota'}}, 'quota_exhausted'),
    ({'status_code': 503}, 'outage'), ('SECRET PROMPT 401', 'unknown'),
    ({'error': {'code': ['untrusted']}}, 'unknown')])
def test_health_redaction_at_actual_provider_boundary_and_retry(managed, monkeypatch, error, expected):
    config, peer, context, credentials = managed
    class Backend:
        backend_id = 'fixture'
        @managed_turn
        def run_turn(self, **kwargs):
            return SimpleNamespace(status='completed' if error is None else 'failed', error=error,
                provider_session_id='native', usage={'input_tokens': 4, 'output_tokens': 1})
    Backend().run_turn(cwd=context.project_root, thread_id='health', provider_session_id='native', timeout_s=30)
    row = Ledger(config.state_dir).rows('provider_health')[0]
    assert row['body']['payload']['status'] == expected
    assert 'SECRET' not in json.dumps(row) and 'error' not in row['body']['payload']
    calls = []
    class Peer:
        def request(self, method, path, body):
            calls.append(dict(body))
            if len(calls) == 1:
                raise OSError('lost ACK')
            return {'status': 'duplicate'}
    transport = Peer()
    assert deliver(config.state_dir, transport) == 1
    assert deliver(config.state_dir, transport) == 0
    assert calls[0] == calls[1]
    assert Ledger(config.state_dir).rows('provider_health')[0]['status'] == 'delivered'
    # An older replay cannot overwrite a more recent observation.
    capture(config.state_dir, credentials['deployment_id'], context.config.observability.controller.project_id,
        'self_managed_cli', 'fixture', status='completed', observed_at=time.time()-5, sample_id='old')
    assert Ledger(config.state_dir).rows('provider_health')[0]['body']['payload'] == calls[0]


def test_timeout_and_expired_health_never_become_healthy(tmp_path, monkeypatch):
    assert classify('unknown', TimeoutError()) == 'unknown'
    capture(tmp_path, 'deployment', 'project', 'self_managed_cli', 'codex', status='completed', sample_id='first')
    row = Ledger(tmp_path).rows('provider_health')[0]
    monkeypatch.setattr('zf.integrations.controller.health.time.time', lambda: row['body']['payload']['observed_at'] + 301)
    assert deliver(tmp_path, SimpleNamespace(request=lambda *args: pytest.fail('expired health must not be uploaded'))) == 0
    assert Ledger(tmp_path).rows('provider_health')[0]['status'] == 'expired'
    capture(tmp_path, 'deployment', 'project', 'managed_cpa', 'codex', status='completed', sample_id='cpa')
    assert len(Ledger(tmp_path).rows('provider_health')) == 1


def test_helper_structured_auth_failure_is_captured_from_real_subprocess(managed):
    import sys
    from zf.integrations.controller.helper import run_provider
    config, _, context, _ = managed
    result = run_provider([sys.executable, '-c',
        'import sys;sys.stderr.write(\'{"status_code":401,"secret":"never-upload"}\');sys.exit(1)'],
        backend='codex', state_dir=context.state_dir, text=True)
    assert result.returncode == 1
    health = Ledger(config.state_dir).rows('provider_health')[0]['body']['payload']
    assert health['status'] == 'credential_expired'
    assert 'never-upload' not in json.dumps(health)


def test_watch_and_cloud_serve_wait_use_applied_interval(managed, monkeypatch):
    from tests.test_controller_workspace import cli
    from zf.integrations.controller import lifecycle
    import uvicorn
    config, peer, _, _ = managed
    settings(peer, interval_seconds=3)
    synchronize_workspace(config)
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        raise KeyboardInterrupt
    with monkeypatch.context() as patch:
        patch.setattr('zf.cli.controller_workspace.time.sleep', sleep)
        assert cli(config, 'sync', '--watch') == 0
    assert waits == [3]
    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            return True
        def set(self):
            pass
    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
        def join(self, **kwargs):
            pass
    monkeypatch.setattr(lifecycle, 'threading', SimpleNamespace(Event=Stop, Thread=Thread))
    monkeypatch.setattr(uvicorn, 'run', lambda *args, **kwargs: None)
    lifecycle.serve(config, host='127.0.0.1', port=8002)
    assert waits == [3, 3]


@pytest.mark.parametrize('existing_policy', [False, True])
def test_serve_rejected_policy_keeps_access_closed_and_accepts_correction(managed, monkeypatch, existing_policy):
    from fastapi.testclient import TestClient
    from zf.integrations.controller import lifecycle
    import uvicorn

    config, peer, _, _ = managed
    saved = Installation(config).path / 'policy.json'
    if not existing_policy:
        saved.unlink()
    before = saved.read_bytes() if saved.exists() else None
    settings(peer, batch_size=100)
    callbacks = []
    waits = []

    class Stop:
        def wait(self, seconds):
            waits.append(seconds)
            if len(waits) == 2:
                settings(peer, batch_size=16)
            return len(waits) == 3

        def set(self):
            pass

    class Thread:
        def __init__(self, target, **kwargs):
            callbacks.append(target)

        def start(self):
            pass

        def join(self, **kwargs):
            pass

    def run(app, **kwargs):
        assert peer.receipts[-1]['applied'] is False
        assert (saved.read_bytes() if saved.exists() else None) == before
        assert any(row['payload']['kind'] == 'heartbeat' for row in peer.records.values())
        with TestClient(app) as client:
            assert client.get('/api/workspace/projects').status_code == 403
            callbacks[0]()
            assert peer.receipts[-2]['applied'] is False
            assert peer.receipts[-1]['applied'] is True
            assert applied(config).batch_size == 16
            # Recovery cannot turn private ingress into an unauthenticated API.
            assert client.get('/api/workspace/projects').status_code == 403

    monkeypatch.setattr(lifecycle, 'threading', SimpleNamespace(Event=Stop, Thread=Thread))
    monkeypatch.setattr(uvicorn, 'run', run)
    lifecycle.serve(config, host='127.0.0.1', port=8002)
    assert len(waits) == 3


def test_serve_identity_failure_never_binds_listener(managed, monkeypatch):
    from zf.integrations.controller import lifecycle
    import uvicorn

    config, _, _, _ = managed
    def invalid(config):
        raise ValueError('Controller policy identity mismatch')
    monkeypatch.setattr(lifecycle, 'synchronize_workspace', invalid)
    monkeypatch.setattr(uvicorn, 'run', lambda *a, **kw: pytest.fail('invalid identity must not listen'))
    with pytest.raises(ValueError, match='identity mismatch'):
        lifecycle.serve(config, host='127.0.0.1', port=8002)


def test_health_capture_failure_cannot_erase_accounting_receipt(managed, monkeypatch):
    config, peer, context, _ = managed
    execution = Execution(project_binding(context.project_root), operation_id='diagnostic-failure', provider='fixture', model='m').begin(lambda: None)
    def broken(*args, **kwargs):
        raise OSError('diagnostic storage unavailable')
    monkeypatch.setattr('zf.integrations.controller.health._capture', broken)
    execution.finish({'input_tokens': 8, 'output_tokens': 2}, status='completed')
    synchronize_workspace(config)
    assert Ledger(config.state_dir).get(execution.id)['status'] == 'settled'
    assert peer.settlements[-1]['tokens']['input_tokens'] == 8


def test_observed_scope_removal_does_not_require_retired_project_config(workspace, tmp_path):
    from tests.test_controller_workspace import enrolled, add_project, configure_projects
    config, peer, _ = workspace
    enrolled(workspace)
    project, context = add_project(config, tmp_path / 'retired')
    config = configure_projects(config, peer, [project])
    synchronize_workspace(config)
    context.config_path.unlink()
    peer.policy.update(projects=[], version=3)
    assert synchronize_workspace(config)['policy_error'] is None
