"""Config-driven native CLI launches and receipts from a registered local installation."""

import json
from types import SimpleNamespace

import pytest
import yaml
from tests.test_controller_observation import project, usage, validate  # noqa: F401
from tests.test_controller_policy_health import settings
from tests.test_controller_workspace import workspace  # noqa: F401

from zf.core.config.controller_workspace import load_controller_workspace
from zf.integrations.controller.collector import Collector
from zf.integrations.controller.cpa_client import native_command
from zf.integrations.controller.runtime_settings import overlay
from zf.integrations.controller.workspace import observation
from zf.runtime.backend_session_reader import ClaudeSessionReader
from zf.runtime.provider_usage_reconciliation import build_disk_usage_event


@pytest.mark.parametrize('client,url_var', [('codex', 'ZF_CPA_CODEX_URL'), ('claude', 'ZF_CPA_CLAUDE_URL')])
def test_native_client_uses_only_its_bound_key(client, url_var, tmp_path):
    key_file = tmp_path / 'key'
    personal = {'ANTHROPIC_API_KEY': 'personal'}
    args, env = native_command(client, ['--version'], personal, key_file)
    assert args[1:] == ['--version'] and env == personal
    origin = 'https://cpa.example.test' + ('/v1' if client == 'codex' else '')
    with pytest.raises(FileNotFoundError):
        native_command(client, ['--version'], {url_var: origin}, key_file)
    key_file.write_text('fixture-workspace-key\n')
    args, env = native_command(client, ['--version'], {url_var: origin, **personal}, key_file)
    assert args[-1] == '--version' and 'fixture-workspace-key' not in ' '.join(args)
    if client == 'codex':
        assert 'model_providers.zaofu_cpa.base_url="' + origin + '"' in args
        assert 'model_providers.zaofu_cpa.env_key="ZF_CPA_INFERENCE_KEY"' in args
        assert env['ZF_CPA_INFERENCE_KEY'] == 'fixture-workspace-key'
    else:
        assert env['ANTHROPIC_BASE_URL'] == origin and env['ANTHROPIC_AUTH_TOKEN'] == 'fixture-workspace-key'
        assert 'ANTHROPIC_API_KEY' not in env


def test_preexisting_project_outbox_keeps_its_personal_mode(project):
    _, local, state = project
    first = Collector(state, local)
    sample = usage()
    first(sample)
    with first.outbox.connect() as db:
        db.execute('DROP TABLE provider_routes')  # On-disk schema before per-client routes.
    restored = Collector(state, local)
    assert restored.outbox.binding_at(sample.ts, 'anthropic')['mode'] == 'self_managed_cli'
    assert restored.outbox.pending(4) == first.outbox.pending(4)
    local.observability.controller._provider_modes = {'claude-code': 'managed_cpa'}
    with pytest.raises(ValueError, match='new deployment binding'):
        Collector(state, local)


def test_cloud_preflight_checks_only_the_selected_native_launcher(tmp_path, monkeypatch):
    from zf.integrations.controller import cpa_client
    from zf.runtime.preflight import _probe_provider_auth

    key = tmp_path / 'key'
    monkeypatch.setattr(cpa_client, 'KEY_FILE', key)
    monkeypatch.setenv('ZF_CPA_CLAUDE_URL', 'https://inference.example.test')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'unrelated-personal-key')
    monkeypatch.setattr('shutil.which', lambda name: str(cpa_client.WRAPPER_BIN / name))
    assert _probe_provider_auth('claude-headless') == (False, 'Workspace CPA inference credential unavailable')
    key.write_text('dedicated-test-key')
    assert _probe_provider_auth('claude-headless') == (True, 'Workspace CPA inference credential configured')


def test_installed_mixed_sources_are_selected_from_config_and_retained(workspace, project, tmp_path):
    config, peer, _ = workspace
    _, local, state = project
    raw = yaml.safe_load(config.path.read_text())
    cloud = raw['workspace']['controller']
    cloud.update(mode='self_managed_cli', allowed_modes=['managed_cpa', 'self_managed_cli'],
                 provider_modes={'claude-code': 'managed_cpa'}, cpa_request_namespace='pool')
    config.path.write_text(yaml.safe_dump(raw))
    config = load_controller_workspace(config.path)
    settings(peer, provider_modes={'claude-code': 'managed_cpa'})
    assert overlay(config, peer.policy).mode_for('claude-headless') == 'managed_cpa'
    settings(peer, provider_modes={'codex': 'managed_cpa'})
    with pytest.raises(ValueError, match='local consent'):
        overlay(config, peer.policy)
    assert config.mode_for('claude-headless') == 'managed_cpa'
    assert config.mode_for('codex-app-server') == 'self_managed_cli'
    local.observability.controller = observation(config, {'client_id': 'a', 'deployment_id': 'dep-a'}, 'project-a')
    collector = Collector(state, local)
    personal = usage(backend='codex-headless')
    collector(personal)
    request_id = 'req_1234567890abcdefXYZ'
    transcript = tmp_path / 'native-session.jsonl'
    transcript.write_text(json.dumps({'type': 'assistant', 'requestId': request_id, 'sessionId': transcript.stem,
        'timestamp': personal.ts, 'message': {'model': 'claude-fixture', 'usage': {'input_tokens': 12, 'output_tokens': 3}}}))
    report = ClaudeSessionReader().read_latest_usage(transcript)
    event = build_disk_usage_event(role=SimpleNamespace(instance_id='agent', backend='claude-code'),
        usage=report, config=None, usage_identity={'operation_id': 'op'})
    collector(event)
    validate(collector.outbox.pending(8))
    plain, shadow = [m['payload'] for m in collector.outbox.pending(8)]
    assert plain['mode'] == 'self_managed_cli' and plain['request_namespace'] is None
    assert (shadow['mode'], shadow['request_namespace'], shadow['source_request_id']) == ('managed_cpa', 'pool', request_id)
    assert (shadow['session_id'], shadow['operation_id'], shadow['provider']) == ('native-session', 'op', 'anthropic')
    local.observability.controller = observation(config, {'client_id': 'b', 'deployment_id': 'dep-b'}, 'project-a')
    local.observability.controller._provider_modes = {}
    local.observability.controller._cpa_request_namespace = ''
    newer = Collector(state, local)
    old = newer.outbox.binding_at(personal.ts, 'anthropic')
    assert old['deployment'] == 'dep-a' and old['mode'] == 'managed_cpa' and old['request_namespace'] == 'pool'
    cloud['allowed_modes'] = ['self_managed_cli']
    config.path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match='explicit locally allowed mode'):
        load_controller_workspace(config.path)
