"""Required fact coverage must not be rejected by the optional review cap."""
import pytest

from tests.test_channel_question_dedup import CHANNEL_ID, _apply, _invite, _open, _writer
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_question_dedup import question_ledger_digest
from zf.runtime.channel_reply_prompt import channel_reply_response_contract


def _plan(tmp_path, facts, optional, explicit=True):
    state, writer = _writer(tmp_path)
    _invite(writer, "arch")
    ids = [f"q-{i}" for i in range(facts + optional)]
    for qid in ids:
        _open(writer, qid)
    payload = {
        "ledger_digest": question_ledger_digest(project_channel(state, CHANNEL_ID), thread_id="main"),
        "groups": [],
        "question_updates": [{"question_id": qid, "kind": "fact", "target_member_id": "arch"}
                             for qid in ids[:facts]],
        "cross_review_requests": [{"question_id": qid, "target_member_ids": ["arch"],
                                   "prompt": "Verify the evidence", "reason": "Unverified fact"}
                                  for qid in (ids if explicit else ids[facts:])],
    }
    return state, writer, payload


@pytest.mark.parametrize("explicit", [True, False])
def test_eleven_required_fact_reviews_apply_once(tmp_path, explicit):
    state, writer, plan = _plan(tmp_path, 11, 0, explicit)
    assert _apply(state, writer, plan) == (True, "applied")
    detail = project_channel(state, CHANNEL_ID)
    assert len(detail["cross_reviews"]) == 11
    assert {r["question_id"] for r in detail["cross_reviews"]} == {f"q-{i}" for i in range(11)}
    count = len(writer.event_log.read_all())
    assert _apply(state, writer, plan) == (True, "already_applied")
    assert len(writer.event_log.read_all()) == count


@pytest.mark.parametrize("facts,optional", [(0, 9), (11, 1)])
def test_optional_reviews_cannot_expand_required_review_budget(tmp_path, facts, optional):
    state, writer, plan = _plan(tmp_path, facts, optional)
    assert _apply(state, writer, plan) == (False, "cross_review_request_limit_exceeded")
    assert not project_channel(state, CHANNEL_ID)["cross_reviews"]
    assert not any(e.type == "channel.question.updated" for e in writer.event_log.read_all())


def test_dedup_prompt_explains_required_fact_review_budget():
    prompt = channel_reply_response_contract({}, {}, {"refs": {"question_dedup_request_id": "dedup-1"}})
    assert "at most 8 request objects" in prompt
    assert "more than 8 fact questions survive" in prompt
    assert "at most 4 members" in prompt
