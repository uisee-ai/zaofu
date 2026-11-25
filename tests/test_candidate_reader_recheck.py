"""Bound read-only diagnosis after an ineffective recommendation and a material change."""
import pytest

from tests.test_candidate_host_consumption import evidence
from tests.test_candidate_reader_method import worker
from tests.test_run_manager_reader_rebuild import consume
from zf.runtime import candidate_host_navigation as navigation
from zf.runtime.candidate_reader_method import PROOF
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


@pytest.mark.parametrize("old_route", ["wait", "unproven_rebuild", "owner_gate", "wait_again"])
def test_run_manager_reopens_old_answer_once_on_new_bound_method(worker, monkeypatch, old_route):
    from zf.runtime import run_manager as manager
    from zf.runtime.candidate_reader_recheck import pending_reader_method_rechecks
    from zf.runtime.recovery_case import bind_recovery_case, recovery_action_admission

    state, log, _, runtime, scope, failure, op, _ = worker
    writer, cfg = runtime.event_writer, runtime.config
    instance = "run-manager"
    briefing = state / "resident.md"
    briefing.write_text("resident")
    resident = {"instance_id": instance, "briefing_path": str(briefing), "status": "watching",
                "tmux_session": "test-rm", "session_mode": "dedicated"}
    writer.emit("run.manager.resident.spawned", payload={**resident, "ready": True})
    writer.emit("run.manager.resident.prompted", payload={**resident, "prompted": True})
    writer.emit("run.manager.agent.observation", actor=instance, payload={"status": "watching"})
    cfg.runtime.run_manager.resident_agent.enabled = True
    monkeypatch.setattr(manager, "_resident_pane_display", lambda *a: {"ok": True, "current_command": "codex"})
    monkeypatch.setattr(manager, "_send_tmux_prompt", lambda *a, **kw: {"ok": True})
    original = bind_recovery_case({"action": "diagnose-attention", "source_event_ids": [failure.id],
        "workflow_run_id": scope["workflow_run_id"], "task_id": op["task_id"],
        "target_operation_id": op["operation_id"], "active_attempt_id": op["active_attempt_id"],
        "safe_resume_action": "diagnose_attention", "suggested_route": "run_manager_recovery",
        "owner_route": "run_manager", "checkpoint_id": "reader-original", "request_id": "reader-original"})
    choose = navigation.host_navigation_entries
    with monkeypatch.context() as previous:
        previous.setattr(navigation, "host_navigation_entries", lambda *a, **kw: [
            {**row, "media_sources": []} for row in choose(*a, **kw)])
        delivery = manager._resident_diagnosis_reprompt_action(original, resident)
        assert manager._execute_resident_agent_reprompt(state_dir=state, writer=writer, config=cfg,
            project_root=runtime.project_root, action=delivery, causation_id=failure.id) == "applied"
        prior = {"recommended_route": "wait"}
        if old_route == "unproven_rebuild":
            prior = {"recommended_route": "controlled_action", "action": "child-rebuild", "action_payload": scope}
        writer.emit("run.manager.agent.recommendation", actor=instance,
                    payload={**prior, "request_id": "reader-original"})
        consume(runtime)
        manager._settle_pending_action_effects(log.read_all(), writer)
        assert not pending_reader_method_rechecks(state, log.read_all(), scope["workflow_run_id"])
    if old_route == "owner_gate":
        writer.emit("human.escalate", payload={"workflow_run_id": scope["workflow_run_id"],
            "blocking_scope":"run", "checkpoint_id":"owner-review", "reason":"await owner"})
        assert not pending_reader_method_rechecks(state, log.read_all(), scope["workflow_run_id"])
        return
    due, = pending_reader_method_rechecks(state, log.read_all(), scope["workflow_run_id"])
    assert recovery_action_admission(log.read_all(), due)["status"] == "admitted"
    other = writer.emit("run.manager.agent.recommendation", actor=instance,
                        payload={"recommended_route": "wait", "request_id": "unrelated"})
    consume(runtime)
    assert recovery_action_admission(log.read_all(), {**due, "recorded_event_id": other.id})["status"] == "blocked"
    projection = manager.build_run_manager_projection(state, events=log.read_all(), config=cfg,
                                                      project_root=runtime.project_root)
    assert any(a.get("checkpoint_id") == due["checkpoint_id"] for a in projection["pending_actions"])
    sent = manager._resident_diagnosis_reprompt_action(due, resident)
    assert sent["request_id"] != original["request_id"]
    for _ in range(2):
        manager.run_manager_tick(state_dir=state, writer=writer, config=cfg, project_root=runtime.project_root,
                                 event_log=log, spawn_repairs=False)
    requests = [e for e in log.read_all() if e.type == "run.manager.action.effect.pending"
                and e.payload.get("request_id") == sent["request_id"]]
    assert len(requests) == 1
    request = requests[0]
    card = hydrate_sidecar_ref(state, request.payload["recovery_context_ref"]).payload["reader_rebuild"]
    assert card["recommendation"]["action_payload"][PROOF] in due["checkpoint_id"]
    assert not pending_reader_method_rechecks(state, log.read_all(), scope["workflow_run_id"])
    if old_route == "wait_again":
        writer.emit("run.manager.agent.recommendation", actor=instance,
                    payload={"request_id": sent["request_id"], "recommended_route": "wait"})
        consume(runtime)
        assert not pending_reader_method_rechecks(state, log.read_all(), scope["workflow_run_id"])
        assert not any(e.type == "repair.action.requested" for e in log.read_all())
        return
    writer.emit("run.manager.agent.recommendation", actor=instance,
                payload={**card["recommendation"], "request_id": sent["request_id"]})
    consume(runtime)
    assert len([e for e in log.read_all() if e.type == "repair.action.requested"]) == 1
