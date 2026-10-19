"""A valid long provider run must not be superseded by the shorter watchdog."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.test_channel_adapter_parallel_dispatch import CH, _seed
from tests.test_channel_reply_remediation import NOW, _evt
from zf.runtime.channel_adapter import dispatch_reply_request
from zf.runtime.channel_reply_contract import emit_structured_reply_events
from zf.runtime.channel_reply_remediation import channel_reply_remediation_candidates


def test_running_reply_respects_its_recorded_provider_execution_budget():
    events = [_evt("channel.agent.reply.requested", age=1001),
              _evt("channel.agent.reply.started", age=1000, provider_timeout_seconds=1800)]
    assert channel_reply_remediation_candidates(events, now=NOW) == []
    assert channel_reply_remediation_candidates(events, now=NOW + timedelta(seconds=861))[0]["kind"] == "exhaust"


def test_pending_retry_does_not_inherit_the_previous_provider_budget():
    events = [_evt("channel.agent.reply.requested", age=6000),
              _evt("channel.agent.reply.started", age=5000, provider_timeout_seconds=1800),
              _evt("channel.agent.reply.requested", age=1000, gen=2)]
    assert channel_reply_remediation_candidates(events, now=NOW)[0]["kind"] == "redispatch"


def test_dispatch_binds_provider_timeout_before_starting_execution(tmp_path, monkeypatch):
    from zf.runtime import channel_adapter
    state, writer = _seed(tmp_path, members=1, backend="codex")
    monkeypatch.setenv("ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S", "2100")
    observed = []
    def run(**kwargs):
        observed.append(kwargs["request"].get("provider_timeout_seconds"))
        started = [e for e in writer.event_log.read_all() if e.type == "channel.agent.reply.started"][-1]
        assert started.payload["provider_timeout_seconds"] == 2100
        return SimpleNamespace(ok=True, reply="Reviewed", backend="codex-headless")
    monkeypatch.setattr(channel_adapter, "_run_headless_reply", run)
    result = dispatch_reply_request(state_dir=state, writer=writer, channel_id=CH, request_id="req-0", actor="test", source="test", project_root=tmp_path)
    assert result.completed == ["req-0"]
    assert observed == [2100]


def test_late_provider_generation_cannot_publish_semantic_contract(tmp_path):
    import json
    from tests.test_channel_long_review_regressions import _review_runtime
    from zf.runtime.channel_projection import project_channel
    from zf.runtime.channel_reply_stream import CHANNEL_CONTRACT_MARKER
    writer = _review_runtime(tmp_path)
    writer.emit("channel.agent.reply.requested", actor="runtime", payload={"channel_id": "ch", "request_id": "reply-arch", "run_generation": 2})
    before = len(writer.event_log.read_all())
    review = {"summary": "old", "answer": "old decision", "outcome": "needs_owner",
              "owner_question": "Obsolete question?", "recommended_answer": "Old recommendation", "evidence_refs": ["PRD.md"]}
    emit_structured_reply_events(state_dir=tmp_path, writer=writer, channel=project_channel(tmp_path, "ch"),
        request={"request_id": "reply-arch", "run_generation": 1, "target_member_id": "arch"},
        message={"refs": {"cross_review_request_id": "x-arch", "question_id": "q1", "cross_review_contract": "v2"}},
        reply="Reviewed\n" + CHANNEL_CONTRACT_MARKER + "\n" + json.dumps({"channel_cross_review": review}),
        reply_event_id="old-completed", actor="arch", source="test")
    assert len(writer.event_log.read_all()) == before
    assert project_channel(tmp_path, "ch")["open_questions"][0]["kind"] == "fact"


@pytest.mark.parametrize("last_tick,expected_calls,expected_status", [(160, 2, "completed"), (2201, 1, "failed")])
def test_continuations_share_the_original_execution_deadline(tmp_path, monkeypatch, last_tick, expected_calls, expected_status):
    from tests.test_channel_router import _ScriptedHeadlessBackend
    from zf.runtime import channel_adapter
    from zf.runtime.channel_projection import project_channel
    state, writer = _seed(tmp_path, members=1, backend="codex")
    ticks = iter([100, 110, last_tick])
    monkeypatch.setenv("ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S", "2100")
    monkeypatch.setattr(channel_adapter, "monotonic", lambda: next(ticks))
    backend = _ScriptedHeadlessBackend([
        {"reply": "First part ", "incomplete": True, "completion_reason": "max_output_tokens"},
        {"reply": "final part"},
    ])
    dispatch_reply_request(state_dir=state, writer=writer, channel_id=CH, request_id="req-0", actor="test", source="test", project_root=tmp_path, headless_backends={"codex-headless": backend})
    assert len(backend.calls) == expected_calls
    assert all(call["execution_deadline"] == 2200 for call in backend.calls)
    assert backend.calls[0]["timeout_s"] == 2090
    if expected_calls == 2:
        assert backend.calls[1]["timeout_s"] == 2040
    assert project_channel(state, CH)["reply_requests"][0]["status"] == expected_status


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_nonfinite_provider_budget_is_not_an_unbounded_execution(value, monkeypatch):
    from zf.runtime.channel_adapter import _channel_provider_headless_timeout_s
    monkeypatch.setenv("ZF_CHANNEL_PROVIDER_HEADLESS_TIMEOUT_S", value)
    assert _channel_provider_headless_timeout_s() == 1800
