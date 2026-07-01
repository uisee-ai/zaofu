"""Bypass-loop meta-bug 2026-05-31: an orphan ``user.message`` in the
inner harness used to be silent — no inline-override match meant the
``_scan_inline_overrides`` no-op branch returned without emitting any
observable signal, so reflection could not tell whether the message had
been received or simply lost in transport.

This test pins the new ``user.message.unrouted`` observability event:
when a human-actor ``user.message`` with non-empty text matches no
inline-override pattern, the orchestrator must append a
``user.message.unrouted`` event so the orphan is visible in
``events.jsonl``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from zf.core.config.schema import WorkflowConfig, WorkflowInlineOverrides
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.events.log import EventLog
from zf.runtime.orchestrator import Orchestrator


@dataclass
class _StubConfig:
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)


def _scan_only_harness(tmp_path: Path) -> tuple[object, Path]:
    """Build a minimum stand-in object exposing the two attributes
    ``Orchestrator._scan_inline_overrides`` touches: ``config`` and
    ``event_writer``. Avoids the heavy Orchestrator constructor."""

    events_path = tmp_path / "events.jsonl"
    log = EventLog(events_path)
    writer = EventWriter(log)

    cfg = _StubConfig()
    cfg.workflow.inline_overrides = WorkflowInlineOverrides(
        enabled=True,
        patterns={
            "skip_critic": ["skip critic", "别走 critic"],
            "skip_test": ["skip test"],
        },
    )

    class _Harness:
        pass

    harness = _Harness()
    harness.config = cfg
    harness.event_writer = writer
    return harness, events_path


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    import json

    out: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def test_user_message_with_no_pattern_match_emits_unrouted(tmp_path: Path) -> None:
    harness, events_path = _scan_only_harness(tmp_path)
    event = ZfEvent(
        type="user.message",
        actor="user",
        payload={"text": "bypass-loop seed: please add tests/foo.txt"},
    )

    Orchestrator._scan_inline_overrides(harness, event)

    events = _read_events(events_path)
    unrouted = [e for e in events if e.get("type") == "user.message.unrouted"]
    assert len(unrouted) == 1, f"expected exactly one unrouted event, got {events}"

    payload = unrouted[0].get("payload", {})
    assert payload.get("message_id") == event.id
    assert payload.get("reason") == "no_inline_override_match"
    # scanned_patterns lists the inline-override keys we scanned against
    assert sorted(payload.get("scanned_patterns", [])) == ["skip_critic", "skip_test"]
    assert payload.get("actor_hint") == "user"
    assert "bypass-loop seed" in payload.get("text_excerpt", "")


def test_user_message_with_pattern_match_does_not_emit_unrouted(tmp_path: Path) -> None:
    """Sanity: when a pattern matches, the legacy workflow.inline_override
    audit fires and the new unrouted event must NOT fire."""

    harness, events_path = _scan_only_harness(tmp_path)
    event = ZfEvent(
        type="user.message",
        actor="user",
        payload={"text": "please skip critic on this one"},
    )

    Orchestrator._scan_inline_overrides(harness, event)

    events = _read_events(events_path)
    types = [e.get("type") for e in events]
    assert "user.message.unrouted" not in types
    assert "workflow.inline_override" in types
