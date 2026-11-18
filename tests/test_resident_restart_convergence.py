"""A second controller's authorized reset reaches the existing resident."""
import pytest
from zf.core.events.model import ZfEvent
from zf.core.config.schema import RoleConfig
from tests.test_worker_state_events import state_dir, config, transport
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.respawn_lease import reset_respawn_success_circuit


@pytest.mark.parametrize("layer2", [False, True])
def test_resident_observes_external_restart_without_projection_rebuild(state_dir, config, transport, layer2):
    if layer2:
        config.roles.append(RoleConfig(name="orchestrator", backend="mock", triggers=["unrelated"]))
    resident = Orchestrator(state_dir, config, transport)
    resident._set_worker_state("dev", "blocked_human", reason="respawn circuit", force=True)
    resident._respawn_success_circuit_opened.add("dev")
    resident._dead_counter["dev"] = 7
    operator = Orchestrator(state_dir, config, transport, control_plane_only=True)
    assert operator._last_worker_state["dev"] == "blocked_human"
    offset = len(resident.event_log.read_all())
    reset_respawn_success_circuit(operator, config.roles[0])
    events = resident.event_log.read_all()[offset:]
    assert any(e.type == "worker.state.changed" and e.payload.get("operator_authorized") for e in events)
    resident.run_once(events=events)
    assert resident._last_worker_state["dev"] == "idle"
    assert "dev" not in resident._respawn_success_circuit_opened
    assert "dev" not in resident._dead_counter
    assert resident._worker_dispatchable("dev")
    recovered = Orchestrator(state_dir, config, transport, control_plane_only=True)
    assert recovered.worker_health()["dev"] == "idle"


@pytest.mark.parametrize("origin", ["worker", "external", ""])
def test_untrusted_state_event_cannot_clear_resident_circuit(state_dir, config, transport, origin):
    resident = Orchestrator(state_dir, config, transport)
    resident._set_worker_state("dev", "blocked_human", reason="respawn circuit", force=True)
    resident._respawn_success_circuit_opened.add("dev")
    event = ZfEvent(type="worker.state.changed", actor="dev", origin=origin,
        payload={"instance_id": "dev", "to": "idle", "operator_authorized": True,
                 "respawn_success_circuit_reset": True})
    resident.event_log.append(event)
    resident.run_once(events=[event])
    assert resident._last_worker_state["dev"] == "blocked_human"
    assert "dev" in resident._respawn_success_circuit_opened


def test_external_old_task_release_cannot_retire_active_writer(state_dir, config, transport):
    resident = Orchestrator(state_dir, config, transport)
    resident._set_worker_state("dev", "busy", task_id="NEW", reason="current owner")
    event = resident.event_writer.append(ZfEvent(type="worker.state.changed", actor="dev", task_id="OLD",
        payload={"instance_id": "dev", "to": "idle"}))
    resident.run_once(events=[event])
    assert resident._last_worker_state["dev"] == "busy"
    assert resident._last_worker_task_id["dev"] == "NEW"


def test_operator_event_replays_by_target_instance_not_actor(state_dir, config, transport):
    runtime = Orchestrator(state_dir, config, transport)
    runtime._set_worker_state("dev", "blocked_human", reason="prior failure", force=True)
    event = runtime.event_writer.append(ZfEvent(type="worker.state.changed", actor="operator",
        payload={"instance_id": "dev", "from": "blocked_human", "to": "idle"}))
    runtime.run_once(events=[event])
    rebuilt = Orchestrator(state_dir, config, transport, control_plane_only=True)
    assert rebuilt._last_worker_state["dev"] == "idle"
    assert rebuilt.worker_health()["dev"] == "idle"
    assert "operator" not in rebuilt._last_worker_state
