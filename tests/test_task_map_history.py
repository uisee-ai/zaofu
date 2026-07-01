"""Tests for task-map re-plan version chain (doc 69 §14.10, slice S-k)."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.task_map_history import build_task_map_history


def _publish(event_id: str, *, refs: list[dict], feature_id: str = "F-1",
             ts: str = "2026-05-30T00:00:00+00:00") -> ZfEvent:
    return ZfEvent(
        id=event_id, type="artifact.manifest.published", actor="arch",
        task_id="T-arch", ts=ts,
        payload={"feature_id": feature_id, "role": "arch", "artifact_refs": refs},
    )


def _ref(**over) -> dict:
    base = {"kind": "task_map", "path": ".zf/artifacts/F-1/task_map.json",
            "sha256": "a" * 64, "summary": "task map", "status": "accepted"}
    base.update(over)
    return base


def _slice(events: list[ZfEvent]):
    return list(enumerate(events))


def test_empty_when_no_task_map_published():
    events = _slice([ZfEvent(id="e1", type="dev.build.done", task_id="T-x")])
    assert build_task_map_history(events, feature_id="F-1") == []


def test_single_task_map_is_current_not_superseded():
    events = _slice([_publish("e1", refs=[_ref(artifact_id="tm-v1", version=1)])])
    hist = build_task_map_history(events, feature_id="F-1")
    assert len(hist) == 1
    assert hist[0]["is_current"] is True
    assert hist[0]["superseded"] is False
    assert hist[0]["version"] == 1
    assert hist[0]["ref"] == ".zf/artifacts/F-1/task_map.json"


def test_replan_with_explicit_supersedes_marks_v1_superseded():
    events = _slice([
        _publish("e1", refs=[_ref(artifact_id="tm-v1", version=1)]),
        _publish("e2", refs=[_ref(artifact_id="tm-v2", version=2,
                                  supersedes="tm-v1", summary="re-cut router")]),
    ])
    hist = build_task_map_history(events, feature_id="F-1")
    assert len(hist) == 2
    v1, v2 = hist
    assert v1["artifact_id"] == "tm-v1"
    assert v1["superseded"] is True and v1["is_current"] is False
    assert v2["is_current"] is True and v2["superseded"] is False
    assert v2["reason"] == "re-cut router"


def test_replan_without_version_metadata_uses_publish_order():
    # Raw worker manifests omit version/artifact_id; last published wins.
    events = _slice([
        _publish("e1", refs=[_ref()]),
        _publish("e2", refs=[_ref()]),
    ])
    hist = build_task_map_history(events, feature_id="F-1")
    assert [e["event_id"] for e in hist] == ["e1", "e2"]
    assert hist[0]["superseded"] is True and hist[0]["is_current"] is False
    assert hist[1]["is_current"] is True and hist[1]["superseded"] is False


def test_feature_filter_excludes_other_features():
    events = _slice([
        _publish("e1", refs=[_ref(artifact_id="tm-v1", version=1)], feature_id="F-2"),
        _publish("e2", refs=[_ref(artifact_id="tm-v9", version=9)], feature_id="F-1"),
    ])
    hist = build_task_map_history(events, feature_id="F-1")
    assert len(hist) == 1
    assert hist[0]["artifact_id"] == "tm-v9"


def test_non_task_map_refs_ignored():
    events = _slice([_publish("e1", refs=[
        _ref(kind="spec", path="docs/spec.md"),
        _ref(kind="implementation_plan", path="docs/plan.md"),
    ])])
    assert build_task_map_history(events, feature_id="F-1") == []


def test_kind_alias_normalized():
    # "work-unit-map" / "task-map" normalize to task_map.
    events = _slice([_publish("e1", refs=[_ref(kind="work-unit-map")])])
    hist = build_task_map_history(events, feature_id="F-1")
    assert len(hist) == 1 and hist[0]["is_current"] is True
