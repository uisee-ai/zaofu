"""Keep complete provenance and first-occurrence order at large ref counts."""

from datetime import datetime, timezone
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime import goal_dossier as dossier


def _original_source_refs(events):
    """Frozen pre-optimization algorithm, using unchanged extraction rules."""
    refs, digests = [], []
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        for key, value in dossier._walk(payload):
            target = digests if dossier._is_digest_key(key) else refs if dossier._is_ref_key(key) else None
            if target is None:
                continue
            for item in dossier._strings(value):
                if item not in target:
                    target.append(item)
    return refs, digests


def _events():
    nested = {"deep_ref": "depth-seven-hidden"}
    for _ in range(7):
        nested = {"child": nested}
    return [
        ZfEvent(type="worker.progress", payload={
            "artifact_refs": ["b", "a", "b", {"ref": "c", "path": "p", "digest": "same"}],
            "source_digest": "same",
            "artifact_ref": "same",
            "authorization_ref": "hidden-auth",
            "token_ref": "hidden-token",
            # Digest selection has always preceded ref/secret-key filtering.
            "secret_digest": "preserved-digest",
            "blank_ref": "  ",
            "spaced_ref": " a ",
            "nested": nested,
        }),
        ZfEvent(type="worker.progress", payload={
            "artifact_ref": "b", "other_ref": "last", "source_digest": "same",
            "digest": ["second-digest", "same"],
            "ordinary": {"inside_ref": "inside"},
        }),
    ]


def test_source_refs_preserve_order_domains_extraction_and_input():
    events = _events()
    before = [event.to_json() for event in events]
    actual = dossier._source_refs(iter(events))
    assert actual == _original_source_refs(events)
    assert actual == (
        ["b", "a", "c", "p", "same", " a ", "last", "inside"],
        ["same", "preserved-digest", "second-digest"],
    )
    assert [event.to_json() for event in events] == before
    assert dossier._source_refs([]) == ([], [])


class _CountedString(str):
    comparisons = 0
    __hash__ = str.__hash__

    def __eq__(self, other):
        type(self).comparisons += 1
        return super().__eq__(other)


def test_unique_refs_do_not_require_pairwise_string_comparisons():
    count = 2048
    values = [_CountedString(f"artifact/{index}") for index in range(count)]
    events = [ZfEvent(type="worker.progress", payload={"artifact_ref": value}) for value in values]
    _CountedString.comparisons = 0
    refs, digests = dossier._source_refs(events)
    assert refs == values
    assert digests == []
    assert _CountedString.comparisons < count * 8


def test_complete_dossier_matches_original_source_collection(tmp_path: Path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    TaskStore(state / "kanban.json").add(Task(id="T1", title="deliver", status="in_progress"))
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    events = [ZfEvent(type="run.goal.started", correlation_id="run-1", payload={
        "run_id": "run-1", "goal_id": "G1", "objective": "retain all source refs",
    }), *_events(), ZfEvent(type="run.goal.blocked", correlation_id="run-1", payload={
        "run_id": "run-1", "goal_id": "G1", "task_ids": ["T1"], "reason": "owner input",
    })]
    for event in events:
        event.correlation_id = "run-1"
        event.ts = now.isoformat()
    actual = dossier.build_goal_dossier(state, "run-1", events=events, now=now)
    monkeypatch.setattr(dossier, "_source_refs", _original_source_refs)
    expected = dossier.build_goal_dossier(state, "run-1", events=events, now=now)
    assert actual == expected
    assert actual["delivery_readiness"]["status"] == "blocked"
    assert "hidden-auth" not in actual["source_manifest"]["artifact_refs"]
    assert actual["source_manifest"]["event_ids"] == [event.id for event in events]
