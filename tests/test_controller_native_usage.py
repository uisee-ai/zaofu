import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zf.cli.main import main
from zf.core.config.loader import load_config
from zf.integrations.controller.collector import Collector
from zf.integrations.controller.local_codex import local_codex_usage
from zf.runtime.backend_session_reader import CodexSessionReader
from zf.runtime.provider_usage_reconciliation import build_disk_usage_event
from zf.core.config.schema import RoleConfig


def rollout(root, project, *, name='native-a', created=None, observed=None):
    path = root / 'sessions/2026/11/09' / ('rollout-' + name + '.jsonl')
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {'type': 'session_meta', 'timestamp': created or '2026-10-01T00:00:00Z', 'payload': {'id': name, 'cwd': str(project), 'model_provider': 'openai'}},
        {'type': 'turn_context', 'payload': {'model': 'unpriced-native-fixture'}},
        {'type': 'response_item', 'payload': {'content': 'private-transcript-body'}},
        {'type': 'event_msg', 'timestamp': observed or '2026-11-09T12:00:00Z', 'payload': {
            'type': 'token_count', 'rate_limits': {'plan_type': 'plus'}, 'info': {
                'last_token_usage': {'input_tokens': 100, 'output_tokens': 20},
                'total_token_usage': {'input_tokens': 1000000, 'cached_input_tokens': 900000,
                    'output_tokens': 10000, 'reasoning_output_tokens': 2000, 'total_tokens': 1010000}}}},
    ]
    path.write_text('\n'.join(json.dumps(record) for record in records)+'\n')
    return path


def config(project):
    (project/'zf.yaml').write_text('''version: '1.0'
project:
  name: native-proof
  state_dir: custom-state
observability:
  controller:
    enabled: true
    endpoint: https://controller.example.test
    issuer: https://identity.example.test/realms/zaofu
    workspace_id: workspace-a
    deployment_id: deployment-a
    project_id: project-a
    client_id: client-a
    mode: self_managed_cli
''')
    return load_config(project/'zf.yaml')


def test_local_query_is_project_scoped_read_only_and_keeps_unknown_cost(tmp_path, monkeypatch):
    native = tmp_path/'native'
    project = tmp_path/'project'; project.mkdir()
    rollout(native, project)
    rollout(native, tmp_path/'unrelated', name='other')
    monkeypatch.setenv('CODEX_HOME', str(native))
    result = local_codex_usage(project, project/'custom-state')
    assert result['matched_sessions'] == 1 and result['uploaded'] is False
    row = result['sessions'][0]
    assert row['tokens']['fresh_input_tokens'] == 100000
    assert row['tokens']['total_tokens'] == 1010000  # Reasoning and cached input are not counted twice.
    assert row['accounting_mode'] == 'subscription' and row['cost_status'] == 'unpriced'
    assert row['counter_scope'] == 'session_lifetime'
    assert row['billed_cost_usd'] is None and row['estimated_cost_usd'] is None
    assert row['session_started_at'] == '2026-10-01T00:00:00Z'
    assert 'private-transcript' not in json.dumps(result)
    assert not (project/'custom-state').exists()


def test_cli_query_works_without_enabling_controller(tmp_path, monkeypatch, capsys):
    project = tmp_path/'project'; project.mkdir()
    (project/'zf.yaml').write_text("version: '1.0'\nproject:\n  name: standalone\n")
    rollout(tmp_path/'native', project)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path/'native'))
    monkeypatch.delenv('ZF_PROJECT_ROOT', raising=False); monkeypatch.delenv('ZF_STATE_DIR', raising=False)
    monkeypatch.chdir(project)
    assert main(['controller', 'codex-usage', '--limit', '1']) == 0
    assert json.loads(capsys.readouterr().out)['sessions'][0]['tokens']['total_tokens'] == 1010000
    assert not (project/'.zf').exists()
    assert main(['controller', 'codex-usage', '--limit', '0']) == 2


def test_old_native_session_gets_a_baseline_through_actual_reader_and_factory(tmp_path):
    configuration = config(tmp_path)
    collector = Collector(tmp_path/'custom-state', configuration)
    path = rollout(tmp_path/'native', tmp_path, observed=datetime.now(timezone.utc).isoformat())
    report = CodexSessionReader().read_latest_usage(path)
    event = build_disk_usage_event(role=RoleConfig(name='worker', backend='codex'), usage=report, config=configuration)
    collector(event)
    first = collector.outbox.pending(32)[0]['payload']
    assert first['baseline_only'] is True
    assert first['tokens']['input_tokens'] == 100000
    # Another sample in this series is a normal cumulative balance; the server subtracts the baseline.
    event.id += '-second'; event.payload['usage_sample_id'] += '-second'
    collector(event)
    assert collector.outbox.pending(32)[1]['payload']['baseline_only'] is False


def test_proven_new_native_session_counts_first_receipt(tmp_path):
    configuration = config(tmp_path)
    collector = Collector(tmp_path/'custom-state', configuration)
    created = datetime.now(timezone.utc).isoformat()
    path = rollout(tmp_path/'native', tmp_path, created=created, observed=created)
    report = CodexSessionReader().read_latest_usage(path)
    event = build_disk_usage_event(role=RoleConfig(name='worker', backend='codex'), usage=report, config=configuration)
    collector(event)
    assert collector.outbox.pending(32)[0]['payload']['baseline_only'] is False


def test_missing_initial_counter_does_not_erase_required_baseline(tmp_path):
    from zf.core.events import ZfEvent
    configuration = config(tmp_path)
    collector = Collector(tmp_path/'custom-state', configuration)
    payload = {'backend': 'codex', 'model': 'fixture', 'usage_semantics': 'cumulative', 'usage_series_id': 'native-a', 'usage': {}}
    collector(ZfEvent(type='agent.usage', payload=payload))
    collector(ZfEvent(type='agent.usage', payload={**payload, 'usage': {'input_tokens': 1000000, 'output_tokens': 10000}}))
    assert collector.outbox.pending(32)[0]['payload']['tokens'] is None
    assert collector.outbox.pending(32)[1]['payload']['baseline_only'] is True


def test_existing_v1_series_is_not_rebaselined_after_upgrade(tmp_path):
    from zf.core.events import ZfEvent
    configuration = config(tmp_path)
    collector = Collector(tmp_path/'custom-state', configuration)
    payload = {'backend': 'codex', 'model': 'fixture', 'usage_semantics': 'cumulative', 'usage_series_id': 'native-a',
        'usage': {'input_tokens': 100, 'output_tokens': 20}}
    collector(ZfEvent(type='agent.usage', payload=payload))
    with collector.outbox.connect() as database:
        database.execute("DELETE FROM metadata WHERE key LIKE 'series-counter:%'")
    collector(ZfEvent(type='agent.usage', payload=payload))
    assert collector.outbox.pending(32)[1]['payload']['baseline_only'] is False


@pytest.mark.parametrize('corruption', ['invalid_counter', 'invalid_record'])
def test_malformed_native_usage_stays_missing(tmp_path, monkeypatch, corruption):
    path = rollout(tmp_path/'native', tmp_path)
    if corruption == 'invalid_counter':
        path.write_text(path.read_text().replace('"cached_input_tokens": 900000', '"cached_input_tokens": 2000000'))
    else:
        with path.open('a') as stream:
            stream.write('[]\n')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path/'native'))
    result = local_codex_usage(tmp_path, tmp_path/'state')
    assert result['sessions'][0]['tokens'] is None
    assert result['sessions'][0]['quality'] == 'missing'


def test_incremental_native_receipt_is_not_labeled_session_lifetime(tmp_path, monkeypatch):
    path = rollout(tmp_path/'native', tmp_path)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    del records[-1]['payload']['info']['total_token_usage']
    path.write_text('\n'.join(json.dumps(record) for record in records)+'\n')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path/'native'))
    row = local_codex_usage(tmp_path, tmp_path/'state')['sessions'][0]
    assert row['counter_scope'] == 'last_reported_turn'
    assert row['tokens']['total_tokens'] == 120


def test_server_without_baseline_capability_cannot_ack_history(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import httpx2
    from authlib.integrations.httpx_client import OAuth2Client

    from zf.core.events import ZfEvent
    from zf.integrations.controller.client import synchronize

    configuration = config(tmp_path)
    collector = Collector(tmp_path/'custom-state', configuration)
    collector(ZfEvent(type='agent.usage', payload={
        'backend': 'codex', 'model': 'fixture', 'usage_semantics': 'cumulative',
        'usage_series_id': 'old-native', 'usage': {'input_tokens': 1000000, 'output_tokens': 10000}}))
    schema = json.loads((Path(__file__).parent/'fixtures/controller/telemetry-v1.schema.json').read_text())
    posted = []

    def handle(request):
        if request.method == 'POST':
            posted.append(request.url.path)
            raise AssertionError('unsupported baseline must not be sent')
        if request.url.path.endswith('/me'):
            return httpx2.Response(200, json={'protocol': 'zfc.telemetry.v1', 'workspace_id': 'workspace-a',
                'deployment_id': 'deployment-a', 'max_batch': 32, 'capabilities': []})
        return httpx2.Response(200, json=schema)

    original = httpx2.Client.__init__

    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs, transport=httpx2.MockTransport(handle))

    monkeypatch.setattr(httpx2.Client, '__init__', initialize)
    monkeypatch.setattr(OAuth2Client, 'fetch_token', lambda *args, **kwargs: {'access_token': 'test-only-token'})
    monkeypatch.setenv('ZF_CONTROLLER_CLIENT_SECRET', 'test-only-secret')
    context = SimpleNamespace(config=configuration, state_dir=tmp_path/'custom-state', project_root=tmp_path)
    with pytest.raises(ValueError, match='baseline capability'):
        synchronize(context)
    assert posted == []
    assert collector.outbox.pending(32)[0]['payload']['baseline_only'] is True
