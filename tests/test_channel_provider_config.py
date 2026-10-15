"""Explicit Channel budgets reach Codex without changing other sessions."""
import shlex

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import ChannelConfig, ZfConfig
from zf.runtime.channel_adapter import dispatch_reply_request
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_provider_config import channel_codex_backend
from tests.test_channel_prd_file import setup, request
from tests.test_channel_provider_retry_session import Backend


@pytest.mark.parametrize('value', ['true', '-1', '1000001', '"500000"', '1.5'])
def test_invalid_context_window_is_rejected(tmp_path, value):
    path=tmp_path/'zf.yaml'
    path.write_text("version: '1.0'\nproject:\n  name: review\nchannel:\n  codex_context_window_tokens: "+value+'\n')
    with pytest.raises(ConfigError, match='codex_context_window_tokens'):
        load_config(path)


def test_context_window_config_and_default_do_not_override_global_command(tmp_path, monkeypatch):
    path=tmp_path/'zf.yaml'
    path.write_text("version: '1.0'\nproject:\n  name: review\nchannel:\n  codex_context_window_tokens: 650000\n")
    assert load_config(path).channel.codex_context_window_tokens==650000
    monkeypatch.setenv('ZF_KANBAN_AGENT_CODEX_HEADLESS_CMD', 'codex --profile channel')
    configured=channel_codex_backend(650000)
    args=configured.build_args()
    assert 'model_context_window=650000' in args
    assert 'model_auto_compact_token_limit=585000' in args
    assert args[:3]==['codex','--profile','channel']
    assert channel_codex_backend().command=='codex --profile channel'
    assert ChannelConfig().codex_context_window_tokens==0


def test_dispatch_passes_project_budget_to_actual_codex_adapter_factory(tmp_path, monkeypatch):
    state,writer,service,file,body=setup(tmp_path,monkeypatch)
    writer.emit('channel.member.invited',actor='web',payload={'channel_id':'ch-file','member_id':'synth',
                'channel_role':'synthesizer','member_type':'provider_agent','backend':'codex',
                'permission_profile':'read_only'})
    service.config=ZfConfig(channel=ChannelConfig(semantic_source_max_chars=1_000_000,codex_context_window_tokens=650000))
    result=request(service,writer,file,body)
    assert result['ok'],result
    reply=project_channel(state,'ch-file')['reply_requests'][-1]
    adapter=Backend();adapter.command='codex'
    monkeypatch.setattr('zf.web.headless_agent.CodexHeadlessBackend',lambda:adapter)
    dispatch_reply_request(state_dir=state,writer=writer,request_id=reply['request_id'],channel_id='ch-file',
                           project_root=tmp_path,config=service.config,actor='test',source='test')
    assert len(adapter.calls)==1
    assert 'model_context_window=650000' in shlex.split(adapter.command)
    started=next(e for e in writer.event_log.read_all() if e.type=='channel.agent.reply.started' and e.payload['request_id']==reply['request_id'])
    assert started.payload['codex_context_window_tokens']==650000
    assert body.rstrip() in adapter.calls[0]['prompt'].replace('\\n','\n')
