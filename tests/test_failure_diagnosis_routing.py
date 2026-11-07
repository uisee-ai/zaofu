from pathlib import Path

import pytest

from tests.test_run_manager_rework_triage import _state
from zf.core.config.loader import load_config
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.rework_triage import classify_rework_trigger
from zf.runtime.run_manager import build_run_manager_projection


@pytest.fixture
def config():
    return load_config(Path(__file__).parents[1] / "zf-prd-v5.yaml")


@pytest.mark.parametrize("kind,payload", [
    ("dev.failed", {"failure_class": "unknown", "reason": "pytest failed"}),
    ("dev.failed", {"reason": "expected exception contains connection refused"}),
    ("integration.failed", {"failure_class": "candidate_integration_conflict"}),
])
def test_v5_unknown_and_conflict_need_diagnosis_not_product_rework(config, kind, payload):
    result = classify_rework_trigger(ZfEvent(type=kind, task_id="T1", payload=payload), config)
    assert result.classification == "unknown"
    assert result.suspected_owner == "run_manager"
    assert result.recommended_action == "request_diagnosis"
    assert not result.should_increment_retry


def test_actual_rm_projection_has_one_owner_for_unknown_failure(tmp_path, config):
    state, log, _ = _state(tmp_path)
    TaskStore(state / "kanban.json").add(Task(id="T1", title="CSV", status="blocked"))
    event = ZfEvent(type="dev.failed", origin="kernel", task_id="T1", correlation_id="run-1",
                   payload={"failure_class": "unknown", "workflow_run_id": "run-1"})
    log.append(event)
    projection = build_run_manager_projection(state, events=log.read_all(), config=config, project_root=tmp_path)
    actions = [a for a in projection["pending_actions"] if a.get("task_id") == "T1"]
    assert len(actions) == 1
    assert actions[0]["action"] == "diagnose-attention"
    assert actions[0]["owner_route"] == "run_manager"
    assert actions[0]["policy_decision"]["decision"] == "needs_diagnosis"
    assert actions[0]["source_event_id"] == event.id


def test_structured_known_environment_failure_preserves_policy(config):
    result = classify_rework_trigger(ZfEvent(type="integration.failed", payload={
        "failure_class": "candidate_dependency_missing"}), config)
    assert result.classification == "environment_issue"
    assert not result.should_increment_retry


def test_current_unknown_failure_is_delivered_to_resident_with_fixed_evidence(tmp_path, config, monkeypatch):
    from zf.runtime.run_manager import run_manager_tick
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.recovery_case import build_recovery_case_projection
    state, log, writer = _state(tmp_path)
    TaskStore(state / "kanban.json").add(Task(id="T1", title="CSV", status="blocked"))
    failure = writer.append(ZfEvent(type="dev.failed", origin="kernel", task_id="T1", correlation_id="run-1",
        payload={"failure_class": "unknown", "workflow_run_id": "run-1", "reason": "database probe failed"}))
    briefing = state / "briefings" / "run-manager.md"
    briefing.parent.mkdir()
    briefing.write_text("Read current request and deliver evidence")
    resident = {"status": "observing", "instance_id": "run-manager", "tmux_session": "isolated-rm",
                "briefing_path": str(briefing), "session_mode": "dedicated"}
    monkeypatch.setattr("zf.runtime.run_manager.build_resident_agent_projection", lambda *a, **kw: resident)
    monkeypatch.setattr("zf.runtime.run_manager._resident_pane_display", lambda _: {
        "ok": True, "current_command": "codex", "current_path": str(tmp_path)})
    monkeypatch.setattr("zf.runtime.run_manager._refresh_resident_briefing", lambda **kw: False)
    sent = []
    monkeypatch.setattr("zf.runtime.run_manager._send_tmux_prompt", lambda target, prompt, **kw: sent.append(prompt) or {"ok": True})
    run_manager_tick(state_dir=state, writer=writer, config=config, event_log=log, project_root=tmp_path, spawn_repairs=False)
    events = log.read_all()
    assert len(sent) == 1 and "expected_output" in sent[0]
    pending = next(e for e in events if e.type == "run.manager.action.effect.pending")
    context = hydrate_sidecar_ref(state, pending.payload["recovery_context_ref"]).payload
    assert failure.id in str(context)
    assert pending.payload["continuation_expected_events"] == ["run.manager.agent.recommendation"]
    assert build_recovery_case_projection(events)["cases"][0]["status"] in {"diagnosing", "verifying"}
