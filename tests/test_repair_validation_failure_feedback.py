"""A failed repair validation must return to diagnosis rather than idle."""
import pytest

from tests.test_run_manager import _config, _state
from zf.runtime.run_manager import build_run_manager_projection, run_manager_tick


def _failed_candidate(writer):
    identity = {
        "fingerprint": "repair:input-contract", "candidate_id": "repair:input-contract",
        "source_commit": "candidate-sha", "base_commit": "base-sha",
        "worktree": "/isolated/repair", "repair_contract_digest": "contract-digest",
        "continuation": {"workflow_run_id": "run-original", "task_id": "task-original"},
    }
    writer.emit("autoresearch.repair.closeout.required", payload={
        **identity, "verification_plan": [{"command": "git diff --check", "required": "true"}],
    })
    failed = writer.emit("run.manager.action.failed", payload={
        **identity, "action": "repair-closeout-validate", "checkpoint_id": "validation-original",
        "reason": "command_not_allowlisted", "validation_result": {"status": "failed"},
    })
    return identity, failed


def test_failed_validation_is_dispatched_once_without_retry_or_apply(tmp_path):
    state, log, writer = _state(tmp_path)
    writer.default_origin = "kernel"
    identity, failed = _failed_candidate(writer)
    def diagnoses():
        return [a for a in build_run_manager_projection(state, events=log.read_all(), config=_config())["pending_actions"]
                if a.get("failure_class") == "repair_validation_failed"]
    actions = diagnoses()
    assert len(actions) == 1
    action = actions[0]
    assert action["action"] == "diagnose-attention"
    assert action["source_event_ids"] == [failed.id]
    assert action["workflow_run_id"] == "run-original"
    assert action["source_commit"] == identity["source_commit"]
    assert action["repair_contract_digest"] == identity["repair_contract_digest"]
    assert action["policy_decision"]["executable"]
    from zf.runtime.recovery_case import recovery_action_admission
    from zf.runtime.run_manager import _resident_diagnosis_reprompt_action

    dispatch = _resident_diagnosis_reprompt_action(action, {
        "status": "observing", "tmux_session": "resident", "briefing_path": "/tmp/brief",
        "instance_id": "run-manager",
    })
    assert dispatch is not None
    assert dispatch["request_kind"] == "recovery_diagnosis"
    assert dispatch["source_event_ids"] == [failed.id]
    assert recovery_action_admission(log.read_all(), dispatch)["status"] == "admitted"
    run_manager_tick(state_dir=state, writer=writer, config=_config(), spawn_repairs=False,
                     action_filter={"diagnose-attention"})
    events = log.read_all()
    assert any(e.type == "run.manager.autoresearch.requested" for e in events)
    assert not any(e.type in {"run.manager.repair.merge.merged", "runtime.restart.requested"} for e in events)
    assert diagnoses() == []
    assert any(e.id == failed.id for e in events)


@pytest.mark.parametrize("terminal", ["merged", "discarded"])
def test_terminal_candidate_does_not_reopen_validation_diagnosis(tmp_path, terminal):
    state, log, writer = _state(tmp_path)
    identity, _ = _failed_candidate(writer)
    writer.emit("run.manager.repair.merge." + terminal, payload=identity)
    pending = build_run_manager_projection(state, events=log.read_all(), config=_config())["pending_actions"]
    assert not any(a.get("failure_class") == "repair_validation_failed" for a in pending)
