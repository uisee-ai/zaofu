"""Route current unknown failures to one existing diagnostic owner."""

from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.rework_progress import has_later_task_progress
from zf.runtime.rework_triage import classify_rework_trigger
from zf.runtime.recovery_step import IDENTITY_FIELDS


def pending_failure_diagnoses(
    state_dir: Path, events: list[ZfEvent], config: Any,
) -> list[dict[str, Any]]:
    wrc = config.workflow.orchestration.normal_frontier_owner == "wrc"
    store = TaskStore(state_dir / "kanban.json")
    latest: dict[str, tuple[int, ZfEvent]] = {}
    for index, event in enumerate(events):
        if event.type not in {"dev.failed", "dev.blocked", "integration.failed", "candidate.conflict"}:
            continue
        payload = event.payload or {}
        task_id = str(event.task_id or payload.get("task_id") or payload.get("pdd_id") or "")
        task = store.get(task_id) if task_id else None
        if task is None or task.status in {"done", "cancelled"}:
            continue
        latest.pop(task_id, None)
        if payload.get("failure_class") == "host_publication_required":
            policy = getattr(config.runtime, "task_publication", None)
            if policy and policy.enabled:
                continue  # The deterministic publisher owns admission, failure and escalation.
        if not wrc and payload.get("failure_class") != "reader_execution_failure":
            continue
        triage = classify_rework_trigger(event, config, evidence_events=events)
        if triage.recommended_action == "request_diagnosis":
            latest[task_id] = index, event
    from zf.runtime.reader_wait_recovery import pending_reader_wait_diagnoses
    from zf.runtime.host_probe_diagnosis import pending_host_probe_diagnoses
    from zf.runtime.candidate_infrastructure_recovery import pending_candidate_infrastructure_diagnoses
    from zf.runtime.durable_mailbox_question_liveness import pending_peer_diagnoses
    actions = [*pending_peer_diagnoses(events), *pending_candidate_infrastructure_diagnoses(events),
               *pending_reader_wait_diagnoses(events),
               *pending_host_probe_diagnoses(state_dir, events, config)]
    for task_id, (index, event) in latest.items():
        if has_later_task_progress(events, index, task_id):
            continue
        payload = event.payload or {}
        actions.append({
            "schema_version": "run-manager.pending-action.v1",
            "action": "diagnose-attention", "safe_resume_action": "diagnose_attention",
            "checkpoint_id": f"failure-diagnosis:{event.id}",
            "request_id": f"failure-diagnosis:{event.id}",
            **{key: payload[key] for key in IDENTITY_FIELDS if payload.get(key)},
            "task_id": task_id,
            "target_operation_id": str(payload.get("operation_id") or ""),
            "workflow_run_id": payload.get("workflow_run_id") or event.correlation_id or "",
            "source_event_id": event.id, "source_event_type": event.type,
            "source_event_ids": [event.id], "failure_event_ids": [event.id], "fingerprint": event.id,
            "failure_class": "unknown", "owner_route": "run_manager",
            "action_policy": "needs_diagnosis", "intervention_class": "diagnose",
            "suggested_route": "run_manager_resident_agent",
            "expected_output": ["run.manager.agent.recommendation"],
            "continuation_expected_events": ["run.manager.agent.recommendation"],
            "summary": "诊断当前失败事实、已尝试方法和适用合同；先收集证据，再决定修复或向 OA 移交。",
            "expected_downstream_events": ["run.manager.resident.prompted", "run.manager.autoresearch.requested"],
            "effect_expected_events": ["run.manager.agent.recommendation"],
        })
    return actions
