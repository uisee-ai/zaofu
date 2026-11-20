from types import SimpleNamespace

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.candidate_rework_generation import current_rework_task_map_replay_candidates
from zf.runtime.orchestrator import Orchestrator


def ready(run="run-1", *, rework=True):
    return ZfEvent(type="task_map.ready", origin="kernel", actor="zf-cli", correlation_id=run,
                   payload={"workflow_run_id": run, "pdd_id": "PDD-1", "task_map_generation": "gen-1",
                            **({"rework_of": "old-rejection"} if rework else {})})


@pytest.mark.parametrize("boundary", ["ready", "authorized_generation"])
def test_restart_does_not_readmit_old_rework_after_new_frontier(boundary):
    old, other = ready(), ready("run-2")
    if boundary == "ready":
        latest = ready(rework=False)
    else:
        latest = ZfEvent(type="run.goal.updated", actor="zf-cli", origin="kernel", correlation_id="run-1",
                         payload={"run_id": "run-1", "status": "active",
                                  "reason": "authorized_stage_replan_new_generation",
                                  "stage_replan_generation": "generation-new"})
    events = [old, other, latest]
    assert current_rework_task_map_replay_candidates(events) == [other]
    dispatched = []
    runtime = SimpleNamespace(_maybe_start_writer_fanout=dispatched.append)
    Orchestrator._resume_unstarted_rework_task_maps(runtime, events)
    assert dispatched == [other]
    fresh = ready()
    assert current_rework_task_map_replay_candidates([*events, fresh]) == [other, fresh]


def test_ordinary_goal_update_does_not_retire_rework():
    old = ready()
    update = ZfEvent(type="run.goal.updated", actor="zf-cli", origin="kernel", correlation_id="run-1",
                     payload={"run_id": "run-1", "status": "active", "reason": "operator_note"})
    assert current_rework_task_map_replay_candidates([old, update]) == [old]
