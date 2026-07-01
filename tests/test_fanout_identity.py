from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.fanout_identity import (
    build_fanout_identity_projection,
    fanout_current_status,
    read_fanout_identities,
)


def _started(fanout_id: str, event_id: str) -> ZfEvent:
    return ZfEvent(
        id=event_id,
        type="fanout.started",
        payload={
            "fanout_id": fanout_id,
            "stage_id": "review",
            "topology": "fanout_reader",
            "target_ref": "candidate/CJMIN-1",
            "pdd_id": "CJMIN-1",
            "expected_children": [{"child_id": "review-0"}],
        },
    )


def test_fanout_identity_projects_wave_compat_fields() -> None:
    projection = build_fanout_identity_projection([
        ZfEvent(
            id="evt-start-wave",
            type="fanout.started",
            payload={
                "fanout_id": "fanout-review-wave",
                "stage_id": "review",
                "target_ref": "candidate/CJMIN-1",
                "wave_id": "review-wave-A",
                "wave_index": "2",
                "expected_total": 4,
                "wave_attempt": "3",
            },
        ),
    ])

    current = projection["current"][0]
    assert current["fanout_id"] == "fanout-review-wave"
    assert current["wave_alias"] == "review-wave-A"
    assert current["wave_index"] == 2
    assert current["wave_total"] == 4
    assert current["attempt"] == 3


def test_latest_fanout_identity_marks_late_old_child_stale() -> None:
    events = [
        _started("fanout-review-old", "evt-start-old"),
        ZfEvent(
            type="fanout.child.completed",
            payload={"fanout_id": "fanout-review-old", "child_id": "review-0"},
        ),
        _started("fanout-review-new", "evt-start-new"),
        ZfEvent(
            type="fanout.child.failed",
            payload={
                "fanout_id": "fanout-review-old",
                "child_id": "review-0",
                "reason": "late rejection",
            },
        ),
        ZfEvent(
            type="fanout.child.completed",
            payload={"fanout_id": "fanout-review-new", "child_id": "review-0"},
        ),
        ZfEvent(
            type="fanout.aggregate.completed",
            payload={"fanout_id": "fanout-review-old", "status": "completed"},
        ),
    ]

    projection = build_fanout_identity_projection(events)

    assert projection["summary"]["current_instances"] == 1
    assert projection["summary"]["stale_instances"] == 1
    assert projection["summary"]["stale_event_count"] == 2
    assert projection["current"][0]["fanout_id"] == "fanout-review-new"
    assert projection["stale"][0]["fanout_id"] == "fanout-review-old"
    assert projection["stale"][0]["superseded_by"] == "fanout-review-new"
    assert projection["stale_events"][0]["event_type"] == "fanout.child.failed"
    assert (
        projection["stale_events"][0]["stale_reason"]
        == "superseded_by_latest_fanout"
    )
    assert projection["current"][0]["child_events"][0]["event_type"] == (
        "fanout.child.completed"
    )


def test_explicit_stale_completion_is_visible() -> None:
    projection = build_fanout_identity_projection([
        _started("fanout-writer", "evt-start"),
        ZfEvent(
            type="fanout.child.stale_completion",
            payload={
                "fanout_id": "fanout-writer",
                "child_id": "dev-0",
                "reason": "run_id mismatch",
            },
        ),
    ])

    assert projection["summary"]["current_instances"] == 1
    assert projection["summary"]["stale_event_count"] == 1
    assert projection["current"][0]["stale_events"][0]["stale_reason"] == (
        "run_id mismatch"
    )


def test_fanout_current_status_fails_open_for_unknown_legacy_fanout() -> None:
    status = fanout_current_status([], "fanout-legacy")

    assert status.known is False
    assert status.current is True


def test_fanout_current_status_identifies_superseded_instance() -> None:
    status = fanout_current_status([
        _started("fanout-review-old", "evt-start-old"),
        _started("fanout-review-new", "evt-start-new"),
    ], "fanout-review-old")

    assert status.known is True
    assert status.current is False
    assert status.superseded_by == "fanout-review-new"
    assert status.stale_reason == "superseded_by_latest_fanout"


def test_fanout_identity_web_api(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from zf.web.server import create_app

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(_started("fanout-review", "evt-start"))

    client = TestClient(create_app(state_dir))
    response = client.get("/api/fanout-identities")
    data = response.json()

    assert response.status_code == 200
    assert data["schema_version"] == "fanout-identity.v1"
    assert data["summary"]["current_instances"] == 1
    assert data["current"][0]["fanout_instance_id"] == "fanout-review"


def test_read_fanout_identities_rebuilds_from_event_log(tmp_path: Path) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    (state_dir / "feature_list.json").write_text("[]\n", encoding="utf-8")
    writer = EventWriter(EventLog(state_dir / "events.jsonl"))
    writer.append(_started("fanout-review", "evt-start"))

    projection = read_fanout_identities(state_dir)

    assert projection["current"][0]["fanout_id"] == "fanout-review"
