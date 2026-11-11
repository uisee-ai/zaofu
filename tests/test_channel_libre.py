"""Caller-level free discussion routing, publication and immutable PRD inputs."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_router import route_channel_message
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_libre import advance_libre, round_ledger
from zf.runtime.channel_question_dedup import apply_question_dedup_reply
from zf.runtime.channel_question_graph import owner_questionnaire
from zf.runtime.channel_prd_snapshot import capture_prd_input, snapshot_channel
from zf.runtime.control_actions import ControlledActionService


@pytest.fixture
def world(tmp_path):
    state = tmp_path / "runtime"
    writer = EventWriter(EventLog(state / "events.jsonl"))
    writer.emit("channel.created", actor="web", correlation_id="ch-free", payload={
        "channel_id": "ch-free", "name": "自由讨论", "discussion_mode": "libre_discuss",
        "leader_member_id": "pm", "default_responder_id": "pm", "owner_actor_ref": "web"})
    for member, role in (("pm", "product_pm"), ("arch", "arch"), ("synth", "synthesizer")):
        writer.emit("channel.member.invited", actor="web", correlation_id="ch-free", payload={
            "channel_id": "ch-free", "member_id": member, "channel_role": role,
            "member_type": "provider_agent", "backend": "fake", "provider": "fake",
            "permissions": ["read", "message", "summarize"]})
    writer.emit("channel.discussion.mode.set", actor="web", correlation_id="ch-free", payload={
        "channel_id": "ch-free", "mode": "libre_discuss", "default_responder_id": "pm",
        "synthesizer": "synth", "participants": ["pm", "arch", "synth"]})
    return state, writer


def post(world, mid="root", mentions=None, text="做一个轻量待办清单", member="operator", role="user"):
    state, writer = world
    payload = {"channel_id": "ch-free", "thread_id": "main", "message_id": mid, "member_id": member,
               "role": role, "text": text, "mentions": mentions or [], "source": "web"}
    event = writer.emit("channel.message.posted", actor="web", correlation_id="ch-free", payload=payload)
    result = route_channel_message(state_dir=state, writer=writer, message_event=event, message_payload=payload,
                                   actor="web", source="web", dispatch_inline=False, project_root=state.parent)
    return result


def complete(world, member, questions=(), *, root="root", relay=None, fact_resolutions=None):
    state, writer = world
    channel = project_channel(state, "ch-free")
    request = next(r for r in channel["reply_requests"] if r["target_member_id"] == member and r["message_id"] == root)
    message = next(m for m in channel["messages"] if m["message_id"] == root)
    event = writer.emit("channel.agent.reply.completed", actor=member, correlation_id="ch-free", payload={
        "channel_id": "ch-free", "thread_id": "main", "request_id": request["request_id"],
        "message_id": root, "target_member_id": member, "text": "已阅读", "source": "test"})
    body = {"summary": "我的建议", "questions": list(questions), **({"libre_relay": relay} if relay else {})}
    if fact_resolutions is not None:
        pack = next(p for p in channel["context_packs"] if p.get("context_pack_id") == request.get("context_pack_id"))
        body.update(fact_resolutions=fact_resolutions, consumed_message_digests=pack.get("semantic_source_required_digests", []))
    emit_structured_reply_events(state_dir=state, writer=writer, channel=project_channel(state, "ch-free"),
                                 request=request, message=message, reply=json.dumps({"channel_contribution": body},ensure_ascii=False),
                                 reply_event_id=event.id, actor=member, source="test")


def question(text, **more):
    return {"question": text, "recommended_answer": "建议保留", "why_it_matters": "决定产品行为", **more}


def test_new_creation_is_quiet_and_legacy_missing_mode_is_unchanged(tmp_path):
    state = tmp_path / "runtime"
    writer = EventWriter(EventLog(state / "events.jsonl"))
    service = ControlledActionService(state, writer, project_root=tmp_path)
    payload = {"name": "静群", "channel_id": "new"}
    event = writer.emit("web.action.requested", actor="web", payload={"action": "channel-create", "request": payload})
    assert service.execute(action="channel-create", requested_action="channel-create", requested=event, payload=payload)["ok"]
    assert project_channel(state, "new")["discussion"]["mode"] == "libre_discuss"
    assert not any(e.type == "channel.message.posted" for e in writer.event_log.read_all())
    writer.emit("channel.created", actor="old", payload={"channel_id": "old", "name": "旧群"})
    assert project_channel(state, "old")["discussion"]["mode"] == "conversation"


@pytest.mark.parametrize("mentions,expected", [(None,["pm"]),(["arch"],["arch"]),(["all"],["pm","arch","synth"])])
def test_human_targets_and_agent_text_cannot_expand_them(world, mentions, expected):
    assert set(post(world, mentions=mentions).targets) == set(expected)
    state, writer = world
    result = post(world, mid="agent", mentions=["all"], member="arch", role="user")
    assert not result.reply_requests
    assert not any(e.type == "channel.discussion.started" for e in writer.event_log.read_all())


def test_same_text_different_options_not_silently_collapsed(world):
    post(world, mentions=["arch"])
    complete(world, "arch", [question("是否保留数据？", options=[{"id":"yes", "label":"刷新保留"},{"id":"no", "label":"不保留"}]),
                             question("是否保留数据？", options=[{"id":"yes", "label":"关闭重开保留"},{"id":"no", "label":"不保留"}])])
    channel = project_channel(world[0], "ch-free")
    assert len(channel["open_questions"]) == 2
    assert len(owner_questionnaire(channel, thread_id="main")) == 2
    assert not channel["question_dedup_requests"]


def prepare_dedup(world):
    post(world, mentions=["all"])
    complete(world, "pm", [question("刷新后是否保留？"), question("关闭浏览器重开是否保留？")])
    assert not owner_questionnaire(project_channel(world[0], "ch-free"), thread_id="main")
    complete(world, "arch")
    complete(world, "synth")
    channel = project_channel(world[0], "ch-free")
    assert len(channel["question_dedup_requests"]) == 1
    assert not owner_questionnaire(channel, thread_id="main")
    return channel, channel["question_dedup_requests"][0]


def apply(world, request, payload):
    return apply_question_dedup_reply(state_dir=world[0], writer=world[1], channel_id="ch-free", thread_id="main",
                                    request_id=request["request_id"], payload=payload, actor="synth", source="test", causation_id="dedup-reply")


def test_two_agents_and_two_questions_get_complete_conservative_dedup(world):
    channel, req = prepare_dedup(world)
    ids = [q["question_id"] for q in channel["open_questions"]]
    assert apply(world, req, {"ledger_digest":req["ledger_digest"],"groups":[],"keep_question_ids":ids})[0]
    advance_libre(world[0],world[1],channel_id="ch-free")
    channel = project_channel(world[0],"ch-free")
    assert len(owner_questionnaire(channel,thread_id="main")) == 2
    assert channel["libre_rounds"]["root"]["publication"]["dedup_status"] == "deduplicated"
    assert not channel["synthesis_requests"]


def test_omitted_original_falls_back_and_late_success_cannot_change_cards(world):
    channel, req = prepare_dedup(world)
    ids = [q["question_id"] for q in channel["open_questions"]]
    assert not apply(world, req, {"ledger_digest":req["ledger_digest"],"groups":[],"keep_question_ids":ids[:1]})[0]
    advance_libre(world[0],world[1],channel_id="ch-free")
    channel = project_channel(world[0],"ch-free")
    assert len(owner_questionnaire(channel,thread_id="main")) == 2
    assert all(q["dedup_status"] == "failed_raw" for q in channel["open_questions"])
    assert not apply(world, req, {"ledger_digest":req["ledger_digest"],"groups":[],"keep_question_ids":ids})[0]
    assert not any(e.type == "channel.question.merged" for e in world[1].event_log.read_all())


def test_dedup_timeout_publishes_raw_without_channel_gate(world):
    _, req = prepare_dedup(world)
    future = datetime.fromisoformat(req["ts"]) + timedelta(hours=1)
    advance_libre(world[0],world[1],channel_id="ch-free", now=future)
    assert len(owner_questionnaire(project_channel(world[0],"ch-free"),thread_id="main")) == 2


def test_new_human_message_prevents_unsent_old_relay(world):
    post(world,mentions=["all"])
    complete(world,"pm",relay={"target_member_ids":["arch"],"prompt":"请核实浏览器存储边界","reason":"需要事实依据"})
    post(world,mid="new",mentions=["synth"],text="补充：先只聊需求")
    complete(world,"arch")
    complete(world,"synth")
    assert not any((m.get("refs") or {}).get("libre_relay") for m in project_channel(world[0],"ch-free")["messages"])


def test_snapshot_keeps_answers_and_materials_fixed(world):
    state,writer=world
    post(world,mentions=["pm"])
    complete(world,"pm",[question("需要登录吗？")])
    channel=project_channel(state,"ch-free")
    descriptor=capture_prd_input(state,channel,request_id="s1",thread_id="main",actor="web",source_event_id="click")
    writer.emit("channel.synthesis.requested",actor="web",correlation_id="ch-free",payload={
        "channel_id":"ch-free","thread_id":"main","request_id":"s1","target_member_id":"synth","prd_input_snapshot":descriptor})
    post(world,mid="later",mentions=["arch"],text="下一版再加同步")
    frozen=snapshot_channel(state,project_channel(state,"ch-free"),"s1")
    assert "later" not in {m["message_id"] for m in frozen["messages"]}
    assert frozen["prd_input_snapshot"]["sha256"] == descriptor["sha256"]
    assert round_ledger(project_channel(state,"ch-free"),"root")["open_questions"]


def execute(service, writer, action, payload):
    requested=writer.emit("web.action.requested",actor="web",payload={"action":action,"request":payload})
    return service.execute(action=action,requested_action=action,requested=requested,payload=payload)


def test_controlled_synthesis_snapshot_idempotency_cancel_retry_and_late_result(world, monkeypatch):
    import zf.runtime.control_actions_channel_msg as actions
    monkeypatch.setattr(actions,"dispatch_channel_replies_background",lambda *a,**k:None)
    state,writer=world
    service=ControlledActionService(state,writer,project_root=state.parent,actor="web",source="web",surface="web")
    post(world,mentions=["pm"])
    first=execute(service,writer,"channel-synthesis-request",{"channel_id":"ch-free","additional_material":"首版不登录，刷新保留"})
    assert first["ok"],first
    channel=project_channel(state,"ch-free")
    rid=channel["synthesis_requests"][-1]["request_id"]
    original=channel["synthesis_requests"][-1]["prd_input_snapshot"]
    second=execute(service,writer,"channel-synthesis-request",{"channel_id":"ch-free"})
    assert second["status"] == "already_requested"
    assert len([e for e in writer.event_log.read_all() if e.type == "channel.synthesis.requested"]) == 1
    cancelled=execute(service,writer,"channel-synthesis-cancel",{"channel_id":"ch-free","request_id":rid})
    assert cancelled["ok"],cancelled
    assert project_channel(state,"ch-free")["synthesis_requests"][-1]["status"] == "cancelled"
    request=next(r for r in channel["reply_requests"] if r["target_member_id"]=="synth")
    message=next(m for m in channel["messages"] if m["message_id"]==request["message_id"])
    emit_structured_reply_events(state_dir=state,writer=writer,channel=project_channel(state,"ch-free"),request=request,message=message,
                                 reply='{"channel_synthesis":{"summary":"迟到结果"}}',reply_event_id="late-result",actor="synth",source="test")
    assert not project_channel(state,"ch-free")["syntheses"]
    post(world,mid="new-input",mentions=["arch"],text="下一版再考虑同步")
    retried=execute(service,writer,"channel-synthesis-request",{"channel_id":"ch-free","retry_of_request_id":rid})
    assert retried["ok"],retried
    channel=project_channel(state,"ch-free")
    new=channel["synthesis_requests"][-1]
    assert new["request_id"] != rid
    assert new["prd_input_snapshot"]["sha256"] == original["sha256"]
    frozen=snapshot_channel(state,channel,new["request_id"])
    assert "new-input" not in {m["message_id"] for m in frozen["messages"]}
    assert any("首版不登录" in m.get("text","") for m in frozen["messages"])


def test_controlled_empty_channel_explains_missing_materials(world,monkeypatch):
    import zf.runtime.control_actions_channel_msg as actions
    monkeypatch.setattr(actions,"dispatch_channel_replies_background",lambda *a,**k:None)
    state,writer=world
    service=ControlledActionService(state,writer,project_root=state.parent,actor="web",source="web",surface="web")
    result=execute(service,writer,"channel-synthesis-request",{"channel_id":"ch-free"})
    assert not result["ok"]
    assert "材料" in result["reason"]
    assert not project_channel(state,"ch-free")["synthesis_requests"]


def test_free_setup_plan_needs_no_seed_and_binds_only_creation():
    from zf.web.plan_extraction import extract_plan_request
    answer={"plan_request":{"id":"quiet-create","question":"创建哪个讨论群？","submit_action":"channel-create-from-template","submit_label":"创建自由讨论群",
            "allow_other":False,"options":[{"id":"prd","label":"需求讨论（推荐）","description":"邀请产品和开发视角，入群后自由聊天",
            "recommended":True,"submit_payload":{"template_id":"prd-clarification","mode":"libre_discuss","overrides":{"backend":"fake"}}},
            {"id":"quick","label":"轻量讨论","description":"少量角色，用户决定何时开始",
            "submit_payload":{"template_id":"quick-change","mode":"libre_discuss","overrides":{"backend":"fake"}}}]}}
    result=extract_plan_request(json.dumps(answer,ensure_ascii=False))
    assert result, result
    assert result["valid"],result.get("validation_error")
    assert result["submit_action"] == "channel-create-from-template"
    assert not result.get("discussion_seed")


def test_fake_provider_discussion_to_full_prd_uses_real_controlled_entry(tmp_path):
    import time
    state=tmp_path/"runtime"
    writer=EventWriter(EventLog(state/"events.jsonl"))
    service=ControlledActionService(state,writer,project_root=tmp_path,actor="web",source="web",surface="web")
    created=execute(service,writer,"channel-create-from-template",{
        "channel_id":"free-e2e","template_id":"prd-clarification","overrides":{"backend":"fake"}})
    assert created["ok"],created
    assert not project_channel(state,"free-e2e")["messages"]
    posted=execute(service,writer,"channel-post-message",{
        "channel_id":"free-e2e","text":"做一个不登录的个人待办清单，刷新后保留数据。","mentions":["product_pm"]})
    assert posted["ok"],posted
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if any(e.type=="channel.libre.reply.processed" for e in writer.event_log.read_all()):
            break
        time.sleep(.03)
    result=execute(service,writer,"channel-synthesis-request",{"channel_id":"free-e2e","additional_material":"删除前需要确认；不做跨设备同步。"})
    assert result["ok"],result
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        channel=project_channel(state,"free-e2e")
        if channel["syntheses"]:
            break
        time.sleep(.03)
    assert channel["syntheses"],channel["synthesis_requests"]
    assert not any(e.type in {"channel.discussion.started","workflow.invoke.requested","task.created"}
                   for e in writer.event_log.read_all())


def test_conversation_projection_retains_free_generation_progress_without_large_sidecars(world):
    from zf.runtime.channel_conversation_projection import project_channel_conversation
    state, writer = world
    writer.emit("channel.synthesis.requested",actor="web",correlation_id="ch-free",payload={
        "channel_id":"ch-free","thread_id":"main","request_id":"s1","target_member_id":"synth",
        "prd_input_snapshot":{"ref":"channels/input.json","sha256":"frozen","cutoff_at":"fixed"}})
    writer.emit("channel.synthesis.repair.requested",actor="kernel",correlation_id="ch-free",payload={
        "channel_id":"ch-free","thread_id":"main","request_id":"s1","target_member_id":"synth",
        "invalid_reply_ref":{"ref":"diagnostics/large.json"},"status":"repair_requested"})
    view=project_channel_conversation(state,"ch-free")
    assert len(view["synthesis_requests"]) == 1
    assert view["synthesis_requests"][0]["prd_input_snapshot"]["cutoff_at"] == "fixed"
    assert view["synthesis_requests"][0]["status"] == "repair_requested"
    assert "invalid_reply_ref" not in view["synthesis_requests"][0]


def fail(world, member, root="root"):
    channel=project_channel(world[0],"ch-free")
    request=next(r for r in channel["reply_requests"] if r["target_member_id"]==member and r["message_id"]==root)
    world[1].emit("channel.agent.reply.failed",actor=member,correlation_id="ch-free",payload={
        **request,"channel_id":"ch-free","reason":"provider failed","status":"failed"})


@pytest.mark.parametrize("other_completed",[False,True])
def test_partial_failure_counts_actual_distinct_replies(world,other_completed):
    post(world,mentions=["all"])
    complete(world,"pm",[question("刷新保留吗？"),question("关闭浏览器重开保留吗？")])
    if other_completed:
        complete(world,"arch")
    else:
        fail(world,"arch")
    fail(world,"synth")
    advance_libre(world[0],world[1],channel_id="ch-free")
    channel=project_channel(world[0],"ch-free")
    assert bool(channel["question_dedup_requests"]) is other_completed
    assert bool(owner_questionnaire(channel,thread_id="main")) is not other_completed


def test_only_one_question_needs_no_dedup_even_after_all_members_reply(world):
    post(world,mentions=["all"])
    complete(world,"pm",[question("需要登录吗？")]);complete(world,"arch");complete(world,"synth")
    channel=project_channel(world[0],"ch-free")
    assert not channel["question_dedup_requests"]
    assert len(owner_questionnaire(channel,thread_id="main")) == 1


def test_interleaved_rounds_and_answering_other_cards_do_not_invalidate_dedup(world):
    post(world,mentions=["all"])
    complete(world,"pm",[question("刷新保留吗？"),question("关闭重开保留吗？")])
    post(world,mid="second",mentions=["arch"])
    complete(world,"arch",[question("需要登录吗？")],root="second")
    channel=project_channel(world[0],"ch-free")
    ready=owner_questionnaire(channel,thread_id="main")
    assert len(ready)==1 and ready[0]["libre_round_id"]=="second"
    complete(world,"arch");complete(world,"synth")
    channel=project_channel(world[0],"ch-free");req=channel["question_dedup_requests"][0]
    service=ControlledActionService(world[0],world[1],project_root=world[0].parent,actor="web",surface="web")
    result=execute(service,world[1],"channel-question-resolve",{"channel_id":"ch-free","question_id":ready[0]["question_id"],"resolution":"answered","answer":"不需要登录"})
    assert result["ok"],result
    ids=[q["question_id"] for q in round_ledger(channel,"root")["open_questions"]]
    assert apply(world,req,{"ledger_digest":req["ledger_digest"],"groups":[],"keep_question_ids":ids})[0]
    advance_libre(world[0],world[1],channel_id="ch-free")
    channel=project_channel(world[0],"ch-free")
    for q in owner_questionnaire(channel,thread_id="main"):
        assert execute(service,world[1],"channel-question-resolve",{"channel_id":"ch-free","question_id":q["question_id"],"resolution":"answered","answer":"保留"})["ok"]
    advance_libre(world[0],world[1],channel_id="ch-free")
    assert not project_channel(world[0],"ch-free")["synthesis_requests"]


def test_atomic_merge_preserves_independent_question_and_output_digest(world):
    from zf.runtime.channel_question_dedup import question_ledger_digest
    post(world,mentions=["all"])
    options=[{"id":"yes","label":"保留"},{"id":"no","label":"不保留"}]
    complete(world,"pm",[question("刷新后保留事项吗？",options=options),question("需要跨设备同步吗？")])
    complete(world,"arch",[question("页面刷新后事项还在吗？",options=options)])
    complete(world,"synth")
    channel=project_channel(world[0],"ch-free");req=channel["question_dedup_requests"][0]
    by_text={q["question"]:q["question_id"] for q in channel["open_questions"]}
    canonical=by_text["刷新后保留事项吗？"];duplicate=by_text["页面刷新后事项还在吗？"];independent=by_text["需要跨设备同步吗？"]
    assert apply(world,req,{"ledger_digest":req["ledger_digest"],"keep_question_ids":[canonical,independent],
        "groups":[{"canonical_question_id":canonical,"merge_question_ids":[duplicate],"reason":"同一刷新持久化决策",
        "option_mappings":{duplicate:{"yes":"yes","no":"no"}}}]})[0]
    applied=next(e for e in world[1].event_log.read_all() if e.type=="channel.question.dedup.applied")
    channel=project_channel(world[0],"ch-free")
    assert applied.payload["output_ledger_digest"]==question_ledger_digest(round_ledger(channel,"root"),thread_id="main")
    advance_libre(world[0],world[1],channel_id="ch-free")
    assert {q["question_id"] for q in owner_questionnaire(project_channel(world[0],"ch-free"),thread_id="main")} == {canonical,independent}


def test_changing_host_changes_default_reply_and_drops_old_unsent_relay(world):
    post(world,mentions=["all"])
    complete(world,"pm",relay={"target_member_ids":["arch"],"prompt":"核实存储","reason":"需要事实"})
    world[1].emit("channel.leader.updated",actor="web",correlation_id="ch-free",payload={"channel_id":"ch-free","leader_member_id":"arch","leader_revision":2})
    complete(world,"arch");complete(world,"synth")
    assert post(world,mid="new-host",text="主持人请看一下").targets == ["arch"]
    assert not any((m.get("refs")or{}).get("libre_relay") for m in project_channel(world[0],"ch-free")["messages"])


def test_kanban_plan_apply_creates_quiet_channel_without_copying_origin(tmp_path):
    from zf.core.events import ZfEvent
    from zf.runtime.kanban_plan_requests import PLAN_REQUESTED_EVENT, plan_requirement_digest
    from zf.web.plan_extraction import extract_plan_request
    state=tmp_path/"runtime";writer=EventWriter(EventLog(state/"events.jsonl"))
    service=ControlledActionService(state,writer,project_root=tmp_path,actor="web",surface="web")
    origin=writer.emit("user.message",actor="web",payload={"message":"帮我建一个讨论个人待办需求的群，进群后我再说。","project_id":"qa","conversation_id":"kanban:qa","thread_key":"main"})
    raw={"plan_request":{"id":"quiet-create","question":"创建哪种讨论群？","submit_action":"channel-create-from-template","submit_label":"创建自由讨论群","allow_other":False,
         "options":[{"id":"quick","label":"轻量群（推荐）","description":"创建后等待用户发言","recommended":True,
                    "submit_payload":{"template_id":"quick-change","mode":"libre_discuss","overrides":{"backend":"fake"}}},
                    {"id":"prd","label":"需求群","description":"包含更多角色","submit_payload":{"template_id":"prd-clarification","mode":"libre_discuss","overrides":{"backend":"fake"}}}]}}
    request=extract_plan_request(json.dumps(raw,ensure_ascii=False),plan_context={
        "project_id":"qa","conversation_id":"kanban:qa","thread_key":"main","originating_message_event_id":origin.id,
        "originating_message_event_ids":[origin.id],"requirement_digest":plan_requirement_digest([(origin.id,origin.payload["message"])])})
    assert request and request["valid"],request
    event=ZfEvent(type=PLAN_REQUESTED_EVENT,actor="web",correlation_id="kanban:qa")
    request["request_event_id"]=event.id;event.payload={"plan_request":request,"request":request};writer.append(event)
    result=execute(service,writer,"kanban-plan-apply",{"plan_response":{
        "request_event_id":event.id,"request_id":request["request_id"],"revision":request["revision"],
        "question_id":request["question_id"],"option_id":"quick","answer":"轻量群"}})
    assert result["ok"],result
    channel=project_channel(state,result["channel_id"])
    assert channel["discussion"]["mode"]=="libre_discuss"
    assert not channel["messages"] and not channel["reply_requests"] and not channel["discussions"]
    assert channel["discussion"]["default_responder_id"]==channel["leader_member_id"]
    assert not any(e.type=="channel.discussion.started" for e in writer.event_log.read_all())


@pytest.mark.parametrize("member,evidence,expected",[("arch",["file:src/storage.py"],True),("arch",[],False),("pm",["file:src/storage.py"],False)])
def test_fact_settlement_requires_assigned_member_bound_input_and_evidence(world,member,evidence,expected):
    post(world,mentions=["pm"])
    complete(world,"pm",[question("请核实当前存储实现",kind="fact",target_member_id="arch")])
    qid=project_channel(world[0],"ch-free")["open_questions"][0]["question_id"]
    post(world,mid="verify",mentions=[member],text="请核实存储实现，并保留依据")
    complete(world,member,root="verify",fact_resolutions=[{"question_id":qid,"outcome":"resolved","answer":"现有实现采用本机存储","evidence_refs":evidence}])
    question_row=next(q for q in project_channel(world[0],"ch-free")["open_questions"] if q["question_id"]==qid)
    assert (question_row["status"]=="resolved") is expected
    assert not project_channel(world[0],"ch-free")["synthesis_requests"]


def test_runtime_dedup_waits_for_semantic_apply_after_transport_completion(world):
    from types import SimpleNamespace
    from zf.runtime.channel_synthesis_reactor import react_channel_question_dedup_requested
    state,writer=world
    channel,req=prepare_dedup(world)
    event=next(e for e in writer.event_log.read_all() if e.id==req["event_id"])
    host=SimpleNamespace(state_dir=state,event_writer=writer,event_log=writer.event_log,config=None,project_root=state.parent)
    react_channel_question_dedup_requested(host,event)
    channel=project_channel(state,"ch-free")
    message=next(m for m in channel["messages"] if (m.get("refs")or{}).get("question_dedup_request_id")==req["request_id"])
    assert message["role"]=="system" and message["member_id"]=="pm"
    control=next(r for r in channel["reply_requests"] if r["message_id"]==message["message_id"])
    pack=next(p for p in channel["context_packs"] if p["context_pack_id"]==control["context_pack_id"])
    assert len(pack["question_ledger"])==2
    completed=writer.emit("channel.agent.reply.completed",actor="synth",correlation_id="ch-free",payload={
        **control,"channel_id":"ch-free","status":"completed","text":"正在提交问题整理结果"})
    # The real watcher can observe the transport event before contract publication.
    advance_libre(state,writer,channel_id="ch-free")
    assert not owner_questionnaire(project_channel(state,"ch-free"),thread_id="main")
    ids=[q["question_id"] for q in round_ledger(channel,"root")["open_questions"]]
    emit_structured_reply_events(state_dir=state,writer=writer,channel=project_channel(state,"ch-free"),request=control,
        message=message,reply=json.dumps({"channel_question_dedup":{"ledger_digest":req["ledger_digest"],"groups":[],"keep_question_ids":ids}}),
        reply_event_id=completed.id,actor="synth",source="test")
    advance_libre(state,writer,channel_id="ch-free")
    assert len(owner_questionnaire(project_channel(state,"ch-free"),thread_id="main"))==2
    assert project_channel(state,"ch-free")["libre_rounds"]["root"]["publication"]["dedup_status"]=="deduplicated"


def test_cancel_and_publication_have_one_serial_order(world, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from contextlib import contextmanager
    from threading import Event
    import zf.runtime.channel_reply_contract as contracts
    import zf.runtime.channel_synthesis_cancel as cancellation
    monkeypatch.setattr("zf.runtime.control_actions_channel_msg.dispatch_channel_replies_background",lambda **kwargs:None)
    state,writer=world
    service=ControlledActionService(state,writer,project_root=state.parent,actor="web",surface="web")
    post(world,mentions=["pm"])
    assert execute(service,writer,"channel-synthesis-request",{"channel_id":"ch-free"})["ok"]
    channel=project_channel(state,"ch-free");rid=channel["synthesis_requests"][-1]["request_id"]
    entered,release,waiting=Event(),Event(),Event()
    def publish(**kwargs):
        entered.set()
        assert release.wait(3)
    native_lock=cancellation.locked_path
    @contextmanager
    def observe_lock(path):
        waiting.set()
        with native_lock(path):
            yield
    monkeypatch.setattr(contracts,"_emit_synthesis_locked",publish)
    monkeypatch.setattr(cancellation,"locked_path",observe_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication=pool.submit(contracts._emit_synthesis,state_dir=state,writer=writer,channel=channel,
            channel_id="ch-free",thread_id="main",member_id="synth",request={},reply="",reply_event_id="publication",
            synthesis_request_id=rid,synthesis_repair_id="",synthesis_repair_revision=0,actor="synth",source="test")
        try:
            assert entered.wait(2)
            cancel=pool.submit(execute,service,writer,"channel-synthesis-cancel",{"channel_id":"ch-free","request_id":rid})
            assert waiting.wait(2)
            with pytest.raises(TimeoutError):
                cancel.result(timeout=.1)
        finally:
            release.set()
        publication.result(timeout=2)
        assert cancel.result(timeout=2)["ok"]


def test_rejected_relay_context_settles_without_hanging_round(world, monkeypatch):
    post(world, mentions=["all"])
    complete(world, "pm", relay={"target_member_ids": ["arch"], "reason": "核实技术限制", "prompt": "请确认本机存储约束"})
    complete(world, "arch")
    complete(world, "synth")
    def reject(state, writer, channel, event, **kwargs):
        writer.emit("channel.context_pack.rejected", actor="channel-runtime", payload={
            "channel_id": "ch-free", "thread_id": "main", "context_pack_id": "ctx-relay-rejected",
            "trigger_message_id": f"msg-{event.payload['request_id']}", "target_member_id": "arch",
            "reason": "profile_snapshot_missing"})
    monkeypatch.setattr("zf.runtime.channel_libre._post_control", reject)
    for _ in range(2):
        advance_libre(world[0], world[1], channel_id="ch-free", project_root=world[0].parent)
    assert project_channel(world[0], "ch-free")["libre_rounds"]["root"]["publication"]


def test_spine_report_intent_does_not_stall_native_chat_questions(world):
    state, writer = world
    writer.emit("channel.member.invited", actor="web", payload={"channel_id": "ch-free", "member_id": "spine",
                "channel_role": "spine-reviewer", "member_type": "provider_agent", "backend": "fake", "permissions": ["read", "message"]})
    post(world, mentions=["all"])
    complete(world, "pm", [question("需要删除确认吗？")])
    complete(world, "arch")
    complete(world, "synth")
    advance_libre(state, writer, channel_id="ch-free", project_root=state.parent)
    channel = project_channel(state, "ch-free")
    assert "spine" not in channel["libre_rounds"]["root"]["targets"]
    assert channel["libre_rounds"]["root"]["publication"]
    assert any(e.type == "channel.spine_review.requested" for e in writer.event_log.read_all())


@pytest.mark.parametrize("mapping", [["invalid"], {"source": {"yes": ["yes"], "no": "no"}}])
def test_malformed_model_option_mapping_rejects_without_partial_merge(world, mapping):
    post(world, mentions=["all"])
    options = [{"id": "yes", "label": "保留"}, {"id": "no", "label": "不保留"}]
    complete(world, "pm", [question("刷新后保留吗？", options=options)])
    complete(world, "arch", [question("页面刷新后是否保留？", options=options)])
    complete(world, "synth")
    channel = project_channel(world[0], "ch-free")
    request = channel["question_dedup_requests"][0]
    canonical, duplicate = [q["question_id"] for q in channel["open_questions"]]
    if isinstance(mapping, dict):
        mapping = {duplicate: mapping["source"]}
    ok, _ = apply(world, request, {"ledger_digest": request["ledger_digest"], "keep_question_ids": [canonical],
        "groups": [{"canonical_question_id": canonical, "merge_question_ids": [duplicate],
                    "reason": "同一刷新持久化决定", "option_mappings": mapping}]})
    assert not ok
    advance_libre(world[0], world[1], channel_id="ch-free")
    assert len(owner_questionnaire(project_channel(world[0], "ch-free"), thread_id="main")) == 2


def test_long_message_round_uses_full_text_instead_of_event_preview(world):
    from zf.runtime.channel_sidecar import channel_message_event_payload
    state, writer = world
    payload = channel_message_event_payload(state, {"channel_id": "ch-free", "thread_id": "main",
        "message_id": "long-root", "member_id": "operator", "role": "user", "source": "web",
        "text": "请一起讨论以下需求。" + "需要保留原始约束。" * 400 + "\n@all"}, created_by="web")
    assert "@all" not in payload["text"]
    event = writer.emit("channel.message.posted", actor="web", correlation_id="ch-free", payload=payload)
    routed = route_channel_message(state_dir=state, writer=writer, message_event=event, message_payload=payload,
        actor="web", source="web", dispatch_inline=False, project_root=state.parent)
    assert set(routed.targets) == {"pm", "arch", "synth"}
    assert project_channel(state, "ch-free")["libre_rounds"]["long-root"]["allow_relay"]
