"""A replacement must never overlap an unconfirmed previous provider process."""
from types import SimpleNamespace

import pytest

from zf.runtime.orchestrator_types import OrchestratorDecision
from tests.test_worker_action_requests import _state, _request


@pytest.mark.parametrize("failure", ["terminate_raises", "failed_turn_still_running", "still_alive", "liveness_unknown"])
def test_respawn_refuses_replacement_when_old_worker_is_not_quiescent(tmp_path, failure):
    _, log, runtime = _state(tmp_path)
    spawned = []

    def terminate(_role):
        if failure in {"terminate_raises", "failed_turn_still_running"}:
            raise RuntimeError("termination unavailable")

    def alive(_role):
        if failure == "failed_turn_still_running":
            return False  # Stream transport last-turn health is not process absence.
        if failure == "liveness_unknown":
            raise RuntimeError("liveness unavailable")
        return True

    runtime.transport.terminate = terminate
    runtime.transport.is_alive = alive
    runtime._get_spawn_coordinator = lambda: SimpleNamespace(spawn=lambda *a, **kw: spawned.append(a))
    decision = runtime._respawn_instance(runtime.config.roles[0])
    assert decision.action == "respawn_failed"
    assert spawned == []
    assert any(e.type == "worker.respawn.failed" for e in log.read_all())


@pytest.mark.parametrize("action", ["respawn_cooldown", "respawn_circuit_open", "respawn_in_progress"])
def test_deferred_respawn_never_delivers_continuation_or_claims_completion(tmp_path, action):
    state, log, runtime = _state(tmp_path)
    briefing = state / "continuation.md"
    briefing.write_text("continue")
    request = _request()
    request.task_id = "TASK-1"
    request.payload["continuation_briefing_ref"] = str(briefing)
    runtime._respawn_instance = lambda role: OrchestratorDecision(action=action, role=role.instance_id)
    runtime._apply_worker_respawn_request(request, instance_id="dev")
    assert runtime.transport.sent == []
    assert not any(e.type == "worker.respawn.completed" for e in log.read_all())
    assert not any(e.payload.get("continuation_delivered") for e in log.read_all())


def test_execution_route_handoff_requires_a_fresh_provider_session(tmp_path):
    _, _, runtime = _state(tmp_path)
    request = _request()
    request.payload.update(schema_version="execution-route-respawn.v1", route_id="fallback")
    calls = []

    def respawn(role, **kwargs):
        calls.append(kwargs)
        return OrchestratorDecision(action="respawn", role=role.instance_id)

    runtime._respawn_instance = respawn
    runtime._apply_worker_respawn_request(request, instance_id="dev")
    assert calls == [{"recovery_reason": "execution_route_switch", "force_fresh_session": True}]
