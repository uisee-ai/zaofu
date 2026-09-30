"""S10 compatibility contracts for supported historical state/config shapes.

These tests deliberately cover only compatibility behavior that exists in the
current implementation.  They do not invent an upgrade or rollback command;
those remain an explicit release gap until such a migration protocol exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.config.loader import load_config
from zf.core.events.log import EventLog
from zf.core.state.state_packet import (
    StatePacket,
    packet_from_dict,
    packet_to_dict,
)


def _legacy_config() -> str:
    return (
        'version: "1.0"\n'
        "project:\n"
        "  name: legacy-release-fixture\n"
        "  state_dir: legacy-runtime\n"
        "roles: []\n"
    )


def test_legacy_v1_config_loads_without_silent_state_dir_drift(tmp_path: Path) -> None:
    """The documented v1 config shape remains readable by a new build."""
    config_path = tmp_path / "zf.yaml"
    config_path.write_text(_legacy_config(), encoding="utf-8")

    config = load_config(config_path)

    assert config.project.name == "legacy-release-fixture"
    assert config.project.state_dir == "legacy-runtime"
    assert config.roles == []


def test_historical_plain_event_archive_replays_with_active_events(
    tmp_path: Path,
) -> None:
    """Old unsigned JSONL archives remain queryable when signing is off."""
    archive_dir = tmp_path / "events"
    archive_dir.mkdir()
    legacy_event = {
        "type": "legacy.completed",
        "id": "legacy-1",
        "ts": "2026-01-01T00:00:00+00:00",
        "actor": "legacy-worker",
        "task_id": "task-legacy",
        "payload": {"source": "historical-fixture"},
        "causation_id": None,
        "correlation_id": None,
    }
    (archive_dir / "2026-01-01.jsonl").write_text(
        json.dumps(legacy_event) + "\n",
        encoding="utf-8",
    )

    log = EventLog(tmp_path / "events.jsonl")
    events = log.read_all()

    assert [(event.id, event.type) for event in events] == [
        ("legacy-1", "legacy.completed"),
    ]
    assert events[0].payload["source"] == "historical-fixture"


def test_state_packet_accepts_same_major_future_fields_but_rejects_new_major() -> None:
    """StatePacket's explicit v1 compatibility rule is fail-closed."""
    packet = packet_to_dict(StatePacket(task_id="legacy-task"))
    packet["schema_version"] = "1.7"
    packet["future_field"] = {"introduced": "later"}

    restored = packet_from_dict(packet)
    assert restored.schema_version == "1.7"
    assert restored.task_id == "legacy-task"

    packet["schema_version"] = "2.0"
    with pytest.raises(ValueError, match="incompatible"):
        packet_from_dict(packet)
