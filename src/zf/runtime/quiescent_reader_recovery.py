"""A probe budget hold does not prohibit handing over already repaired inputs."""
from zf.runtime.quiescent import host_probe_budget_hold


def reconcile_quiescent_reader_handoff(*, state_dir, config, writer, project_root,
                                       events, escalation_id) -> bool:
    alert = next((event for event in events if event.id == escalation_id), None)
    if alert is None or not host_probe_budget_hold(config, events, alert):
        return False
    from zf.runtime.run_manager import _pending_human_decisions

    if any(gate.get('event_id') != alert.id and gate.get('blocking_scope') != 'side'
           for gate in _pending_human_decisions(events)):
        return False
    from zf.runtime.control_actions import ControlledActionService
    from zf.runtime.host_verification import reconcile_host_verification

    service = ControlledActionService(state_dir, writer, config=config,
        project_root=project_root, actor='run-manager', surface='runtime')
    return bool(reconcile_host_verification(service, allow_execution=False,
                                           workflow_run_id=alert.correlation_id))
