"""blocked 角色烧钱看门狗(r5:dev-flow blocked_human 冷却期烧 30M)。"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.tick_services import (
    TickServiceIntervals,
    TickServiceState,
    _emit_blocked_role_burn_if_needed,
)


def _run(tmp_path: Path, events: list[ZfEvent]) -> list[str]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir(exist_ok=True)
    log = EventLog(state_dir / "events.jsonl")
    for event in events:
        log.append(event)
    writer = EventWriter(log)
    _emit_blocked_role_burn_if_needed(
        event_log=log,
        event_writer=writer,
        state_dir=state_dir,
        state=TickServiceState(),
        intervals=TickServiceIntervals(blocked_burn_tokens=1000),
    )
    return [e.type for e in log.read_all()]


def _blocked(instance: str) -> ZfEvent:
    return ZfEvent(type="worker.state.changed", actor=instance,
                   payload={"instance_id": instance, "state": "blocked_human"})


def _usage(instance: str, tokens: int) -> ZfEvent:
    return ZfEvent(type="agent.usage", actor=f"role:{instance}",
                   payload={"instance_id": instance,
                            "usage": {"input_tokens": tokens, "output_tokens": 0}})


def test_blocked_role_burning_emits(tmp_path: Path) -> None:
    types = _run(tmp_path, [_blocked("dev-flow"), _usage("dev-flow", 1500)])
    assert "cost.blocked_role_burn" in types


def test_active_role_burning_does_not_emit(tmp_path: Path) -> None:
    types = _run(tmp_path, [_usage("dev-flow", 5000)])
    assert "cost.blocked_role_burn" not in types


def test_unblocked_resets_counter(tmp_path: Path) -> None:
    types = _run(tmp_path, [
        _blocked("dev-flow"), _usage("dev-flow", 1500),
        ZfEvent(type="worker.state.changed", actor="dev-flow",
                payload={"instance_id": "dev-flow", "state": "busy"}),
    ])
    assert "cost.blocked_role_burn" not in types
