"""Regressions captured in the real 2026-09-29 Channel review recording."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.core.config.schema import ChannelConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.channel_context import build_channel_context_pack
from zf.runtime.channel_deliberation_contract import apply_cross_review_reply
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_synthesis_reactor import react_channel_cross_review_requested


@pytest.mark.parametrize("budget, repeats", [(512000, 18000), (2000000, 200000)])
def test_long_review_budget_is_loaded_and_preserves_the_complete_source(tmp_path, budget, repeats):
    path = tmp_path / "zf.yaml"
    path.write_text(f"version: '1.0'\nproject:\n  name: review\nchannel:\n  semantic_source_max_chars: {budget}\n")
    config = load_config(path)
    text = "source " * repeats + "FINAL SOURCE RULE"
    channel = {"messages": [
        {"message_id": "source", "thread_id": "main", "role": "user", "text": text},
        {"message_id": "synth", "thread_id": "main", "source": "runtime", "refs": {"synthesis_request_id": "s1"}},
    ], "discussions": {"main": {"requirement_message_id": "source"}}}
    kwargs = dict(channel_id="ch", thread_id="main", target_member_id="arch", trigger_message_id="synth")
    limited = build_channel_context_pack(
        channel, config=ZfConfig(channel=ChannelConfig(semantic_source_max_chars=120_000)), **kwargs,
    )
    assert not limited["semantic_source_complete"]
    complete = build_channel_context_pack(channel, config=config, **kwargs)
    assert complete["semantic_source_complete"]
    assert complete["semantic_source_documents"][0]["content"] == text
    assert complete["limits"]["max_semantic_source_chars"] == budget


@pytest.mark.parametrize("value", ["true", "0", "-1", "10000001", "1.5", "'512000'"])
def test_review_budget_rejects_unbounded_or_noninteger_values(tmp_path, value):
    path = tmp_path / "zf.yaml"
    path.write_text(f"version: '1.0'\nproject:\n  name: review\nchannel:\n  semantic_source_max_chars: {value}\n")
    with pytest.raises(ConfigError, match="semantic_source_max_chars"):
        load_config(path)


@pytest.mark.parametrize("budget", [1, 1_000_001, 2_000_000, 10_000_000])
def test_large_source_budget_has_matching_yaml_and_typed_boundaries(tmp_path, budget):
    path = tmp_path / "zf.yaml"
    path.write_text(f"version: '1.0'\nproject:\n  name: review\nchannel:\n  semantic_source_max_chars: {budget}\n")
    assert load_config(path).channel.semantic_source_max_chars == budget
    assert ChannelConfig(semantic_source_max_chars=budget).semantic_source_max_chars == budget
    assert ChannelConfig().semantic_source_max_chars == 2_000_000
    assert ChannelConfig().codex_context_window_tokens == 0


@pytest.mark.parametrize("budget", [True, 0, -1, 10_000_001, 1.5, "2000000"])
def test_typed_source_budget_rejects_invalid_values(budget):
    with pytest.raises(ValueError, match="semantic_source_max_chars"):
        ChannelConfig(semantic_source_max_chars=budget)


@pytest.mark.parametrize("channel_yaml", ["", "channel: {}\n", "channel:\n  agent_profiles: []\n"])
def test_global_default_preserves_large_sources_without_project_override(tmp_path, channel_yaml):
    path = tmp_path / "zf.yaml"
    path.write_text("version: '1.0'\nproject:\n  name: another-project\n" + channel_yaml)
    config = load_config(path)
    assert config.channel.semantic_source_max_chars == 2_000_000
    body = "完整来源。" * 250_000 + "FINAL RULE"
    channel = {"messages": [
        {"message_id": "source", "thread_id": "main", "role": "user", "text": body},
        {"message_id": "synth", "thread_id": "main", "source": "runtime",
         "refs": {"synthesis_request_id": "s1"}},
    ], "discussions": {"main": {"requirement_message_id": "source"}}}
    for runtime_config in (None, ZfConfig(), config):
        pack = build_channel_context_pack(
            channel, config=runtime_config, channel_id="ch", thread_id="main",
            target_member_id="arch", trigger_message_id="synth",
        )
        assert pack["semantic_source_complete"]
        assert pack["semantic_source_documents"][0]["content"] == body
        assert pack["limits"]["max_semantic_source_chars"] == 2_000_000


def test_global_default_still_rejects_over_budget_without_truncation():
    channel = {"messages": [
        {"message_id": "source", "thread_id": "main", "role": "user", "text": "x" * 2_000_001},
        {"message_id": "synth", "thread_id": "main", "refs": {"synthesis_request_id": "s1"}},
    ], "discussions": {"main": {"requirement_message_id": "source"}}}
    pack = build_channel_context_pack(
        channel, channel_id="ch", thread_id="main", target_member_id="arch", trigger_message_id="synth",
    )
    assert not pack["semantic_source_complete"]
    assert "semantic_source_budget_exceeded" in pack["semantic_source_reason"]


def test_reactor_enqueues_provider_work_without_waiting_for_the_model(tmp_path, monkeypatch):
    from zf.runtime import channel_synthesis_reactor as reactor
    log = EventLog(tmp_path / "events.jsonl")
    writer = EventWriter(log)
    event = writer.emit("channel.cross_review.requested", actor="test", payload={
        "channel_id": "ch", "request_id": "x1", "question_id": "q1", "target_member_id": "arch",
    })
    host = SimpleNamespace(state_dir=tmp_path, event_log=log, event_writer=writer, project_root=tmp_path, config=None)
    calls = []
    def route(**kwargs):
        assert kwargs["dispatch_inline"] is False, "a slow provider must not hold the kernel reactor"
        calls.append("routed")
        return SimpleNamespace(reply_requests=["reply-1"])
    monkeypatch.setattr(reactor, "route_channel_message", route)
    monkeypatch.setattr("zf.runtime.channel_message_ingress.dispatch_channel_replies_background", lambda **kwargs: calls.append("scheduled"))
    react_channel_cross_review_requested(host, event)
    assert calls == ["routed", "scheduled"]


def _review_runtime(tmp_path: Path):
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    for member in ("arch", "security"):
        writer.emit("channel.member.invited", actor="test", payload={"channel_id": "ch", "member_id": member, "member_type": "persona_agent"})
    writer.emit("channel.question.opened", actor="test", payload={
        "channel_id": "ch", "thread_id": "main", "question_id": "q1", "question": "Does initiating imply candidate access?",
        "kind": "fact", "target_member_id": "arch", "asked_by": "arch",
    })
    for member in ("arch", "security"):
        writer.emit("channel.cross_review.requested", actor="test", payload={
            "channel_id": "ch", "thread_id": "main", "request_id": f"x-{member}", "question_id": "q1", "target_member_id": member,
        })
    return writer


def _reply(tmp_path, writer, member, outcome, **extra):
    apply_cross_review_reply(state_dir=tmp_path, writer=writer, channel=project_channel(tmp_path, "ch"),
        channel_id="ch", thread_id="main", member_id=member, request={},
        refs={"cross_review_request_id": f"x-{member}", "question_id": "q1", "cross_review_contract": "v2"},
        review={"summary": "Source review", "answer": "There are two interpretations.",
                "evidence_refs": ["PRD.md#permissions"], "outcome": outcome, **extra},
        reply_event_id=f"reply-{member}", actor=member, source="test")


def test_ambiguous_fact_becomes_owner_choice_instead_of_false_evidence_resolution(tmp_path):
    writer = _review_runtime(tmp_path)
    _reply(tmp_path, writer, "arch", "needs_owner", owner_question="Should initiation include minimal candidate access?",
           recommended_answer="Allow only minimal candidates in the initiation context.")
    question = project_channel(tmp_path, "ch")["open_questions"][0]
    assert question["status"] == "open"
    assert question["kind"] == "owner_decision"
    assert question["target_member_id"] == "owner"
    assert question["question"] == "Should initiation include minimal candidate access?"
    _reply(tmp_path, writer, "security", "resolved")
    assert project_channel(tmp_path, "ch")["open_questions"][0]["status"] == "open"


def test_multiple_reviewers_must_all_resolve_before_closing_fact(tmp_path):
    writer = _review_runtime(tmp_path)
    _reply(tmp_path, writer, "arch", "resolved")
    assert project_channel(tmp_path, "ch")["open_questions"][0]["status"] == "open"
    _reply(tmp_path, writer, "security", "resolved")
    question = project_channel(tmp_path, "ch")["open_questions"][0]
    assert question["status"] == "resolved"
    assert question["resolution"] == "evidence"


def test_unresolved_review_and_missing_v2_outcome_cannot_close_a_fact(tmp_path):
    writer = _review_runtime(tmp_path)
    _reply(tmp_path, writer, "arch", "unresolved")
    _reply(tmp_path, writer, "security", "")
    assert project_channel(tmp_path, "ch")["open_questions"][0]["status"] == "open"
    assert any(e.type == "channel.cross_review.rejected" for e in writer.event_log.read_all())
