from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_attachments import capture_channel_attachment, read_channel_attachment, channel_image_inputs
from zf.runtime.channel_context import build_channel_context_pack
from zf.runtime.channel_projection import project_channel
from zf.runtime.control_actions import ControlledActionService
from zf.web.server import create_app
from zf.web.headless_agent import ClaudeHeadlessBackend, CodexHeadlessBackend

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS1sAAAAASUVORK5CYII=')


def capture(state, **kwargs):
    return capture_channel_attachment(state, channel_id='ch-test', thread_id='main', name='image.png', mime='image/png', data=PNG, **kwargs)


def post(state, refs, message_id='m1'):
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    requested = writer.emit('web.action.requested', actor='web', payload={'action': 'channel-post-message'})
    service = ControlledActionService(state, writer, actor='web', source='channel', surface='web')
    return service.execute(action='channel-post-message', requested_action='channel.post_message', requested=requested,
                           payload={'channel_id': 'ch-test', 'thread_id': 'main', 'message_id': message_id,
                                    'member_id': 'operator', 'text': 'read images', 'refs': {'attachments': refs, 'dispatch_policy': 'trigger_only'}})


def test_upload_auth_scope_limits_and_message_link(tmp_path, monkeypatch):
    state = tmp_path / 'custom-state'
    writer = EventWriter(EventLog(state / 'events.jsonl'))
    writer.emit('channel.created', actor='web', payload={'channel_id': 'ch-test', 'name': 'test'})
    (state / 'kanban.json').write_text('[]')
    monkeypatch.setenv('ZF_WEB_ACTION_TOKEN', 'test-token')
    from zf.core.config.loader import load_config
    (tmp_path / 'zf.yaml').write_text('version: "1.0"\nproject:\n  name: attachment-test\n  state_dir: custom-state\n')
    client = TestClient(create_app(state, config=load_config(tmp_path / 'zf.yaml'), project_root=tmp_path))
    project = client.get('/api/workspace/projects').json()['server_default_project_id']
    url = f'/api/projects/{project}/channels/ch-test/attachments?name=l01.png&thread_id=main'
    headers = {'x-zf-web-token': 'test-token', 'content-type': 'image/png'}
    assert client.post(url, content=PNG).status_code == 403
    missing = client.post(url.replace('ch-test', 'absent'), content=PNG, headers=headers)
    assert missing.status_code == 404, missing.text
    assert client.post(url, content=b'not an image', headers=headers).status_code == 422
    assert client.post(url.replace('l01.png', '../escape'), content=PNG, headers=headers).status_code == 422
    assert client.post(url, content=b'x' * (10 * 1024 * 1024 + 1), headers=headers).status_code == 413
    response = client.post(url, content=PNG, headers=headers)
    assert response.status_code == 201, response.text
    ref = response.json()['attachment']
    assert read_channel_attachment(state, ref, channel_id='ch-test', thread_id='main')[1] == PNG
    assert not any(e.type == 'channel.attachment.uploaded' for e in EventLog(state / 'events.jsonl').read_all())
    assert post(state, [{**ref, 'source': 'browser-file-picker'}])['status'] == 'posted'
    events = EventLog(state / 'events.jsonl').read_all()
    uploaded = next(e for e in events if e.type == 'channel.attachment.uploaded')
    assert uploaded.payload['hash'] == ref['hash']
    assert 'data_base64' not in (state / 'events.jsonl').read_text()


@pytest.mark.parametrize('change', [{'hash': 'bad'}, {'size': 3}, {'name': 'other.png'}, {'mime': 'image/jpeg'}, {'uri': 'channel://attachments/../../escape'}])
def test_ingress_rejects_forged_refs_before_message(tmp_path, change):
    ref = {**capture(tmp_path), **change}
    assert post(tmp_path, [ref])['status'] == 'message_rejected'
    assert not any(e.type in {'channel.message.posted', 'channel.attachment.uploaded'} for e in EventLog(tmp_path / 'events.jsonl').read_all())


def test_missing_cross_scope_tamper_and_symlink(tmp_path):
    ref = capture(tmp_path)
    for channel, thread in [('other', 'main'), ('ch-test', 'other')]:
        with pytest.raises(ValueError, match='another channel/thread'):
            read_channel_attachment(tmp_path, ref, channel_id=channel, thread_id=thread)
    with pytest.raises(ValueError, match='missing'):
        read_channel_attachment(tmp_path / 'other-project', ref, channel_id='ch-test', thread_id='main')
    path = tmp_path / ref['body_ref']['ref']
    raw = path.read_bytes()
    path.write_bytes(raw + b' ')
    assert post(tmp_path, [ref])['status'] == 'message_rejected'
    path.unlink()
    target = tmp_path / 'elsewhere'; target.write_bytes(raw); path.symlink_to(target)
    assert post(tmp_path, [ref])['status'] == 'message_rejected'


def test_metadata_only_browser_attachment_rejected(tmp_path):
    ref = {'name': 'old.png', 'type': 'image/png', 'size': 20, 'source': 'browser-file-picker'}
    response = post(tmp_path, [ref])
    assert response['status'] == 'message_rejected'
    assert 'upload' in json.dumps(response)


def test_five_images_deduplicate_and_reach_followup_provider(tmp_path):
    refs = [capture_channel_attachment(tmp_path, channel_id='ch-test', thread_id='main', name=f'l0{i}.png', mime='image/png', data=PNG) for i in range(1, 6)]
    assert post(tmp_path, refs)['status'] == 'posted'
    assert post(tmp_path, [], 'followup')['status'] == 'posted'
    channel = project_channel(tmp_path, 'ch-test')
    pack = build_channel_context_pack(channel, channel_id='ch-test', thread_id='main', target_member_id='designer', trigger_message_id='followup')
    assert len(pack['artifact_refs']) == 5
    channel['context_packs'] = {'pack': pack}
    images = channel_image_inputs(tmp_path, channel=channel, request={'context_pack_id': 'pack'})
    assert [i['name'] for i in images] == [f'l0{i}.png' for i in range(1, 6)]
    assert all(base64.b64decode(i['data']) == PNG for i in images)
    # Exercise the real runtime caller, not just the image packing helper.
    from zf.runtime.channel_adapter import _run_headless_reply
    from zf.web.headless_agent import HeadlessTurnResult
    calls = []
    class Backend:
        def available(self): return True
        def run_turn(self, **kw):
            calls.append(kw)
            return HeadlessTurnResult(ok=True, status='completed', backend='codex-headless', thread_id=kw['thread_id'], provider_session_id='native', reply='Read five images', messages=[], usage={})
    _run_headless_reply(state_dir=tmp_path, project_root=tmp_path, writer=EventWriter(EventLog(tmp_path / 'events.jsonl')),
        channel=channel, member={'member_id': 'designer', 'backend': 'codex', 'permission_profile': 'read_only'},
        message={'text': 'read all images'}, request={'context_pack_id': 'pack', 'thread_id': 'main'},
        request_id='r1', started_event_id='start', actor='test', source='test', backend='codex-headless', backends={'codex-headless': Backend()})
    assert calls and calls[0]['image_inputs'] == images


@pytest.mark.parametrize('backend', ['codex', 'claude'])
def test_native_provider_image_wire(tmp_path, monkeypatch, backend):
    # Existing fake provider processes exercise actual subprocess serialization.
    from tests.test_web_headless_agent import _fake_codex_script, _fake_claude_script
    output = tmp_path / 'input.json'
    images = [{'name': 'l01.png', 'mime': 'image/png', 'data': base64.b64encode(PNG).decode()}]
    if backend == 'codex':
        script = _fake_codex_script(tmp_path)
        source = script.read_text().replace("if expected_thinking:\n            assert msg['params']['effort']", "open(" + repr(str(output)) + ", 'w').write(json.dumps(msg['params']))\n        if expected_thinking:\n            assert msg['params']['effort']")
        script.write_text(source)
        monkeypatch.setenv('FAKE_CODEX_SANDBOX', 'danger-full-access')
        adapter = CodexHeadlessBackend(command=f'{sys.executable} {script}')
    else:
        script = _fake_claude_script(tmp_path)
        monkeypatch.setenv('FAKE_CLAUDE_CAPTURE', str(output))
        adapter = ClaudeHeadlessBackend(command=f'{sys.executable} {script}')
    result = adapter.run_turn(prompt='describe', image_inputs=images, cwd=tmp_path, system_prompt='', thread_id='thread', provider_session_id='', on_session_id=lambda _: None, on_message=None, timeout_s=5)
    assert result.ok, result.error
    sent = json.loads(output.read_text())
    if backend == 'codex':
        assert sent['input'][1] == {'type': 'image', 'url': 'data:image/png;base64,' + images[0]['data']}
    else:
        assert sent['message']['content'][1]['source'] == {'type': 'base64', 'media_type': 'image/png', 'data': images[0]['data']}
