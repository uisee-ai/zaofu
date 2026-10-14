"""Kanban approval -> project config -> immutable Channel member -> contribution."""
from dataclasses import asdict
import json
from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_discussion import start_discussion
from zf.runtime.channel_profile_registration import ACTION, add_profile_yaml, digest_text, registration_context
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_provider_prompt import build_channel_provider_prompt
from zf.runtime.channel_adapter import dispatch_pending_replies
from zf.runtime.control_actions import ControlledActionService
from zf.web.proposal_extraction import extract_action_proposal


def setup(tmp_path, monkeypatch, *, envelope=False):
    spec = {'project': {'name': 'profile-test', 'state_dir': str(tmp_path/'state')},
            'roles': [], 'channel': {'semantic_source_max_chars': 120000,
                'agent_profiles': {'test-provider': {'provider': 'fake', 'backend': 'fake', 'channel_role': 'critic'}}}}
    if envelope:
        spec = {'apiVersion': 'zaofu.dev/v1', 'kind': 'ZfConfig', 'metadata': {'name': 'profiles'}, 'spec': spec}
    path = tmp_path/'zf.yaml'
    path.write_text('# Preserve this comment\n'+yaml.safe_dump(spec, sort_keys=False))
    config = load_config(path)
    writer = EventWriter(EventLog(tmp_path/'state/events.jsonl'))
    service = ControlledActionService(tmp_path/'state', writer, project_root=tmp_path, config=config, actor='web')
    monkeypatch.setattr('zf.runtime.channel_message_ingress.dispatch_channel_replies_background', lambda **kwargs: None)
    return path, writer, service


def payload(path):
    return {'profile_id': 'ui-ux', 'profile': {'display_name': 'UI/UX 设计师', 'persona': 'Design accessible interfaces',
        'channel_role': 'ui_ux_designer', 'provider': 'fake', 'backend': 'fake',
        'role_context_ref': 'channel_roles/ui-ux.md', 'skill_refs': ['skills/zf-channel-discussion-participant/SKILL.md'],
        'visibility_ceiling': 'reviewer', 'permission_ceiling': 'read_only'},
        'role_markdown': '# UI/UX\nReview page maps, layouts and empty/error/permission states. Preserve approved product rules.\n',
        'base_config_digest': digest_text(path.read_text()), 'reason': 'Missing independent UI perspective', 'source_refs': ['PRD.md']}


def approve(service, writer, body, *, proposal=None):
    proposal = proposal or writer.emit('operator.action.proposed', actor='kanban-agent', payload={'proposal': {
        'action': ACTION, 'requested_action': ACTION, 'valid': True, 'payload': body, 'reason': body['reason']}})
    request = writer.emit('web.action.requested', actor='web', payload={'action': ACTION})
    result = service.execute(action=ACTION, requested_action=ACTION,
        payload={**body, 'proposal_event_id': proposal.id}, requested=request)
    return result, proposal


@pytest.mark.parametrize('envelope', [False, True])
def test_registration_uses_one_config_and_preserves_unrelated_yaml(tmp_path, monkeypatch, envelope):
    path, writer, service = setup(tmp_path, monkeypatch, envelope=envelope)
    old = path.read_text()
    body = payload(path)
    answer = json.dumps({'action_proposal': {'action': ACTION, 'payload': body, 'reason': body['reason']}})
    proposal = extract_action_proposal(answer)
    assert proposal and proposal['valid'], proposal
    assert path.read_text() == old  # A generated candidate never registers itself.
    result, event = approve(service, writer, body)
    assert result['ok'], result
    assert path.read_text().startswith('# Preserve this comment\n')
    config = load_config(path)
    assert config.channel.agent_profiles['ui-ux'].channel_role == 'ui_ux_designer'
    assert 'fake' in registration_context(tmp_path, service.state_dir, config)['providers']
    assert (tmp_path/'channel_roles/ui-ux.md').read_text() == body['role_markdown']
    repeated, _ = approve(service, writer, body, proposal=event)
    assert repeated['ok']
    assert len([e for e in writer.event_log.read_all() if e.type == 'channel.profile.registered']) == 1


@pytest.mark.parametrize('fault', ['base', 'skill', 'provider', 'permission', 'role_path', 'secret', 'existing_role', 'no_approval'])
def test_invalid_registration_does_not_modify_source(tmp_path, monkeypatch, fault):
    path, writer, service = setup(tmp_path, monkeypatch)
    body = payload(path)
    if fault == 'base': body['base_config_digest'] = '0'*64
    if fault == 'skill': body['profile']['skill_refs'] = ['skills/not-installed/SKILL.md']
    if fault == 'provider': body['profile']['provider'] = body['profile']['backend'] = 'codex'
    if fault == 'permission': body['profile']['permission_ceiling'] = 'dangerous_full'
    if fault == 'role_path': body['profile']['role_context_ref'] = '../outside.md'
    if fault == 'secret': body['role_markdown'] = 'api_key=sk-' + 'a'*30
    if fault == 'existing_role':
        (tmp_path/'channel_roles').mkdir()
        (tmp_path/'channel_roles/ui-ux.md').write_text('Existing role')
    old = path.read_text()
    if fault == 'no_approval':
        request = writer.emit('web.action.requested', actor='web', payload={'action': ACTION})
        result = service.execute(action=ACTION, requested_action=ACTION, payload=body, requested=request)
    else: result, _ = approve(service, writer, body)
    assert not result['ok'], result
    assert path.read_text() == old
    assert not [e for e in writer.event_log.read_all() if e.type == 'channel.profile.registered']


def origin(service, writer):
    request = writer.emit('web.action.requested', actor='web', payload={})
    result = service.execute(action='channel-create-from-template', requested_action='channel-create-from-template',
        payload={'channel_id': 'ch-review', 'template_id': 'prd-clarification', 'name': 'Review',
                 'mode': 'multi_lens', 'overrides': {'backend': 'fake'}}, requested=request)
    assert result['ok'], result
    writer.emit('channel.message.posted', actor='web', payload={'channel_id': 'ch-review', 'thread_id': 'original',
        'message_id': 'requirement', 'member_id': 'operator', 'role': 'user', 'text': 'Review page layout for this PRD.'})
    start_discussion(writer, project_channel(service.state_dir, 'ch-review'), actor='web', channel_id='ch-review',
        thread_id='original', trigger_message_id='requirement', trigger='explicit', source='test')


def test_register_and_admit_binds_custom_role_and_actual_context(tmp_path, monkeypatch):
    path, writer, service = setup(tmp_path, monkeypatch)
    origin(service, writer)
    body = payload(path) | {'channel_id': 'ch-review', 'origin_thread_id': 'original', 'member_id': 'ui_designer',
        'objective': 'Map the PRD to page layouts and permission/error states.', 'required_reviewer': True,
        'leader_member_id': 'product_pm', 'leader_revision': 1}
    result, _ = approve(service, writer, body)
    assert result['ok'], result
    d = project_channel(service.state_dir, 'ch-review')
    ui = next(m for m in d['members'] if m['member_id'] == 'ui_designer')
    assert ui['channel_role'] == 'ui_ux_designer'
    assert ui['profile_provenance'] == 'project_catalog' and ui['permission_profile'] == 'read_only'
    session = d['discussions']['original']
    assert session['requirement_message_id'] == 'requirement'
    assert 'ui_designer' in session['review_roster']
    request = next(r for r in d['reply_requests'] if r['target_member_id'] == 'ui_designer')
    message = next(m for m in d["messages"] if m["message_id"] == request["message_id"])
    prompt = build_channel_provider_prompt(channel=d, request=request, member=ui,
                                           message=message, state_dir=service.state_dir)
    assert 'UI/UX' in prompt and 'page layouts' in prompt
    dispatch_pending_replies(state_dir=service.state_dir, writer=writer, channel_id='ch-review', actor='test',
        source='test', max_dispatch=20, allow_queued=True, project_root=tmp_path, config=service.config)
    d = project_channel(service.state_dir, 'ch-review')
    assert any(c['member_id'] == 'ui_designer' and c.get('artifact_ref') for c in d['contributions'])


def test_recovery_after_config_commit_without_registered_event(tmp_path, monkeypatch):
    path, writer, service = setup(tmp_path, monkeypatch)
    body = payload(path)
    original = writer.emit
    def crash(event_type, **kwargs):
        if event_type == 'channel.profile.registered': raise OSError('interrupted after config write')
        return original(event_type, **kwargs)
    monkeypatch.setattr(writer, 'emit', crash)
    result, proposal = approve(service, writer, body)
    assert not result['ok'] and 'ui-ux' in load_config(path).channel.agent_profiles
    monkeypatch.setattr(writer, 'emit', original)
    result, _ = approve(service, writer, body, proposal=proposal)
    assert result['ok'], result
    assert len([e for e in writer.event_log.read_all() if e.type == 'channel.profile.registered']) == 1


def test_yaml_insert_preserves_other_documents_and_empty_mapping():
    profile = {'persona': 'reviewer'}
    for text in ('project: {name: demo}\nchannel: {}\n',
                 'kind: ZfConfig\nspec:\n  project: {name: demo}\n---\nkind: SchemaProfile\nmetadata: {name: other}\n'):
        updated = add_profile_yaml(text, 'ui-ux', profile)
        docs = list(yaml.safe_load_all(updated))
        target = docs[0].get('spec', docs[0])
        assert target['channel']['agent_profiles']['ui-ux'] == profile
        if len(docs) > 1: assert docs[1] == list(yaml.safe_load_all(text))[1]


def test_web_registration_requires_token_and_exact_proposal(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from zf.web.server import create_app
    path, writer, service = setup(tmp_path, monkeypatch)
    monkeypatch.setenv('ZF_WEB_ACTION_TOKEN', 'profile-test-token')
    body = payload(path)
    proposal = writer.emit('operator.action.proposed', actor='kanban-agent', payload={'proposal': {
        'action': ACTION, 'requested_action': ACTION, 'valid': True, 'payload': body, 'reason': body['reason']}})
    with TestClient(create_app(service.state_dir, config=service.config, project_root=tmp_path)) as client:
        url = '/api/actions/channel-profile-register'
        assert client.post(url, json=body).status_code in {401, 403}
        tampered = body | {'reason': 'different approval'}
        response = client.post(url, headers={'x-zf-web-token': 'profile-test-token'},
            json=tampered | {'proposal_event_id': proposal.id})
        assert not response.json()['ok']
        response = client.post(url, headers={'x-zf-web-token': 'profile-test-token'},
            json=body | {'proposal_event_id': proposal.id})
        assert response.json()['ok'], response.text
        assert 'ui-ux' in load_config(path).channel.agent_profiles


@pytest.mark.parametrize('fault', ['actor', 'thread', 'leader', 'existing_member'])
def test_origin_rejected_before_config_write(tmp_path, monkeypatch, fault):
    path, writer, service = setup(tmp_path, monkeypatch)
    origin(service, writer)
    body = payload(path) | {'channel_id': 'ch-review', 'origin_thread_id': 'original', 'member_id': 'ui_designer',
        'objective': 'Review layouts', 'required_reviewer': True, 'leader_member_id': 'product_pm', 'leader_revision': 1}
    if fault == 'actor': service.actor = 'stranger'
    if fault == 'thread': body['origin_thread_id'] = 'foreign'
    if fault == 'leader': body['leader_revision'] = 2
    if fault == 'existing_member': body['member_id'] = 'arch'
    old = path.read_text()
    result, _ = approve(service, writer, body)
    assert not result['ok'], result
    assert path.read_text() == old


def test_member_snapshot_does_not_follow_mutable_role_prompt(tmp_path, monkeypatch):
    path, writer, service = setup(tmp_path, monkeypatch)
    origin(service, writer)
    body = payload(path) | {'channel_id': 'ch-review', 'origin_thread_id': 'original', 'member_id': 'ui_designer',
        'objective': 'Review layouts', 'required_reviewer': True, 'leader_member_id': 'product_pm', 'leader_revision': 1}
    result, _ = approve(service, writer, body)
    assert result['ok'], result
    d = project_channel(service.state_dir, 'ch-review')
    ui = next(m for m in d['members'] if m['member_id'] == 'ui_designer')
    from zf.runtime.channel_profiles import load_channel_profile_role_definition
    frozen = load_channel_profile_role_definition(service.state_dir, ui)
    (tmp_path/'channel_roles/ui-ux.md').write_text('Changed roles must not affect an existing binding')
    assert load_channel_profile_role_definition(service.state_dir, ui) == frozen
