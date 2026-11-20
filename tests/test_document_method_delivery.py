"""Document methods reach actual provider prompts in isolated product roots."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, SkillSourceConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.writer import EventWriter
from zf.core.events.model import ZfEvent
from zf.runtime.channel_context import build_channel_context_pack
from zf.runtime.channel_provider_prompt import build_channel_provider_prompt
from zf.runtime.channel_skill_context import load_channel_skill_guidance
from zf.runtime.channel_skill_materialization import _materialize_channel_skill_refs
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.sidecar_refs import SidecarRefError
from zf.web.headless_agent import KanbanHeadlessAgent, HeadlessTurnResult
from zf.web.headless_methods import document_method_context

ROOT = Path(__file__).resolve().parents[1]
METHOD = ROOT / 'skills/zf-document-reading/SKILL.md'


class CaptureBackend:
    def available(self):
        return True

    def run_turn(self, **kwargs):
        self.call = kwargs
        return HeadlessTurnResult(ok=True, status='completed', backend='codex-headless',
            thread_id=kwargs['thread_id'], provider_session_id='', reply='captured', messages=[], usage={})


def test_isolated_kanban_actual_backend_gets_complete_method(tmp_path):
    backend = CaptureBackend()
    agent = KanbanHeadlessAgent(state_dir=tmp_path/'custom-state', project_root=tmp_path,
        backends={'codex-headless': backend})
    result = agent.run_turn(backend='codex-headless', message='生成 PRD', context={})
    assert result.ok, result.error
    method = json.loads(document_method_context().split('\n', 1)[1])
    assert method['content'] == METHOD.read_text()
    assert method['sha256'] == hashlib.sha256(METHOD.read_bytes()).hexdigest()
    assert document_method_context() in backend.call['prompt']
    assert not (tmp_path/'skills').exists()
    assert not (tmp_path/'.codex').exists()


@pytest.mark.parametrize('skill,phase', [
    ('zf-channel-discussion-synthesizer', {'synthesis_request_id':'s1'}),
    ('zf-channel-discussion-participant', {'consensus_review_id':'r1'}),
])
def test_invitation_to_typed_prompt_delivers_dependency(tmp_path, monkeypatch, skill, phase):
    state = tmp_path/'custom-state'
    log = EventLog(state/'events.jsonl')
    service = ControlledActionService(state, EventWriter(log), project_root=tmp_path,
        config=ZfConfig(project=ProjectConfig(name='test', state_dir='custom-state')))
    def execute(action, payload):
        return service._execute_action(requested=ZfEvent(type='control.action.requested', actor='web', payload=payload),
            action=action, requested_action=action, payload=payload)
    assert execute('channel-create', {'channel_id':'review', 'name':'review'}).get('ok')
    result = execute('channel-invite-member', {'channel_id':'review', 'member_id':'reviewer',
        'provider':'codex', 'channel_role':'critic', 'skill_refs':[skill]})
    assert result.get('ok'), result
    member = next(e.payload for e in log.read_all() if e.type=='channel.member.invited')
    refs = member['resolved_skill_refs']
    channel = {'channel_id':'review', 'messages':[{'message_id':'trigger', 'thread_id':'main',
        'member_id':'operator', 'role':'user', 'text':'review', 'refs':phase}]}
    pack = build_channel_context_pack(channel, channel_id='review', thread_id='main',
        target_member_id='reviewer', trigger_message_id='trigger', state_dir=state,
        project_root=tmp_path, resolved_skill_refs=refs)
    methods = [i for i in pack['skill_guidance'] if i['logical_ref']=='skills/zf-document-reading/SKILL.md']
    assert len(methods)==1
    assert methods[0]['content']==METHOD.read_text()
    monkeypatch.setattr('zf.runtime.channel_provider_prompt.channel_context_pack_by_id',lambda *a,**k:pack)
    prompt = build_channel_provider_prompt(channel=channel,member=member,
        request={'context_pack_id':pack['context_pack_id']},message={'refs':phase},state_dir=state)
    delivered = ast.literal_eval(next(line for line in prompt.splitlines() if line.startswith("context_pack: ")).removeprefix("context_pack: "))
    assert methods[0] in delivered["skill_guidance"]
    assert 'Do not call tools' in prompt
    dep = next(i for i in refs if i['logical_ref']=='skills/zf-document-reading/SKILL.md')
    Path(dep['resolved_path']).write_text('tampered')
    with pytest.raises(SidecarRefError, match='differs'):
        load_channel_skill_guidance(refs,state_dir=state,project_root=tmp_path)


def test_dependency_upgrade_cycles_dedup_and_missing(tmp_path):
    source = tmp_path/'source'
    def write(name, text, deps):
        p=source/name/'SKILL.md'; p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(f'---\nname: {name}\ndescription: test\ndependencies: {deps}\n---\n{text}')
    write('parent','parent',['child','child'])
    write('child','original',['parent'])
    config=ZfConfig(skill_sources=[SkillSourceConfig(name='test',path=str(source))])
    args=dict(project_root=tmp_path/'product',state_dir=tmp_path/'state',config=config)
    missing,before=_materialize_channel_skill_refs(['skills/parent/SKILL.md'],**args)
    assert not missing
    assert len(before)==2
    write('child','upgraded',['parent'])
    missing,after=_materialize_channel_skill_refs(['skills/parent/SKILL.md'],**args)
    assert not missing
    assert Path(before[1]['resolved_path']).read_text().endswith('original')
    assert Path(after[1]['resolved_path']).read_text().endswith('upgraded')
    (source/'child/SKILL.md').unlink()
    missing,_=_materialize_channel_skill_refs(['skills/parent/SKILL.md'],**args)
    assert missing==['skills/child/SKILL.md']
    with pytest.raises(SidecarRefError, match='budget'):
        load_channel_skill_guidance(before,state_dir=args['state_dir'],project_root=args['project_root'],max_bytes=1)


def test_headless_method_fails_closed_when_missing_or_oversized(tmp_path,monkeypatch):
    monkeypatch.setattr('zf.web.headless_methods.resolve_builtin_skill_source',lambda name:None)
    with pytest.raises(ValueError,match='unavailable'):
        document_method_context()
    p=tmp_path/'SKILL.md'; p.write_bytes(b'x'*65537)
    monkeypatch.setattr('zf.web.headless_methods.resolve_builtin_skill_source',lambda name:p)
    with pytest.raises(ValueError,match='64 KiB'):
        document_method_context()
