"""Opt-in event-to-durable-outbox contract, independent of the Controller package."""

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from zf.core.config.loader import ConfigError, load_config
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.events.log import EventLog
from zf.integrations.controller.collector import Collector
from zf.integrations.controller.outbox import Outbox, OutboxFull
from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter


@pytest.fixture
def project(tmp_path):
    raw = {'version': '1.0', 'project': {'name': 'Controller fixture', 'state_dir': 'custom-state'},
           'observability': {'controller': {'enabled': True, 'endpoint': 'https://controller.example.test',
           'issuer': 'https://identity.example.test/realms/zaofu', 'workspace_id': 'workspace-a',
           'deployment_id': 'deployment-a', 'project_id': 'project-a', 'client_id': 'client-a',
           'mode': 'self_managed_cli'}}}
    (tmp_path / 'zf.yaml').write_text(yaml.safe_dump(raw))
    config = load_config(tmp_path / 'zf.yaml')
    return tmp_path, config, tmp_path / 'custom-state'


def usage(**payload):
    return ZfEvent(type='agent.usage', actor='worker', task_id='task-a', payload={
        'backend': 'codex', 'model': 'fixture-model',
        'usage': {'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 30, 'reasoning_output_tokens': 10},
        **payload})


def validate(messages):
    schema = json.loads((Path(__file__).parent / 'fixtures/controller/telemetry-v1.schema.json').read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for message in messages:
        validator.validate(message)


def ack(messages, status='accepted'):
    return {'protocol': 'zfc.telemetry.v1', 'results': [
        {'message_id': m['message_id'], 'seq': m['seq'], 'status': status} for m in messages]}


def test_default_standalone_creates_no_connector_state(tmp_path):
    (tmp_path / 'zf.yaml').write_text('version: "1.0"\nproject:\n  name: standalone\n')
    config = load_config(tmp_path / 'zf.yaml')
    assert not config.observability.controller.enabled
    log = event_log_from_project(tmp_path / 'state', config=config)
    EventWriter(log).append(usage())
    log.close()
    assert not (tmp_path / 'state/controller').exists()


@pytest.mark.parametrize('override', [
    {'enabled': 'true'}, {'batch_size': 33}, {'max_pending_bytes': 5}, {'client_secret': 'forbidden'},
    {'endpoint': 'http://remote.test', 'development': True}, {'workspace_id': 'bad space'},
    {'client_secret_env': 'secret-value'}, {'mode': 'litellm'}, {'interval_seconds': True},
])
def test_invalid_config_fails_before_runtime(project, override):
    root, _, _ = project
    data = yaml.safe_load((root / 'zf.yaml').read_text())
    data['observability']['controller'].update(override)
    (root / 'zf.yaml').write_text(yaml.safe_dump(data))
    with pytest.raises(ConfigError):
        load_config(root / 'zf.yaml')


def test_event_writer_captures_native_usage_without_bodies_and_replays_once(project):
    _, config, state = project
    log = event_log_from_project(state, config=config)
    event = usage(session_id='session-a', operation_id='operation-a', prompt='must-stay-local')
    EventWriter(log).append(event)
    log.close()
    collector = Collector(state, config)
    message = collector.outbox.pending(32)[0]
    assert message['payload']['tokens'] == {'input_tokens': 70, 'output_tokens': 20,
        'cache_read_tokens': 30, 'cache_creation_tokens': 0, 'reasoning_tokens': 10}
    assert message['payload']['session_id'] == 'session-a'
    assert 'must-stay-local' not in json.dumps(message)
    collector(event)
    assert collector.outbox.status()['pending_records'] == 1
    validate([message])
    collector.outbox.acknowledge([message], ack([message]))
    collector(event)
    assert collector.outbox.pending(32) == []
    assert len(EventLog(state / 'events.jsonl').read_all()) == 1


def test_short_native_session_and_turn_usage_survive_restart(project):
    _, config, state = project
    log = event_log_from_project(state, config=config)
    stream = AgentSessionStreamEmitter(writer=EventWriter(log), identity=AgentSessionIdentity(
        run_id='run-a', thread_id='session-short', source='channel', actor='helper', task_id='task-a', backend='codex'))
    stream.start()
    stream.complete(usage={'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 30})
    log.close()
    collector = Collector(state, config)
    collector.project(); collector.heartbeat()
    messages = collector.outbox.pending(32)
    validate(messages)
    assert [m['payload']['status'] for m in messages if m['payload']['kind'] == 'session'] == ['active', 'idle']
    assert [m['payload']['status'] for m in messages if m['payload']['kind'] == 'operation'] == ['running', 'completed']
    receipt = next(m['payload'] for m in messages if m['payload']['kind'] == 'usage')
    assert receipt['session_id'] == 'session-short' and receipt['operation_id'] == 'run-a'
    assert receipt['tokens']['input_tokens'] == 70


def test_missing_usage_is_not_zero_and_cumulative_samples_are_stable(project):
    _, config, state = project
    collector = Collector(state, config)
    events = [usage(usage={}), usage(usage_semantics='cumulative', usage_series_id='native-series'),
              usage(usage_semantics='cumulative', usage_series_id='native-series', usage={'input_tokens': 150, 'output_tokens': 30})]
    for event in events + events:
        collector(event)
    messages = collector.outbox.pending(32)
    validate(messages)
    assert messages[0]['payload']['tokens'] is None
    assert messages[0]['payload']['quality'] == 'missing'
    assert [m['payload']['sample_seq'] for m in messages[1:]] == [1, 2]


def test_capacity_failure_keeps_original_event_for_recovery(project):
    _, config, state = project
    config.observability.controller.max_pending_records = 1
    log = event_log_from_project(state, config=config)
    first, second = usage(), usage()
    EventWriter(log).append(first)
    with pytest.raises(OutboxFull):
        EventWriter(log).append(second)
    log.close()
    assert [event.id for event in EventLog(state / 'events.jsonl').read_all()] == [first.id, second.id]
    outbox = Outbox(state, config.observability.controller)
    batch = outbox.pending(32); outbox.acknowledge(batch, ack(batch))
    Collector(state, config)(second)
    assert len(outbox.pending(32)) == 1


def test_ack_identity_is_transactional_and_rejections_are_retained(project):
    _, config, state = project
    collector = Collector(state, config)
    collector(usage()); collector(usage())
    batch = collector.outbox.pending(32)
    bad = ack(batch); bad['results'][1]['message_id'] = 'wrong'
    with pytest.raises(ValueError):
        collector.outbox.acknowledge(batch, bad)
    assert len(collector.outbox.pending(32)) == 2
    collector.outbox.acknowledge(batch[:1], ack(batch[:1], 'rejected'))
    assert collector.outbox.status()['quarantined_records'] == 1
    assert collector.outbox.status()['pending_records'] == 2


def test_binding_history_preserves_origin_and_mode_during_crash_recovery(project):
    _, config, state = project
    first = Collector(state, config)
    old = usage()
    first(old)
    recovery = usage()  # Durable event occurred under A, not yet projected.
    new_config = replace(config, observability=replace(config.observability,
        controller=replace(config.observability.controller, deployment_id='deployment-b', mode='managed_cpa')))
    second = Collector(state, new_config)
    second(recovery); second(usage())
    batch = second.outbox.pending(32)
    assert [m['origin_deployment_id'] for m in batch] == ['deployment-a', 'deployment-a', 'deployment-b']
    assert [m['payload']['mode'] for m in batch] == ['self_managed_cli', 'self_managed_cli', 'managed_cpa']
    assert [m['seq'] for m in batch] == [1, 2, 1]
    with pytest.raises(ValueError, match='retired'):
        Collector(state, config)
    before = replace(old, id='old-unbound-event', ts='2020-01-01T00:00:00Z')
    second(before)
    assert len(second.outbox.pending(32)) == 3
    assert datetime.fromisoformat(second.outbox.status()['coverage_since']) < datetime.now(timezone.utc)


def test_cli_entrypoint_custom_state_and_disabled_fallback(project, monkeypatch, capsys):
    from zf.cli.main import main
    root, _, state = project
    monkeypatch.chdir(root)
    monkeypatch.delenv('ZF_PROJECT_ROOT', raising=False)
    monkeypatch.delenv('ZF_STATE_DIR', raising=False)
    assert main(['controller', 'status']) == 0
    assert json.loads(capsys.readouterr().out)['pending_records'] == 0
    assert (state / 'controller/outbox.sqlite3').exists()
    assert not (root / '.zf').exists()
    data = yaml.safe_load((root / 'zf.yaml').read_text()); data.pop('observability')
    (root / 'zf.yaml').write_text(yaml.safe_dump(data))
    assert main(['controller', 'sync']) == 2


@pytest.mark.parametrize('capacity', [100, 1])
def test_network_caller_uses_bound_identity_and_validates_ack(project, monkeypatch, capacity):
    pytest.importorskip('authlib', reason='install the controller extra for transport qualification')
    import httpx2
    from authlib.integrations.httpx_client import OAuth2Client
    from zf.integrations.controller.client import synchronize
    root, config, state = project
    config.observability.controller.max_pending_records = capacity
    collector = Collector(state, config); collector(usage())
    schema = json.loads((Path(__file__).parent / 'fixtures/controller/telemetry-v1.schema.json').read_text())
    seen = []
    def handle(request):
        assert request.headers['authorization'] == 'Bearer temporary-test-token'
        if request.url.path.endswith('/me'):
            return httpx2.Response(200, json={'protocol': 'zfc.telemetry.v1', 'workspace_id': 'workspace-a', 'deployment_id': 'deployment-a', 'max_batch': 32})
        if request.url.path.endswith('/schema'):
            return httpx2.Response(200, json=schema)
        records = json.loads(request.content)['records']; seen.extend(records)
        return httpx2.Response(200, json=ack(records))
    original = httpx2.Client.__init__
    def initialize(self, *args, **kwargs):
        kwargs['transport'] = httpx2.MockTransport(handle)
        original(self, *args, **kwargs)
    monkeypatch.setattr(httpx2.Client, '__init__', initialize)
    monkeypatch.setattr(OAuth2Client, 'fetch_token', lambda *args, **kwargs: {'access_token': 'temporary-test-token'})
    monkeypatch.setenv('ZF_CONTROLLER_CLIENT_SECRET', 'test-only-secret')
    context = SimpleNamespace(config=config, state_dir=state, project_root=root)
    for _ in range(4):
        status = synchronize(context)
        assert status['pending_records'] == 0
        if not status['collection_deferred']:
            break
    assert not status['collection_deferred']
    assert {m['payload']['kind'] for m in seen} == {'usage', 'project', 'heartbeat'}
    assert 'test-only-secret' not in json.dumps(seen)


def test_terminal_settled_cost_receipts_and_short_lifecycle_are_replayed_once(project):
    from zf.core.cost.tracker import CostTracker
    from zf.integrations.controller.terminal import collect_terminal
    from zf.web.terminal_registry import TerminalRegistry
    root, config, state = project
    collector = Collector(state, config)
    timestamp = datetime.now(timezone.utc).isoformat()
    registry = TerminalRegistry(state, project_id='local-project-id', project_root=root)
    registry.mutate(lambda value: value['sessions'].append({'session_id': 'term-a', 'generation': 1,
        'provider': 'codex', 'provider_session_id': 'native-a', 'state': 'stopped', 'created_at': timestamp,
        'stopped_at': datetime.now(timezone.utc).isoformat(), 'title': 'private-terminal-title'}))
    tracker = CostTracker(state / 'terminal-cost.jsonl')
    tracker.record_usage(role='web-terminal', instance_id='terminal:local-project-id:term-a:g1',
        input_tokens=70, output_tokens=20, cache_read_tokens=30, model='fixture-model',
        backend='codex', provider='openai', accounting_mode='subscription', occurred_at=timestamp, usage_sample_id='terminal-sample-a')
    context = SimpleNamespace(state_dir=state, project_root=root)
    collect_terminal(context, collector); collect_terminal(context, collector)
    batch = collector.outbox.pending(32)
    validate(batch)
    assert len(batch) == 3
    receipt = batch[-1]['payload']
    assert receipt['session_id'] == 'term-a' and receipt['provider_session_id'] == 'native-a'
    assert receipt['tokens']['cache_read_tokens'] == 30
    assert receipt['semantics'] == 'delta' and receipt['input_semantics'] == 'normalized_cost_v2'
    assert 'private-terminal-title' not in json.dumps(batch)


def test_project_configuration_can_return_to_a_previous_value(project):
    _, config, state = project
    collector = Collector(state, config)
    collector.project()
    config.project.name = 'Changed project name'
    collector.project()
    config.project.name = 'Controller fixture'
    collector.project()
    collector.project()
    messages = collector.outbox.pending(32)
    assert [row['payload']['name'] for row in messages] == ['Controller fixture', 'Changed project name', 'Controller fixture']
    assert messages[-1]['payload']['version'] > messages[0]['payload']['version']


def test_late_replayed_lifecycle_keeps_source_order(project):
    _, config, state = project
    collector = Collector(state, config)
    started = ZfEvent(type='agent.session.run.started', payload={'thread_id': 'session-a', 'run_id': 'turn-a'})
    completed = ZfEvent(type='agent.session.run.completed', payload={'thread_id': 'session-a', 'run_id': 'turn-a'})
    collector(completed)
    collector(started)
    sessions = [row for row in collector.outbox.pending(32) if row['payload']['kind'] == 'session']
    assert sessions[0]['payload']['version'] > sessions[1]['payload']['version']


def test_cancelled_turn_without_receipt_stays_missing(project):
    _, config, state = project
    log = event_log_from_project(state, config=config)
    stream = AgentSessionStreamEmitter(writer=EventWriter(log), identity=AgentSessionIdentity(
        run_id='cancelled-turn', thread_id='session-a', source='kanban', actor='helper'))
    stream.start()
    stream.cancel(reason='qualification cancellation')
    log.close()
    receipt = next(row['payload'] for row in Outbox(state, config.observability.controller).pending(32) if row['payload']['kind'] == 'usage')
    assert receipt['quality'] == 'missing' and receipt['tokens'] is None


def test_project_namespaces_prevent_cross_project_series_and_sample_collision(project):
    _, config, state = project
    first = Collector(state, config)
    other = replace(config, observability=replace(config.observability,
        controller=replace(config.observability.controller, project_id='project-b')))
    second = Collector(state.parent / 'other-project-state', other)
    event = usage(usage_sample_id='same-local-sample', usage_series_id='same-native-series', usage_semantics='cumulative')
    first(event); second(event)
    a, b = first.outbox.pending(32)[0]['payload'], second.outbox.pending(32)[0]['payload']
    assert a['record_id'] != b['record_id'] and a['series_id'] != b['series_id']
