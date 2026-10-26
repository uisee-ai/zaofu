from pathlib import Path

import pytest

from zf.core.config.schema import ZfConfig
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.runtime.control_actions import ControlledActionService
from zf.runtime import control_actions_candidate_rework as actions
from zf.runtime.replan_resynth import plan_missing_replan_resynth_events


@pytest.mark.parametrize("recover_marker_only", [False, True])
def test_candidate_action_keeps_current_review_context(tmp_path: Path, monkeypatch, recover_marker_only):
    config = ZfConfig()
    config.workflow.admission_replan.enabled = True
    config.workflow.admission_replan.resynth_trigger = "prd.scan.completed"
    writer = EventWriter(EventLog(tmp_path / "events.jsonl"))
    writer.append(ZfEvent(type="prd.scan.completed", correlation_id="run-1", payload={
        "previous_plan_candidate_refs": [{"ref": "old-plan.json", "sha256": "a" * 64}],
    }))
    service = ControlledActionService(tmp_path, writer, config=config, actor="operator")
    if recover_marker_only:
        monkeypatch.setattr(actions, "_build_resynth_event", lambda *args, **kwargs: None)
    context = {
        "previous_plan_candidate_refs": [{"ref": "new-plan.json", "sha256": "b" * 64}],
        "required_actions": ["Keep the repaired environment and reject invalid timing proof"],
        "owner_authorization": {"source": "existing operator repair authorization"},
        "operator_override": True,
    }
    result = service._candidate_rework_apply(
        requested=ZfEvent(type="web.action.requested", actor="web"),
        action="candidate-rework-apply", requested_action="candidate-rework-apply",
        payload={"candidate_rework_action": "replan", "pdd_id": "PDD-1", "trace_id": "run-1",
                 "workflow_run_id": "run-1", "flow_kind": "prd", "source_event_id": "failure-1",
                 "source_event_type": "integration.failed", "rework_attempt": 3, **context},
    )
    assert result["ok"]
    events = writer.event_log.read_all()
    marker = next(e for e in events if e.type == "orchestrator.replan_requested")
    for key, value in context.items():
        assert marker.payload.get(key) == value
    triggers = plan_missing_replan_resynth_events(events, config=config) if recover_marker_only else [
        e for e in events if e.type == "prd.scan.completed" and e.payload.get("rework_of") == "failure-1"
    ]
    assert len(triggers) == 1
    for key, value in context.items():
        assert triggers[0].payload.get(key) == value
