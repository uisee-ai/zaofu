"""Prevent dedup from hiding every owner question behind a merge alias cycle."""

from pathlib import Path

import pytest

from zf.core.events import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.channel_projection import project_channel
from zf.runtime.channel_question_dedup import (
    apply_question_dedup_reply,
    question_ledger_digest,
)


CHANNEL = "ch-merge-dependencies"


def _open(writer: EventWriter, question_id: str, dependencies: list[str]) -> None:
    writer.emit("channel.question.opened", actor="critic", payload={
        "channel_id": CHANNEL, "thread_id": "main", "question_id": question_id,
        "question": f"Decide {question_id}?", "kind": "owner_decision",
        "target_member_id": "owner", "depends_on": dependencies,
    })


def _apply(state: Path, writer: EventWriter, source: str, target: str,
           updates: list[dict] | None = None, request: str = "dedup-1") -> tuple[bool, str]:
    return apply_question_dedup_reply(
        state_dir=state, writer=writer, channel_id=CHANNEL, thread_id="main",
        request_id=request, actor="synthesizer", source="test", causation_id="review-1",
        payload={
            "ledger_digest": question_ledger_digest(project_channel(state, CHANNEL), thread_id="main"),
            "groups": [{"canonical_question_id": target, "merge_question_ids": [source]}],
            "question_updates": updates or [],
        },
    )


@pytest.mark.parametrize("indirect", [False, True])
def test_dedup_rejects_dependency_cycle_before_any_question_write(tmp_path: Path, indirect: bool) -> None:
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    _open(writer, "q-a", [])
    _open(writer, "q-b", ["q-a"])
    _open(writer, "q-c", ["q-b"] if indirect else ["q-a"])
    before = len(writer.event_log.read_all())

    ok, reason = _apply(tmp_path, writer, "q-a", "q-c" if indirect else "q-b",
                        [{"question_id": "q-b", "priority": "p0"}])

    assert not ok
    assert reason.startswith("question_dependency_cycle:")
    emitted = writer.event_log.read_all()[before:]
    assert [event.type for event in emitted] == ["channel.question.dedup.rejected"]
    detail = project_channel(tmp_path, CHANNEL)
    assert [q["question_id"] for q in detail["owner_questionnaires"]["main"]] == ["q-a"]
    assert all(q["status"] == "open" for q in detail["open_questions"])


def test_dedup_checks_dependencies_through_existing_merge_aliases(tmp_path: Path) -> None:
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    _open(writer, "q-old", [])
    _open(writer, "q-a", [])
    assert _apply(tmp_path, writer, "q-old", "q-a") == (True, "applied")
    _open(writer, "q-b", ["q-old"])

    ok, reason = _apply(tmp_path, writer, "q-a", "q-b", request="dedup-2")

    assert not ok
    assert reason.startswith("question_dependency_cycle:")


def test_explicit_dependency_update_keeps_owner_answers_and_unblocks_in_order(tmp_path: Path) -> None:
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    _open(writer, "q-a", [])
    _open(writer, "q-b", ["q-a"])
    _open(writer, "q-c", ["q-a"])

    assert _apply(tmp_path, writer, "q-a", "q-b", [{
        "question_id": "q-b", "depends_on": [],
    }]) == (True, "applied")
    detail = project_channel(tmp_path, CHANNEL)
    assert [q["question_id"] for q in detail["owner_questionnaires"]["main"]] == ["q-b"]
    assert not any(q["status"] == "resolved" for q in detail["open_questions"])
    before = len(writer.event_log.read_all())
    assert _apply(tmp_path, writer, "q-a", "q-b") == (True, "already_applied")
    assert len(writer.event_log.read_all()) == before

    writer.emit("channel.question.resolved", actor="operator", payload={
        "channel_id": CHANNEL, "thread_id": "main", "question_id": "q-b",
        "resolution": "answered", "resolved_by": "operator", "answer": "Owner choice",
    })
    detail = project_channel(tmp_path, CHANNEL)
    assert [q["question_id"] for q in detail["owner_questionnaires"]["main"]] == ["q-c"]


def test_merging_dependent_into_predecessor_remains_valid(tmp_path: Path) -> None:
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    _open(writer, "q-a", [])
    _open(writer, "q-b", ["q-a"])
    assert _apply(tmp_path, writer, "q-b", "q-a") == (True, "applied")
    detail = project_channel(tmp_path, CHANNEL)
    assert [q["question_id"] for q in detail["owner_questionnaires"]["main"]] == ["q-a"]


def test_dedup_rejects_update_order_that_replay_would_partially_apply(tmp_path: Path) -> None:
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    _open(writer, "q-a", [])
    _open(writer, "q-b", ["q-a"])
    _open(writer, "q-c", ["q-b"])
    before = len(writer.event_log.read_all())

    ok, reason = _apply(tmp_path, writer, "q-a", "q-b", [
        {"question_id": "q-b", "depends_on": ["q-c"]},
        {"question_id": "q-c", "depends_on": []},
    ])

    assert not ok
    assert reason.startswith("question_dependency_cycle:")
    assert [e.type for e in writer.event_log.read_all()[before:]] == ["channel.question.dedup.rejected"]
    detail = project_channel(tmp_path, CHANNEL)
    assert not detail["question_graph_rejections"]
    assert [q["question_id"] for q in detail["owner_questionnaires"]["main"]] == ["q-a"]


def test_dedup_allows_dependency_updates_in_replay_safe_order(tmp_path: Path) -> None:
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    _open(writer, "q-a", [])
    _open(writer, "q-b", ["q-a"])
    _open(writer, "q-c", ["q-b"])

    assert _apply(tmp_path, writer, "q-a", "q-b", [
        {"question_id": "q-c", "depends_on": []},
        {"question_id": "q-b", "depends_on": ["q-c"]},
    ]) == (True, "applied")
    detail = project_channel(tmp_path, CHANNEL)
    assert not detail["question_graph_rejections"]
    by_id = {q["question_id"]: q for q in detail["open_questions"]}
    assert by_id["q-b"]["depends_on"] == ["q-c"]
    assert by_id["q-c"]["depends_on"] == []
    assert [q["question_id"] for q in detail["owner_questionnaires"]["main"]] == ["q-c"]
