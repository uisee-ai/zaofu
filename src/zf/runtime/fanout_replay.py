"""Fanout fixture recording and deterministic replay."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.atomic_io import atomic_write_text
from zf.runtime.fanout import FanoutManifestProjector


def record_fanout_fixture(
    *,
    event_log: EventLog,
    state_dir: Path,
    fanout_id: str,
    output_path: Path,
) -> dict:
    events = [
        event for event in event_log.read_all()
        if _mentions_fanout(event, fanout_id)
    ]
    manifest = FanoutManifestProjector(state_dir).rebuild(fanout_id, events)
    fixture = {
        "fixture_type": "zf.fanout.v1",
        "fanout_id": fanout_id,
        "events": [asdict(event) for event in events],
        "expected_manifest": manifest,
    }
    atomic_write_text(
        output_path,
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
    )
    return fixture


def replay_fanout_fixture(fixture_path: Path) -> dict:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fanout_id = str(fixture.get("fanout_id") or "")
    events = [
        ZfEvent.from_dict(event)
        for event in fixture.get("events", [])
        if isinstance(event, dict)
    ]
    actual = FanoutManifestProjector(fixture_path.parent).rebuild(fanout_id, events)
    expected = fixture.get("expected_manifest") or {}
    diff = _diff(expected, actual)
    return {
        "status": "matched" if not diff else "mismatch",
        "fanout_id": fanout_id,
        "diff": diff,
        "actual_manifest": actual,
    }


def _mentions_fanout(event: ZfEvent, fanout_id: str) -> bool:
    if isinstance(event.payload, dict) and event.payload.get("fanout_id") == fanout_id:
        return True
    return event.type.startswith("fanout.") and event.correlation_id == fanout_id


def _diff(expected: Any, actual: Any, path: str = "$") -> list[dict]:
    if type(expected) is not type(actual):
        return [{"path": path, "expected": expected, "actual": actual}]
    if isinstance(expected, dict):
        out: list[dict] = []
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                out.append({"path": f"{path}.{key}", "expected": None, "actual": actual[key]})
            elif key not in actual:
                out.append({"path": f"{path}.{key}", "expected": expected[key], "actual": None})
            else:
                out.extend(_diff(expected[key], actual[key], f"{path}.{key}"))
        return out
    if isinstance(expected, list):
        out = []
        for index in range(max(len(expected), len(actual))):
            if index >= len(expected):
                out.append({"path": f"{path}[{index}]", "expected": None, "actual": actual[index]})
            elif index >= len(actual):
                out.append({"path": f"{path}[{index}]", "expected": expected[index], "actual": None})
            else:
                out.extend(_diff(expected[index], actual[index], f"{path}[{index}]"))
        return out
    if expected != actual:
        return [{"path": path, "expected": expected, "actual": actual}]
    return []
