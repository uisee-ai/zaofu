from datetime import datetime, timedelta, timezone

from zf.core.events.model import ZfEvent
from zf.runtime.resident_request_obligations import resident_request_obligations
from zf.runtime.run_manager import build_resident_agent_projection
from tests.test_run_manager_resident import _resident_config
from zf.runtime.run_manager import _pending_resident_agent_actions
from zf.core.config.schema import RuntimeRunManagerResidentAgentConfig
import pytest


def _events():
    now = datetime.now(timezone.utc)
    earlier = (now - timedelta(seconds=700)).isoformat()
    return now, [
        ZfEvent(type="run.manager.resident.spawned", ts=earlier),
        ZfEvent(type="run.manager.agent.observation", actor="run-manager", ts=earlier),
        ZfEvent(type="run.manager.resident.prompted", ts=earlier, payload={
            "prompted": True, "request_id": "request-2", "instance_id": "run-manager",
            "expected_output": ["run.manager.agent.recommendation"],
        }),
    ]


def test_old_or_unrelated_observation_cannot_answer_new_request():
    now, events = _events()
    events.append(ZfEvent(type="run.manager.agent.observation", actor="run-manager",
                         payload={"request_id": "request-2"}))
    assert resident_request_obligations(events, now=now, timeout_seconds=600)[0]["status"] == "overdue"
    result = build_resident_agent_projection(events, config=_resident_config(), now=now)
    assert result["stalled"] is True
    assert result["requests"][0]["request_id"] == "request-2"


def test_matching_response_completes_request_on_replay():
    now, events = _events()
    events.append(ZfEvent(type="run.manager.agent.recommendation", actor="run-manager",
                         payload={"request_id": "request-2"}))
    assert resident_request_obligations(list(events), now=now, timeout_seconds=600) == []
    assert build_resident_agent_projection(events, config=_resident_config(), now=now)["stalled"] is False


def test_unbound_startup_prompt_and_idle_are_not_obligations():
    now, events = _events()
    events[-1].payload = {"prompted": True}
    assert resident_request_obligations(events, now=now, timeout_seconds=600) == []


def test_overdue_request_has_runtime_recovery_exit_without_agent_permission():
    now, events = _events()
    projection = build_resident_agent_projection(events, config=_resident_config(), now=now)
    actions = _pending_resident_agent_actions(projection, events)
    assert len(actions) == 1
    assert actions[0]["action"] == "diagnose-attention"
    assert actions[0]["request_id"] == "request-2"
    assert "resident_agent_diagnosis" in actions[0]["expected_output"]


@pytest.mark.parametrize("value", [0, -1, True, "600"])
def test_request_timeout_config_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="response_timeout_seconds"):
        RuntimeRunManagerResidentAgentConfig(response_timeout_seconds=value)


def test_pause_time_is_excluded_from_current_request_deadline():
    now, events = _events()
    events.extend([
        ZfEvent(type="dispatch.paused", ts=(now - timedelta(seconds=650)).isoformat()),
        ZfEvent(type="dispatch.resumed", ts=(now - timedelta(seconds=10)).isoformat()),
    ])
    row = resident_request_obligations(events, now=now, timeout_seconds=600)[0]
    assert row["status"] == "pending"
    assert row["age_seconds"] == pytest.approx(60)
