"""Requirement selection is explicit; runtime only checks immutable refs and scope."""

import json

import pytest

from zf.core.events import ZfEvent
from zf.web.plan_runtime import prepare_headless_plan_draft
from zf.runtime.kanban_plan_requests import plan_requirement_digest


def message(event_id, text, **scope):
    return ZfEvent(id=event_id, type="user.message", actor="web", payload={
        "project_id": "p", "conversation_id": "kanban:p", "thread_key": "main",
        "message": text, **scope,
    })


def draft(events, refs=None, *, cutoff=""):
    request = {"header": "Route", "subject_type": "clarification", "question": "Which scope?",
               "options": [{"id": "a", "label": "Small (Recommended)"}, {"id": "b", "label": "Large"}]}
    if refs is not None:
        request["originating_message_event_ids"] = refs
    result, _ = prepare_headless_plan_draft(
        events, answer=json.dumps({"plan_request": request}), action_proposal=None,
        project_id="p", conversation_id="kanban:p", thread_key="main", fallback_thread_id="main",
        originating_message_event_id="current", turn_id="turn", backend="codex-headless",
        provider_session_id="session", task_id=None, correlation_id="trace",
        requirement_source_cutoff_event_id=cutoff,
    )
    assert result is not None
    return result.request


def test_default_does_not_swallow_old_recovery_instruction():
    current = message("current", "Create a requirements discussion for the new editor.")
    result = draft([message("recovery", "Repair the timeout and retry the old group."), current])
    assert result["valid"], result["validation_error"]
    assert result["originating_message_event_ids"] == ["current"]
    assert result["requirement_digest"] == plan_requirement_digest([("current", current.payload["message"])])


def test_explicit_refs_keep_ledger_order_and_always_bind_current_intent():
    previous = message("previous", "Editor must support search.")
    current = message("current", "Also support replace; create the discussion.")
    result = draft([previous, current], ["previous"])
    assert result["valid"], result["validation_error"]
    assert result["originating_message_event_ids"] == ["previous", "current"]


@pytest.mark.parametrize("scope", [
    {"project_id": "other"}, {"conversation_id": "other"}, {"thread_key": "other"},
    {"project_id": ""}, {"conversation_id": ""}, {"thread_key": ""},
])
def test_explicit_refs_reject_cross_scope_and_missing_scope(scope):
    result = draft([message("other", "unrelated", **scope), message("current", "new requirement")], ["other"])
    assert not result["valid"]
    assert "requirement refs" in result["validation_error"]


@pytest.mark.parametrize("refs", [["missing"], ["future"], "current", [42], []])
def test_invalid_and_future_refs_are_rejected(refs):
    result = draft([message("current", "new requirement"), message("future", "later")], refs)
    assert not result["valid"]


@pytest.mark.parametrize("refs", [None, ["current"], ["continued"]])
def test_continuation_keeps_origin_and_current_input(refs):
    original = message("current", "Plan the editor.")
    continued = message("continued", "Keep replace in the small scope.")
    result = draft([original, continued], refs, cutoff="continued")
    assert result["valid"], result["validation_error"]
    assert result["originating_message_event_id"] == "current"
    assert result["originating_message_event_ids"] == ["current", "continued"]
    assert result["requirement_digest"] == plan_requirement_digest([
        ("current", original.payload["message"]), ("continued", continued.payload["message"]),
    ])


@pytest.mark.parametrize("scope", [{"project_id": "other"}, {"conversation_id": "other"},
                                  {"thread_key": "other"}])
def test_continuation_still_rejects_cross_scope_refs(scope):
    result = draft([message("current", "Plan"), message("foreign", "Private", **scope),
                    message("continued", "Small scope")], ["foreign"], cutoff="continued")
    assert not result["valid"]


def test_continuation_rejects_future_source_and_unknown_cutoff():
    events = [message("current", "Plan"), message("continued", "Small scope"),
              message("future", "Unseen later requirement")]
    assert not draft(events, ["future"], cutoff="continued")["valid"]
    assert not draft(events, cutoff="missing")["valid"]
