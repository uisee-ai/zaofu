"""A host hold permits bound read-only diagnosis and already repaired inputs."""
from zf.runtime.quiescent import host_probe_budget_hold


def reconcile_quiescent_reader_handoff(*, state_dir, config, writer, project_root,
                                       events, escalation_id) -> bool:
    alert = next((event for event in events if event.id == escalation_id), None)
    from zf.runtime.task_orphan_recovery import orphaned_review_needs_tick

    if orphaned_review_needs_tick(state_dir, config, project_root, events, alert):
        return True
    if alert is not None and _failed_diagnosis_ready(state_dir, config, project_root, events, alert):
        return True
    if alert is not None and _host_diagnosis_ready(state_dir, config, project_root, events, alert):
        return True  # The normal RM tick retains its gate and single-flight checks.
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


def _host_diagnosis_ready(state_dir, config, project_root, events, alert) -> bool:
    from zf.runtime.host_probe_diagnosis import pending_host_probe_diagnoses
    from zf.runtime.run_continuation import _host_effect_allows_diagnosis
    from zf.runtime.run_manager import build_run_manager_projection

    actions = [a for a in pending_host_probe_diagnoses(state_dir, events, config)
               if _host_effect_allows_diagnosis({'event_id': alert.id}, a, events)]
    if not actions:
        return False
    projection = build_run_manager_projection(state_dir, events=events, config=config, project_root=project_root)
    selected = (projection.get('continuation') or {}).get('next_operation') or {}
    candidate = next((a for a in projection.get('pending_actions', [])
                      if a.get('operation_key') == selected.get('operation_key')), {})
    if _host_effect_allows_diagnosis({'event_id': alert.id}, candidate, events):
        return True
    # A returned diagnosis must reach its existing consumer even while the
    # original effect gate remains closed. No recommendation grants permission.
    checkpoints = {a['checkpoint_id'] for a in actions}
    requests = {e.payload.get('request_id'): e.payload.get('instance_id') for e in events if e.origin == 'kernel'
        and e.type == 'run.manager.resident.prompted'
        and e.payload.get('source_action_checkpoint_id') in checkpoints}
    consumed = {e.payload.get('source_event_id') for e in events
                if e.type == 'run.manager.agent.recommendation.consumed'}
    return any(e.type == 'run.manager.agent.recommendation' and e.payload.get('request_id') in requests
               and e.actor == requests[e.payload['request_id']] and e.id not in consumed for e in events)

def _failed_diagnosis_ready(state_dir, config, project_root, events, alert):
    from zf.runtime.autoresearch_failure_handoff import failed_diagnosis_belongs_to_gate, pending_autoresearch_followups
    from zf.runtime.run_manager import build_run_manager_projection, _action_seen

    if not any(e.type == "autoresearch.loop.failed" for e in reversed(events)):
        return False
    gate = {"event_id": alert.id}
    checkpoints = {a["checkpoint_id"] for a in pending_autoresearch_followups(events)
                   if failed_diagnosis_belongs_to_gate(gate, a, events) and not _action_seen(events, a)}
    if not checkpoints:
        return False
    projection = build_run_manager_projection(state_dir, config=config, project_root=project_root, events=events)
    selected = (projection["continuation"].get("next_operation") or {})
    return any(a.get("operation_key") == selected.get("operation_key") and a.get("checkpoint_id") in checkpoints
               for a in projection["pending_actions"])
