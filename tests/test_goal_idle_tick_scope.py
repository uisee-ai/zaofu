from pathlib import Path
from types import SimpleNamespace

from zf.core.config.schema import GoalConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.tick_services import (
    TickServiceIntervals,
    TickServiceState,
    run_standard_tick_services,
)


def test_heartbeat_tick_excludes_pre_goal_rescan_from_current_run_cap(tmp_path: Path) -> None:
    state_dir = tmp_path / "custom-state"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    writer.append(ZfEvent(type="goal.rescan.requested", actor="zf-cli", payload={}))
    writer.append(ZfEvent(type="run.goal.started", actor="zf-cli", payload={
        "run_id": "current", "objective": "finish current delivery",
    }))
    writer.append(ZfEvent(type="dev.build.done", actor="dev", payload={
        "workflow_run_id": "current",
    }))
    config = ZfConfig(goal=GoalConfig(
        enabled=True, idle_progress_ticks=1, max_rescans=1,
    ))
    orchestrator = SimpleNamespace(
        state_dir=state_dir, project_root=tmp_path, config=config,
        event_log=log, event_writer=writer,
    )
    state = TickServiceState()
    for now in (1.0, 2.0, 3.0):
        result = run_standard_tick_services(
            orchestrator, state=state, now=now,
            intervals=TickServiceIntervals(heartbeat_sweep_s=0),
        )
        assert result.heartbeat_sweep
    events = log.read_all()
    rescans = [event for event in events if event.type == "goal.rescan.requested"]
    assert len(rescans) == 2
    assert rescans[-1].payload["rescan_ordinal"] == 1
    assert rescans[-1].payload["workflow_run_id"] == "current"
    assert rescans[-1].correlation_id == "current"
    assert not [event for event in events if event.type == "human.escalate"]
