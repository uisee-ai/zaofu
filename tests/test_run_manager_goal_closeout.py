"""Authoritative goal completion must not acquire a contradictory legacy closeout."""
from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.run_manager import run_manager_tick
from tests.test_run_manager import _config, _state


def test_goal_completion_does_not_emit_legacy_unshipped_closeout(tmp_path: Path) -> None:
    state_dir, log, writer = _state(tmp_path)
    terminal = ZfEvent(
        type="run.goal.completed",
        origin="kernel",
        payload={
            "run_id": "R4",
            "pdd_id": "CANGJIE-R4",
            "delivery_status": "settled",
            "delivery_policy": "ship_candidate",
            "candidate_head_commit": "abc1234",
        },
    )
    log.append(terminal)
    for _ in range(2):
        result = run_manager_tick(
            state_dir=state_dir, writer=writer, config=_config(),
            event_log=log, spawn_repairs=False,
        )
        assert result.closeout_events == 0
    assert not any(event.type == "run.completed" for event in log.read_all())
    assert any(event.id == terminal.id for event in log.read_all())
