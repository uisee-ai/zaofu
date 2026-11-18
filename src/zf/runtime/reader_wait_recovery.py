"""Bound Reader resource waits without restarting holders or changing targets."""
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from zf.core.config.schema import RoleConfig

from zf.core.events.model import ZfEvent


def reconcile_undelivered_wait_failures(runtime: Any, events: list[ZfEvent]) -> bool:
    """Close only a failed wait's exact request when no provider ever received it."""
    from zf.runtime.call_result_runtime import workflow_operation_service
    from zf.runtime.sidecar_refs import hydrate_sidecar_ref
    from zf.runtime.workflow_operation import reduce_workflow_operations

    failures = [e for e in events if e.type == 'fanout.child.failed' and e.origin == 'kernel'
                and e.payload.get('failure_class') == 'reader_environment_wait_exhausted']
    if not failures:
        return False
    positions = {e.id: i for i, e in enumerate(events)}
    for op in reduce_workflow_operations(events).values():
        if (op.get('status') != 'requested' or op.get('operation_type') != 'fanout_reader_child'
                or op.get('started_event_id') or op.get('call_result_admitted_event_id')):
            continue
        for failed in reversed(failures):
            p = failed.payload
            bindings = {'workflow_run_id': p.get('workflow_run_id'),
                        'parent_stage_id': p.get('stage_id'), 'role_instance': p.get('role_instance'),
                        'active_attempt_id': p.get('run_id')}
            if (any(not value or op.get(key) != value for key, value in bindings.items())
                    or positions.get(op.get('last_event_id'), len(events)) > positions[failed.id]):
                continue
            if any(e.type in {'workflow.operation.started', 'workflow.operation.retry_started',
                              'fanout.child.dispatched'}
                   and (e.payload.get('operation_id') == op['operation_id']
                        or (e.payload.get('role_instance') == op['role_instance']
                            and e.payload.get('run_id') == op['active_attempt_id'])) for e in events):
                continue
            try:
                body = hydrate_sidecar_ref(runtime.state_dir, op['request_ref']).payload
            except (ValueError, KeyError, OSError):
                continue
            request = body.get('request', {})
            if (any(body.get(key) != op.get(key) for key in
                    ('workflow_run_id', 'operation_id', 'active_attempt_id', 'role_instance'))
                    or any(not p.get(key) or request.get(key) != p[key]
                           for key in ('fanout_id', 'child_id', 'stage_id'))):
                continue
            workflow_operation_service(runtime).fail(
                operation_id=op['operation_id'], request_hash=op['request_hash'],
                workflow_run_id=op['workflow_run_id'], task_id=op.get('task_id', ''),
                reason=p.get('reason') or 'reader environment wait exhausted before delivery',
                causation_id=failed.id, correlation_id=op['workflow_run_id'],
                details={'failure_class': p['failure_class'], 'fanout_id': p['fanout_id'],
                         'child_id': p['child_id']},
            )
            return True
    return False


def exhaust_reader_wait(
    runtime: Any, *, role: RoleConfig, context: Any, child: Any,
    run_id: str, target_ref: str, causation_id: str,
) -> bool:
    events = runtime.event_log.read_all()
    waits = [event for event in events
             if event.type == "fanout.child.dispatch_deferred"
             and event.payload.get("fanout_id") == context.fanout_id
             and event.payload.get("child_id") == child.child_id
             and event.payload.get("run_id") == run_id
             and event.payload.get("reason") == "reader_environment_busy"]
    if not waits:
        return False
    # Use the configured liveness window; restarts cannot reset the first wait.
    # Checking resource availability still precedes this deadline, so a released
    # resource can progress instead of failing solely because time has elapsed.
    timeout = max(60, role.stuck_threshold_seconds)
    from zf.runtime.pause_lifecycle import active_request_age_seconds
    # An explicit operator stop is not unexpected downtime. Keep consumed time,
    # but exclude the stopped interval when this same workflow is resumed.
    clock_events = [replace(event, type="loop.pause_requested")
                    if event.type in {"runtime.stop.requested", "loop.shutdown_requested", "loop.stopped"}
                    else replace(event, type="loop.resume_requested") if event.type == "loop.started"
                    else event for event in events]
    age = active_request_age_seconds(clock_events, created_at=datetime.fromisoformat(waits[0].ts),
                                     now=datetime.fromtimestamp(runtime._now(), timezone.utc))
    if age < timeout:
        return False
    runtime.event_writer.append(ZfEvent(
        type="fanout.child.failed", actor="zf-cli", correlation_id=context.trace_id,
        causation_id=causation_id,
        payload={"fanout_id": context.fanout_id, "trace_id": context.trace_id,
                 "workflow_run_id": context.trace_id, "stage_id": context.stage_id,
                 "child_id": child.child_id, "role_instance": role.instance_id,
                 "run_id": run_id, "target_ref": target_ref,
                 "reason": "reader environment wait exceeded its recovery deadline",
                 "failure_class": "reader_environment_wait_exhausted", "failure_kind": "infra",
                 "first_wait_event_id": waits[0].id, "wait_seconds": age,
                 "timeout_seconds": timeout},
    ))
    return True


def pending_reader_wait_diagnoses(events: list[ZfEvent]) -> list[dict[str, Any]]:
    """Workflow failures remain owned even when every product Task is done."""
    cancelled = {e.payload.get("fanout_id") for e in events if e.type == "fanout.cancelled"}
    actions = []
    for event in events:
        p = event.payload or {}
        if (event.type != "fanout.child.failed"
                or p.get("failure_class") != "reader_environment_wait_exhausted"
                or p.get("fanout_id") in cancelled):
            continue
        actions.append({
            "schema_version": "run-manager.pending-action.v1",
            "action": "diagnose-attention", "safe_resume_action": "diagnose_attention",
            "checkpoint_id": f"reader-wait:{event.id}", "request_id": f"reader-wait:{event.id}",
            "workflow_run_id": p.get("workflow_run_id") or event.correlation_id or "",
            **{key: p[key] for key in ("fanout_id", "child_id", "stage_id", "target_ref") if key in p},
            "source_event_id": event.id, "source_event_type": event.type,
            "source_event_ids": [event.id], "failure_event_ids": [event.id],
            "fingerprint": event.id, "failure_class": p["failure_class"],
            "owner_route": "run_manager", "action_policy": "needs_diagnosis",
            "intervention_class": "diagnose", "suggested_route": "run_manager_resident_agent",
            "summary": "Reader资源等待已超时；核实持有者与目标，提出有界恢复或源码修复，不绕过占用保护。",
            "expected_output": ["run.manager.agent.recommendation"],
            "continuation_expected_events": ["run.manager.agent.recommendation"],
            "effect_expected_events": ["run.manager.agent.recommendation"],
            "expected_downstream_events": ["run.manager.resident.prompted"],
        })
    return actions
