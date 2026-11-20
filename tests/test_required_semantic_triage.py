from pathlib import Path

import pytest

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick
from zf.runtime.run_manager_rework_triage import pending_rework_triage_actions


def _cap(*, required: bool = True, count: int = 1) -> ZfEvent:
    return ZfEvent(
        type="task.pipeline.semantic_rework.exhausted",
        actor="zf-runtime", task_id="T1", correlation_id="RUN-1",
        payload={
            "workflow_run_id": "RUN-1", "stage_id": "verify",
            "failure_class": "task_pipeline_semantic_rework_exhausted",
            "failure_fingerprint": "semantic:T1:verify",
            "failure_count": count, "failure_event_ids": ["verify-result"],
            "semantic_triage_required": required,
        },
    )


@pytest.mark.parametrize("threshold", [0, 3, 10])
def test_required_cap_routes_independently_of_legacy_threshold(threshold: int) -> None:
    actions = pending_rework_triage_actions(
        [_cap()], threshold=threshold, stale_seconds=600,
    )
    assert len(actions) == 1
    assert actions[0]["action"] == "orchestrator-rework-triage"


def test_disabled_legacy_triage_still_ignores_unmarked_cap() -> None:
    assert pending_rework_triage_actions(
        [_cap(required=False, count=100)], threshold=0, stale_seconds=600,
    ) == []


def test_default_config_tick_requests_only_bounded_triage(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "kanban.json").write_text("[]\n")
    (state / "feature_list.json").write_text("[]\n")
    log = EventLog(state / "events.jsonl")
    writer = EventWriter(log, default_origin="kernel")
    writer.append(_cap())
    config = ZfConfig(roles=[RoleConfig(name="orchestrator", backend="mock")])
    projection = build_run_manager_projection(
        state, events=log.read_all(), config=config, project_root=tmp_path,
    )
    actions = [a for a in projection["pending_actions"] if a.get("task_id") == "T1"]
    assert len(actions) == 1
    assert actions[0]["policy_decision"]["decision"] == "auto_decide"
    for _ in range(2):
        run_manager_tick(
            state_dir=state, writer=writer, config=config,
            project_root=tmp_path, event_log=log, spawn_repairs=False,
            action_filter={"orchestrator-rework-triage"},
        )
    types = [e.type for e in log.read_all()]
    assert types.count("orchestrator.rework.triage.requested") == 1
    assert not set(types) & {"task.rework.requested", "task.done", "task_map.ready"}
