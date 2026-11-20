"""The token-gated Web entry must expose existing exact legacy dismissal."""
import pytest

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events import EventLog, EventWriter
from zf.runtime.run_manager import _consume_human_decisions, _pending_human_decisions
from zf.web.server import _web_action


@pytest.fixture
def escalation(tmp_path, monkeypatch):
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-operator-token")
    monkeypatch.delenv("ZF_WEB_TRUSTED_SESSION", raising=False)
    state = tmp_path / ".state"
    state.mkdir()
    writer = EventWriter(EventLog(state / "events.jsonl"))
    event = writer.emit("human.escalate", actor="orchestrator", payload={"reason": "host repair required"})
    config = ZfConfig(project=ProjectConfig(name="test", state_dir=".state"))
    payload = {"decision_token": event.id, "escalation_event_id": event.id,
               "approval_ref": "owner:host-repair", "reason": "host probe now passes"}
    return state, writer, config, payload


def submit(context, payload, *, action="human-decision-dismiss", token="test-operator-token"):
    state, _, config, _ = context
    return _web_action(state, action, payload=payload, authorization=None,
                       x_zf_web_token=token, config=config, project_root=state.parent)


def test_web_exact_legacy_dismissal_is_consumed_without_reopening_work(escalation):
    state, writer, config, payload = escalation
    result = submit(escalation, payload)
    assert result["ok"], result
    events = writer.event_log.read_all()
    acknowledgements = [e for e in events if e.type == "human.escalation.acknowledged"]
    assert len(acknowledgements) == 1
    assert acknowledgements[0].payload["escalation_event_id"] == payload["escalation_event_id"]
    assert acknowledgements[0].payload["approval_ref"] == payload["approval_ref"]
    _consume_human_decisions(state_dir=state, writer=writer, config=config,
                            project_root=state.parent, events=events, causation_id="test-tick")
    assert _pending_human_decisions(writer.event_log.read_all()) == []
    assert not submit(escalation, payload)["ok"]
    assert not any(e.type in {"run.resumed", "repair.action.requested", "task.done"}
                   for e in writer.event_log.read_all())


@pytest.mark.parametrize("fault", ["token", "event", "wrong_event", "approval", "reason", "approve_action"])
def test_legacy_dismissal_keeps_auth_and_exact_review_requirements(escalation, fault):
    _, writer, _, original = escalation
    payload = dict(original)
    kwargs = {}
    if fault == "token":
        kwargs["token"] = "wrong"
    elif fault == "approve_action":
        kwargs["action"] = "human-decision-approve-controlled-action"
    elif fault == "wrong_event":
        payload["escalation_event_id"] = "evt-not-the-current-escalation"
    else:
        payload[{"event": "escalation_event_id", "approval": "approval_ref", "reason": "reason"}[fault]] = ""
    assert not submit(escalation, payload, **kwargs)["ok"]
    assert not any(e.type == "human.escalation.acknowledged" for e in writer.event_log.read_all())
