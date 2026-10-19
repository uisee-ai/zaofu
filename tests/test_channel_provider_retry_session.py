from pathlib import Path
import pytest
from zf.core.events import EventLog,EventWriter
from zf.runtime.channel_adapter import _run_headless_reply
from zf.runtime.channel_reply_prompt import fake_channel_reply_text
from zf.web.headless_agent import HeadlessTurnResult

class Backend:
    def __init__(self): self.calls=[]
    def available(self): return True
    def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        sid=f'native-{len(self.calls)}';kwargs['on_session_id'](sid)
        failed=len(self.calls)==1
        reply='' if failed else fake_channel_reply_text({'member_id':'synthesizer'},{'refs':{'synthesis_request_id':'synth-1'}})
        return HeadlessTurnResult(ok=not failed,status='timeout' if failed else 'completed',backend='codex-headless',thread_id=kwargs['thread_id'],provider_session_id=sid,reply=reply,messages=[],usage={},error='total deadline' if failed else '')

@pytest.mark.parametrize('typed',[True,False])
def test_transport_retry_isolates_failed_typed_history_but_keeps_conversation(tmp_path:Path,typed):
    state=tmp_path/'state';writer=EventWriter(EventLog(state/'events.jsonl'));backend=Backend()
    message={'text':'Synthesize complete evidence','refs':{'synthesis_request_id':'synth-1'} if typed else {}}
    for generation in [1,2]:
        request={'request_id':'reply-1','target_member_id':'synthesizer','thread_id':'main','message_id':'m1','run_generation':generation}
        _run_headless_reply(state_dir=state,project_root=tmp_path,writer=writer,channel={'channel_id':'ch-1'},member={'member_id':'synthesizer','backend':'codex','channel_role':'synthesizer','permission_profile':'read_only'},message=message,request=request,request_id='reply-1',started_event_id=f'start-{generation}',actor='test',source='test',backend='codex-headless',backends={'codex-headless':backend})
    assert len(backend.calls)==2
    public_runs = [event.payload for event in EventLog(state/'events.jsonl').read_all()
                   if event.type == 'agent.session.run.started']
    assert len(public_runs) == 2
    for call, public in zip(backend.calls, public_runs):
        assert call['run_id'] == public['run_id']
        assert call['run_thread_id'] == public['thread_id']
        assert call['project_id'] == public['project_id']
    assert backend.calls[0]['provider_session_id']==''
    assert backend.calls[1]['provider_session_id']==('' if typed else 'native-1')
    assert (backend.calls[0]['run_thread_id']!=backend.calls[1]['run_thread_id']) is typed


def test_contract_repair_never_reuses_another_requests_native_attempt(tmp_path):
 state=tmp_path/'state';writer=EventWriter(EventLog(state/'events.jsonl'));backend=Backend()
 for request_id,generation,repair_id in [('reply-1',1,''),('reply-1',2,''),('reply-repair',1,'repair-1')]:
  message={'text':'Synthesize complete evidence','refs':{'synthesis_request_id':'synth-1'}}
  if repair_id:message['refs']['synthesis_repair_id']=repair_id
  request={'request_id':request_id,'target_member_id':'synthesizer','thread_id':'main','message_id':request_id,'run_generation':generation}
  _run_headless_reply(state_dir=state,project_root=tmp_path,writer=writer,channel={'channel_id':'ch-1'},member={'member_id':'synthesizer','backend':'codex','channel_role':'synthesizer','permission_profile':'read_only'},message=message,request=request,request_id=request_id,started_event_id=f'start-{request_id}-{generation}',actor='test',source='test',backend='codex-headless',backends={'codex-headless':backend})
 assert len(backend.calls)==3
 assert backend.calls[2]['provider_session_id']=='', 'repair generation 1 resumed failed original generation 1 session'
 assert len({call['run_thread_id'] for call in backend.calls})==3


def test_blind_pack_never_resumes_conversation_or_another_blind_round(tmp_path):
    state = tmp_path / "state"
    writer = EventWriter(EventLog(state / "events.jsonl"))
    backend = Backend()
    for index, pack_id in enumerate(["", "blind-1", "blind-2"]):
        channel = {"channel_id": "ch-1", "context_packs": [
            {"context_pack_id": pack_id, "semantic_source_stage": "blind_contribution"}
        ] if pack_id else []}
        request = {"request_id": f"reply-{index}", "target_member_id": "critic", "thread_id": "main",
                   "message_id": f"m-{index}", "context_pack_id": pack_id}
        _run_headless_reply(state_dir=state, project_root=tmp_path, writer=writer, channel=channel,
            member={"member_id": "critic", "backend": "codex", "permission_profile": "read_only"},
            message={"text": "independent review"}, request=request, request_id=request["request_id"],
            started_event_id=f"start-{index}", actor="test", source="test", backend="codex-headless",
            backends={"codex-headless": backend})
    assert len(backend.calls) == 3
    assert all(call["provider_session_id"] == "" for call in backend.calls)
    assert len({call["run_thread_id"] for call in backend.calls}) == 3
    assert "Do not call tools" in backend.calls[-1]["prompt"]
