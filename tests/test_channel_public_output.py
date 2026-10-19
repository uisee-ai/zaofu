from dataclasses import replace
import json

import pytest

from zf.runtime.channel_provider_completion import (
    _merge_turn_results, _normalize_public_items, run_channel_provider_with_continuations,
)
from zf.runtime.channel_reply_stream import CHANNEL_CONTRACT_MARKER, ChannelVisibleMessageEmitter
from zf.runtime.channel_sidecar import channel_message_event_payload, hydrate_channel_message_text
from zf.web.headless_agent import HeadlessMessage, HeadlessTurnResult


def result(text, **kwargs):
    return HeadlessTurnResult(ok=True, status="completed", backend="codex-headless",
                              thread_id="thread", provider_session_id="session",
                              reply=text, messages=[], usage={}, **kwargs)


def contract(summary):
    return CHANNEL_CONTRACT_MARKER + '\n' + json.dumps({"channel_contribution": {"summary": summary}})


def test_continuation_replaces_only_incomplete_contract_and_recovers_public_stream():
    first = "首段完整证据。\n\n" + CHANNEL_CONTRACT_MARKER + '\n{"channel_contribution":{"summary":"未完'
    second = "续写的新证据😀。\n\n" + contract("完整")
    merged = _merge_turn_results(result(first), result(second), continuation_count=1,
                                 expected_contract_key="channel_contribution")
    assert merged.reply == "首段完整证据。\n\n" + second
    emitted = []
    bridge = ChannelVisibleMessageEmitter(emitted.append)
    bridge.emit(HeadlessMessage(type="text", content=first))
    bridge.begin_continuation()
    bridge.emit(HeadlessMessage(type="text", content=second))
    bridge.finish(merged.reply)
    assert "".join(m.content for m in emitted) == "首段完整证据。\n\n续写的新证据😀。\n\n"


def test_continuation_json_suffix_never_leaks_into_public_stream():
    first = "原文\n" + CHANNEL_CONTRACT_MARKER + '\n{"channel_contribution":{"summary":"'
    suffix = 'secret contract value"}}'
    merged = _merge_turn_results(result(first), result(suffix), continuation_count=1,
                                 expected_contract_key="channel_contribution")
    emitted = []
    bridge = ChannelVisibleMessageEmitter(emitted.append)
    bridge.emit(HeadlessMessage(type="text", content=first))
    bridge.begin_continuation()
    bridge.emit(HeadlessMessage(type="text", content=suffix))
    bridge.finish(merged.reply)
    assert merged.reply == first + suffix
    assert "".join(m.content for m in emitted) == "原文\n"


def test_native_item_boundary_preserves_both_answers_and_final_contract():
    texts = ["第一项\n" + contract("旧"), "第二项\n" + contract("新")]
    original = replace(result("\n\n".join(texts)), messages=[
        HeadlessMessage(type="text", content=t, raw={"item_id": str(i), "completed": True})
        for i, t in enumerate(texts)])
    normalized = _normalize_public_items(original)
    assert normalized.reply == "第一项\n\n\n" + texts[1]
    assert normalized.messages == original.messages


def test_unknown_delivery_never_starts_continuation():
    calls = []
    def run(prompt, session):
        calls.append(prompt)
        return replace(result("已接收的部分正文", telemetry={"input_delivery": "unknown"}),
                       ok=False, status="incomplete", incomplete=True)
    output = run_channel_provider_with_continuations(run_turn=run, initial_prompt="请求",
        provider_session_id="session", channel={}, request={}, message={})
    assert len(calls) == 1 and not output.ok


def test_public_item_evidence_is_sidecar_only_and_digest_verified(tmp_path):
    text = "公开原文😀\n" * 600
    items = [{"type": "text", "content": text, "raw": {"item_id": "a", "turn_id": "t"}}]
    payload = channel_message_event_payload(tmp_path, {
        "channel_id": "ch", "message_id": "m", "text": text, "public_output_items": items,
    }, created_by="test")
    assert "public_output_items" not in payload
    assert len(payload["text"]) < len(text)
    assert hydrate_channel_message_text(tmp_path, payload, strict=True) == text
    body_path = tmp_path / payload["body_ref"]
    body = json.loads(body_path.read_text())
    assert body["public_output_items"] == items
    body_path.write_text("{}")
    from zf.runtime.sidecar_refs import SidecarRefError
    with pytest.raises(SidecarRefError):
        hydrate_channel_message_text(tmp_path, payload, strict=True)


def test_unknown_run_keeps_partial_evidence_blocks_same_member_and_allows_other_member(tmp_path, monkeypatch):
    from tests.test_channel_adapter_parallel_dispatch import CH, _seed
    from zf.runtime import channel_adapter
    from zf.runtime.channel_conversation_projection import project_channel_conversation
    from zf.runtime.channel_projection import project_channel

    state, writer = _seed(tmp_path, members=2, backend="codex")
    called = []
    def run(**kwargs):
        target = kwargs["request"]["target_member_id"]
        called.append(target)
        if target == "m-0":
            return replace(result("部分证据😀", telemetry={"input_delivery": "unknown"}),
                           ok=False, status="timeout", error="transport lost")
        return result("独立目标正常完成")
    monkeypatch.setattr(channel_adapter, "_run_headless_reply", run)
    def dispatch(request):
        return channel_adapter.dispatch_reply_request(state_dir=state, writer=writer,
            channel_id=CH, request_id=request, actor="test", source="test", project_root=tmp_path)
    assert dispatch("req-0").failed == ["req-0"]
    writer.emit("channel.agent.reply.requested", actor="test", payload={
        "channel_id": CH, "thread_id": "main", "request_id": "next", "message_id": "m-1",
        "target_member_id": "m-0", "status": "pending"})
    assert dispatch("next").skipped
    assert dispatch("req-1").completed == ["req-1"]
    assert called == ["m-0", "m-1"]
    first = next(r for r in project_channel(state, CH)["reply_requests"] if r["request_id"] == "req-0")
    assert first["failure_status"] == "delivery_unknown" and not first["retryable"]
    view = project_channel_conversation(state, CH)
    partial = next(m for m in view["messages"] if m["refs"].get("incomplete"))
    assert partial["text"] == "部分证据😀"
    assert partial["refs"]["run_generation"] == 1
