"""Supervisor Inspection projections and optional attention events.

The module folds existing runtime truth and projections into a compact
supervisor snapshot. Snapshot/projection builders never mutate task/session
truth. The runtime watcher may opt in to append high-priority attention
events derived from the projection.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from zf.autoresearch.failure_signals import (
    collect_failure_signals,
    completed_run_quiesced,
)
from zf.autoresearch.triggers import read_trigger_decisions
from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.automation_projection import project_automations
from zf.runtime.pause_lifecycle import project_pause_lifecycle
from zf.runtime.project_spine_review_common import project_id as stable_project_id
from zf.runtime.project_spine_review_artifacts import project_spine_review_insight
from zf.runtime.problem_taxonomy import problem_envelope_from_attention
from zf.runtime.pane_probe import (
    build_runtime_pane_probe,
    pane_probe_attention_items,
)
from zf.runtime.plan_insights import (
    build_plan_insight_projection,
    plan_insight_attention_items,
)
from zf.runtime.supervisor_attention import (
    ATTENTION_SCHEMA_VERSION,
    apply_attention_lifecycle,
    attention_summary,
    build_attention_items,
    failure_signal_row,
    is_actionable_attention,
)
from zf.runtime.supervisor_control_loop import (
    build_supervisor_control_loop_events,
    control_loop_projection,
)
from zf.runtime.supervisor_plan_integrity import (
    PLAN_INTEGRITY_SCHEMA_VERSION,
    build_plan_integrity_projection,
    task_plan_refs,
)
from zf.runtime.sidecar_refs import write_sidecar_json
from zf.runtime.workflow_resume import build_workflow_resume_projection


SNAPSHOT_SCHEMA_VERSION = "supervisor.snapshot.v0"
_STALE_ACTIVE_RUN_NO_PROGRESS_SECONDS = 900
_STALE_ACTIVE_RUN_FAILURE_EVENTS = frozenset({
    "worker.drift.detected",
    "worker.context.compact.failed",
    "task.ref.rejected",
    "autoresearch.repair.dispatch_requested",
    "dispatch.silent_stall",
    "worker.stuck",
    "worker.stuck.recovery_failed",
    "orchestrator.tick.failed",
})
_STALE_ACTIVE_RUN_PROGRESS_EVENTS = frozenset({
    "task.dispatched",
    "task.ref.updated",
    "dev.build.done",
    "review.approved",
    "test.passed",
    "verify.passed",
    "judge.passed",
    "candidate.ready",
    "fanout.child.completed",
    "fanout.aggregate.completed",
})
_STALE_ACTIVE_RUN_LIVENESS_EVENTS = frozenset({
    "worker.heartbeat",
    "worker.state.changed",
    "agent.usage",
})
_ATTENTION_RESOLUTION_EVENTS = frozenset({
    "runtime.attention.acknowledged",
    "runtime.attention.snoozed",
    "runtime.attention.resolved",
    "runtime.attention.escalated",
})


def build_supervisor_snapshot(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    project_id: str = "",
    events: list[ZfEvent] | None = None,
) -> dict[str, Any]:
    state_dir = Path(state_dir)
    root = Path(project_root) if project_root is not None else state_dir.parent
    pid = project_id or stable_project_id(config=config, project_root=root)
    now = datetime.now(timezone.utc)
    all_events = events if events is not None else _read_events(state_dir, config=config)
    tasks = _read_tasks(state_dir)
    recent_events = all_events[-500:]
    pause = project_pause_lifecycle(state_dir, events=all_events, now=now)
    automation = _safe_automations(state_dir, pid, config=config)
    failure_signals = _safe_failure_signals(state_dir)
    trigger_history = _trigger_history(state_dir)
    plan_integrity = build_plan_integrity_projection(
        state_dir,
        project_root=root,
        tasks=tasks,
        events=all_events,
        now=now,
    )
    spine_review_hint = _spine_hint(state_dir, pid)
    plan_insights = build_plan_insight_projection(
        events=all_events,
        plan_integrity=plan_integrity,
        spine_review_hint=spine_review_hint,
        now=now,
    )
    pane_probe = build_runtime_pane_probe(
        state_dir,
        config=config,
        project_root=root,
        now=now,
    )
    workflow_resume = _safe_workflow_resume(
        state_dir,
        config=config,
        events=all_events,
        tasks=tasks,
    )
    base_attention_items = build_attention_items(
        events=recent_events,
        automation=automation,
        failure_signals=failure_signals,
        plan_integrity=plan_integrity,
    )
    base_attention_items.extend(plan_insight_attention_items(plan_insights))
    base_attention_items.extend(pane_probe_attention_items(pane_probe))
    base_attention_items.extend(_workflow_resume_attention_items(
        workflow_resume,
        state_dir=state_dir,
    ))
    base_attention_items.extend(_stale_active_run_attention_items(
        state_dir=state_dir,
        tasks=tasks,
        events=all_events,
        now=now,
    ))
    run_quiesced = completed_run_quiesced(all_events)
    if run_quiesced:
        base_attention_items = [
            _completed_run_quiesced_attention_item(item)
            for item in base_attention_items
        ]
    attention_items = apply_attention_lifecycle(base_attention_items, all_events, now=now)
    if run_quiesced:
        attention_items = [
            _completed_run_quiesced_attention_item(item)
            for item in attention_items
        ]
    attention_items = [
        _attention_with_problem_envelope(item)
        for item in attention_items
    ]
    control_loop = control_loop_projection(events=all_events, state_dir=state_dir)
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "is_derived_projection": True,
        "generated_at": now.isoformat(),
        "state_dir": str(state_dir),
        "project_id": pid,
        "task_summary": _task_summary(tasks),
        "worker_summary": _worker_summary(state_dir, recent_events, now=now),
        "freshness": _freshness(recent_events, now=now),
        "pause_lifecycle": {
            "status": pause.get("status"),
            "paused": bool(pause.get("paused")),
            "dispatch_allowed": bool(pause.get("dispatch_allowed")),
            "summary": pause.get("summary") or {},
            "resume_sweep": pause.get("resume_sweep") or {},
        },
        "failure_signals": [
            failure_signal_row(signal) for signal in failure_signals[:20]
        ],
        "autoresearch_triggers": trigger_history[-20:],
        "attention_items": attention_items[:80],
        "attention_summary": attention_summary(attention_items),
        "plan_integrity": plan_integrity,
        "plan_insights": plan_insights,
        "controlled_action_capabilities": control_loop["controlled_action_capabilities"],
        "supervisor_decisions": control_loop["supervisor_decisions"],
        "owner_message_delivery": control_loop["owner_message_delivery"],
        "autoresearch_invocations": control_loop["autoresearch_invocations"],
        "context_recovery": control_loop["context_recovery"],
        "skill_provenance": control_loop["skill_provenance"],
        "pane_probe": pane_probe,
        "workflow_resume": workflow_resume,
        "spine_review_hint": spine_review_hint,
        "source_projections": {
            "automation_schema": automation.get("schema_version", ""),
            "pause_lifecycle_schema": pause.get("schema_version", ""),
            "control_loop_schema": control_loop.get("schema_version", ""),
            "pane_probe_schema": pane_probe.get("schema_version", ""),
            "workflow_resume_schema": workflow_resume.get("schema_version", ""),
        },
    }
    return redact_obj(snapshot)


def write_supervisor_projection(
    state_dir: Path,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    root = Path(state_dir) / "projections" / "supervisor"
    snapshot_path = root / "snapshot.json"
    attention_path = root / "attention-candidates.json"
    plan_path = root / "plan-integrity.json"
    plan_insights_path = root / "plan-insights.json"
    control_loop_path = root / "control-loop.json"
    pane_probe_path = root / "pane-probe.json"
    workflow_resume_path = root / "workflow-resume.json"
    hash_path = root / "snapshot.sha256"
    digest = _stable_digest(snapshot)
    prior = hash_path.read_text(encoding="utf-8").strip() if hash_path.exists() else ""
    changed = digest != prior
    if changed:
        atomic_write_text(
            snapshot_path,
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        atomic_write_text(
            attention_path,
            json.dumps({
                "schema_version": ATTENTION_SCHEMA_VERSION,
                "generated_at": snapshot.get("generated_at", ""),
                "project_id": snapshot.get("project_id", ""),
                "items": snapshot.get("attention_items") or [],
                "summary": snapshot.get("attention_summary") or {},
            }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        atomic_write_text(
            plan_path,
            json.dumps(snapshot.get("plan_integrity") or {}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        atomic_write_text(
            plan_insights_path,
            json.dumps(snapshot.get("plan_insights") or {}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        atomic_write_text(
            control_loop_path,
            json.dumps({
                "schema_version": "supervisor.control_loop.v0",
                "generated_at": snapshot.get("generated_at", ""),
                "project_id": snapshot.get("project_id", ""),
                "controlled_action_capabilities": snapshot.get("controlled_action_capabilities") or {},
                "supervisor_decisions": snapshot.get("supervisor_decisions") or {},
                "owner_message_delivery": snapshot.get("owner_message_delivery") or {},
                "autoresearch_invocations": snapshot.get("autoresearch_invocations") or {},
                "context_recovery": snapshot.get("context_recovery") or {},
                "skill_provenance": snapshot.get("skill_provenance") or {},
            }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        atomic_write_text(
            pane_probe_path,
            json.dumps(snapshot.get("pane_probe") or {}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        atomic_write_text(
            workflow_resume_path,
            json.dumps(snapshot.get("workflow_resume") or {}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        atomic_write_text(hash_path, digest + "\n")
    return {
        "changed": changed,
        "snapshot_path": str(snapshot_path),
        "attention_path": str(attention_path),
        "plan_integrity_path": str(plan_path),
        "plan_insights_path": str(plan_insights_path),
        "control_loop_path": str(control_loop_path),
        "pane_probe_path": str(pane_probe_path),
        "workflow_resume_path": str(workflow_resume_path),
        "hash": digest,
    }


def run_supervisor_inspection(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
    project_id: str = "",
    emit_attention_events: bool = False,
) -> dict[str, Any]:
    snapshot = build_supervisor_snapshot(
        state_dir,
        config=config,
        project_root=project_root,
        project_id=project_id,
    )
    result = write_supervisor_projection(state_dir, snapshot)
    attention_events_emitted = 0
    control_loop_events_emitted = 0
    if emit_attention_events:
        attention_events_emitted = _emit_attention_events(
            state_dir,
            snapshot,
            projection=result,
            config=config,
        )
        control_loop_events_emitted = _emit_control_loop_events(
            state_dir,
            snapshot,
            projection=result,
            config=config,
        )
    return {
        **result,
        "snapshot": snapshot,
        "attention_events_emitted": attention_events_emitted,
        "control_loop_events_emitted": control_loop_events_emitted,
    }


def read_supervisor_snapshot(state_dir: Path) -> dict[str, Any]:
    path = Path(state_dir) / "projections" / "supervisor" / "snapshot.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def supervisor_snapshot_ref(state_dir: Path) -> dict[str, str]:
    path = Path(state_dir) / "projections" / "supervisor" / "snapshot.json"
    if not path.exists():
        return {}
    return {
        "path": str(path.relative_to(state_dir)) if _is_relative(path, state_dir) else str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _emit_attention_events(
    state_dir: Path,
    snapshot: dict[str, Any],
    *,
    projection: dict[str, Any],
    config: ZfConfig | None,
) -> int:
    """Append one event per unresolved high/critical attention candidate."""
    try:
        event_log = event_log_from_project(state_dir, config=config, warn=False)
        events = event_log.read_all()
    except Exception:
        return 0
    open_keys = _open_attention_event_keys(events)
    projection_ref = _projection_ref(state_dir, projection)
    writer = EventWriter(event_log)
    emitted = 0
    for item in snapshot.get("attention_items") or []:
        if not isinstance(item, dict):
            continue
        if not is_actionable_attention(item):
            continue
        keys = _attention_dedupe_keys(item)
        if not keys or keys & open_keys:
            continue
        source_event_ids = [
            str(value)
            for value in item.get("source_event_ids") or []
            if str(value).strip()
        ]
        diagnostic_ref = _write_attention_diagnostic_ref(
            state_dir=state_dir,
            item=item,
            events=events,
            source_event_ids=source_event_ids,
            projection_ref=projection_ref,
        )
        payload = {
            "schema_version": "runtime.attention.needed.v0",
            "is_derived_projection": True,
            "project_id": str(snapshot.get("project_id") or ""),
            "attention_id": str(item.get("attention_id") or ""),
            "fingerprint": str(item.get("fingerprint") or ""),
            "severity": str(item.get("severity") or ""),
            "source": str(item.get("source") or ""),
            "title": str(item.get("title") or ""),
            "summary": str(item.get("summary") or ""),
            "task_id": str(item.get("task_id") or ""),
            "source_event_ids": source_event_ids,
            "source_ref": str(item.get("source_ref") or ""),
            "suggested_route": str(item.get("suggested_route") or "observe_only"),
            "suggested_action": (
                item.get("suggested_action")
                if isinstance(item.get("suggested_action"), dict) else {}
            ),
            "problem_envelope": problem_envelope_from_attention(item),
            "projection_ref": projection_ref,
        }
        if diagnostic_ref:
            payload["diagnostic_ref"] = diagnostic_ref
        try:
            writer.append(ZfEvent(
                type="runtime.attention.needed",
                actor="zf-supervisor",
                task_id=payload["task_id"] or None,
                payload=redact_obj(payload),
                causation_id=source_event_ids[0] if source_event_ids else None,
            ))
        except Exception:
            continue
        open_keys.update(keys)
        emitted += 1
    return emitted


def _write_attention_diagnostic_ref(
    *,
    state_dir: Path,
    item: dict[str, Any],
    events: list[ZfEvent],
    source_event_ids: list[str],
    projection_ref: str,
) -> dict[str, Any]:
    attention_id = str(item.get("attention_id") or "").strip()
    if not attention_id:
        return {}
    event_by_id = {event.id: event for event in events if event.id}
    excerpts = []
    for event_id in source_event_ids[:20]:
        event = event_by_id.get(event_id)
        if not event:
            continue
        excerpts.append({
            "event_id": event.id,
            "event_type": event.type,
            "task_id": event.task_id or "",
            "actor": event.actor or "",
            "payload": event.payload if isinstance(event.payload, dict) else {},
        })
    payload = {
        "schema_version": "supervisor.attention-evidence.v1",
        "attention": item,
        "projection_ref": projection_ref,
        "source_event_ids": source_event_ids,
        "event_excerpts": excerpts,
    }
    return write_sidecar_json(
        state_dir,
        f"diagnostics/supervisor/{attention_id}/evidence.json",
        payload,
        kind="diagnostic_trace",
        schema_version="supervisor.attention-evidence.v1",
        created_by="zf-supervisor",
        source_event_id=source_event_ids[0] if source_event_ids else "",
        access_scope={
            "visibility": "project",
            "actor": "run-manager",
            "purpose": "attention-evidence",
        },
        retention={"class": "audit_required"},
        required=False,
        preview=str(item.get("summary") or item.get("title") or attention_id)[:200],
    )


def _emit_control_loop_events(
    state_dir: Path,
    snapshot: dict[str, Any],
    *,
    projection: dict[str, Any],
    config: ZfConfig | None,
) -> int:
    """Append bounded supervisor decision / owner-visible message events."""
    try:
        event_log = event_log_from_project(state_dir, config=config, warn=False)
        events = event_log.read_all()
    except Exception:
        return 0
    projection_ref = _projection_ref(state_dir, projection)
    writer = EventWriter(event_log)
    emitted = 0
    contract = None
    if config is not None:
        try:
            from zf.core.workflow.reconcile_expected import contract_from_config
            contract = contract_from_config(config)
        except Exception:
            contract = None
    for event in build_supervisor_control_loop_events(
        snapshot,
        events=events,
        projection_ref=projection_ref,
        contract=contract,
    ):
        try:
            writer.append(event)
        except Exception:
            continue
        emitted += 1
    return emitted


def _open_attention_event_keys(events: list[ZfEvent]) -> set[str]:
    open_keys: set[str] = set()
    for event in events:
        if (
            event.type != "runtime.attention.needed"
            and event.type not in _ATTENTION_RESOLUTION_EVENTS
        ):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        keys = _attention_dedupe_keys(payload)
        if event.type in {
            "runtime.attention.needed",
            "runtime.attention.acknowledged",
            "runtime.attention.escalated",
        }:
            open_keys.update(keys)
        elif event.type == "runtime.attention.snoozed":
            if _snooze_still_active(payload):
                open_keys.update(keys)
            else:
                open_keys.difference_update(keys)
        else:
            open_keys.difference_update(keys)
    return open_keys


def _snooze_still_active(payload: dict[str, Any]) -> bool:
    value = str(payload.get("snooze_until") or "")
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > datetime.now(timezone.utc)


def _attention_dedupe_keys(payload: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    fingerprint = str(payload.get("fingerprint") or "").strip()
    attention_id = str(payload.get("attention_id") or "").strip()
    if fingerprint:
        keys.add(f"fingerprint:{fingerprint}")
    if attention_id:
        keys.add(f"attention_id:{attention_id}")
    return keys


def _projection_ref(state_dir: Path, projection: dict[str, Any]) -> dict[str, str]:
    return {
        "snapshot_path": _relative_ref(projection.get("snapshot_path"), state_dir),
        "attention_path": _relative_ref(projection.get("attention_path"), state_dir),
        "plan_integrity_path": _relative_ref(
            projection.get("plan_integrity_path"),
            state_dir,
        ),
        "plan_insights_path": _relative_ref(
            projection.get("plan_insights_path"),
            state_dir,
        ),
        "control_loop_path": _relative_ref(
            projection.get("control_loop_path"),
            state_dir,
        ),
        "pane_probe_path": _relative_ref(
            projection.get("pane_probe_path"),
            state_dir,
        ),
        "workflow_resume_path": _relative_ref(
            projection.get("workflow_resume_path"),
            state_dir,
        ),
        "snapshot_sha256": str(projection.get("hash") or ""),
    }


def _relative_ref(value: Any, state_dir: Path) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    return str(path.relative_to(state_dir)) if _is_relative(path, state_dir) else text


def _read_events(state_dir: Path, *, config: ZfConfig | None) -> list[ZfEvent]:
    try:
        return event_log_from_project(state_dir, config=config, warn=False).read_all()
    except Exception:
        return []


def _read_tasks(state_dir: Path) -> list[Task]:
    try:
        return TaskStore(state_dir / "kanban.json").list_all_with_archive(last_days=14)
    except Exception:
        return []


def _safe_automations(state_dir: Path, project_id: str, *, config: ZfConfig | None) -> dict[str, Any]:
    project_name = config.project.name if config is not None else project_id
    try:
        return project_automations(state_dir, project_id=project_id, project_name=project_name)
    except Exception:
        return {"schema_version": "project_automation.v1", "items": [], "automations": []}


def _safe_failure_signals(state_dir: Path) -> list[Any]:
    try:
        return collect_failure_signals(state_dir)
    except Exception:
        return []


def _completed_run_quiesced_attention_item(item: dict[str, Any]) -> dict[str, Any]:
    updated = dict(item)
    updated["status"] = "resolved"
    updated["severity"] = "info"
    updated["quiesced_by"] = "run.completed"
    updated["quiesce_reason"] = "completed_run_without_post_completion_regression"
    updated.pop("suggested_route", None)
    updated.pop("recommended_route", None)
    updated.pop("suggested_action", None)
    updated.pop("failure_class", None)
    updated.pop("primary_failure_class", None)
    return redact_obj(updated)


def _attention_with_problem_envelope(item: dict[str, Any]) -> dict[str, Any]:
    updated = dict(item)
    if not isinstance(updated.get("problem_envelope"), dict):
        updated["problem_envelope"] = problem_envelope_from_attention(updated)
    return redact_obj(updated)


def _trigger_history(state_dir: Path) -> list[dict[str, Any]]:
    try:
        return [row.to_dict() for row in read_trigger_decisions(state_dir)]
    except Exception:
        return []


def _safe_workflow_resume(
    state_dir: Path,
    *,
    config: ZfConfig | None,
    events: list[ZfEvent],
    tasks: list[Task],
) -> dict[str, Any]:
    if config is None:
        return {
            "schema_version": "workflow-resume.unavailable.v0",
            "summary": {"batch_pending": 0},
            "batch_checkpoints": [],
        }
    try:
        return build_workflow_resume_projection(
            state_dir,
            config,
            events=events,
            tasks=tasks,
        )
    except Exception as exc:
        return {
            "schema_version": "workflow-resume.unavailable.v0",
            "error": str(exc),
            "summary": {"batch_pending": 0},
            "batch_checkpoints": [],
        }


def _workflow_resume_attention_items(
    workflow_resume: dict[str, Any],
    *,
    state_dir: Path,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for checkpoint in workflow_resume.get("batch_checkpoints") or []:
        if not isinstance(checkpoint, dict):
            continue
        action = str(checkpoint.get("safe_resume_action") or "")
        if not action or action in {"no_action", "wait_for_children"}:
            continue
        checkpoint_id = str(checkpoint.get("checkpoint_id") or "").strip()
        if not checkpoint_id:
            continue
        source_event_ids = [
            str(value) for value in checkpoint.get("evidence_event_ids") or []
            if str(value).strip()
        ]
        blocking = str(checkpoint.get("blocking_event_id") or "").strip()
        if blocking and blocking not in source_event_ids:
            source_event_ids.append(blocking)
        pdd_id = str(checkpoint.get("pdd_id") or checkpoint.get("feature_id") or "")
        fanout_id = str(checkpoint.get("fanout_id") or "")
        failed_children = [
            str(value) for value in checkpoint.get("failed_children") or []
            if str(value).strip()
        ]
        completed_task_ids = [
            str(value) for value in checkpoint.get("completed_task_ids") or []
            if str(value).strip()
        ]
        severity = "high"
        fingerprint = f"workflow_resume_batch:{checkpoint_id}"
        items.append(redact_obj({
            "schema_version": "attention-item.v0",
            "attention_id": "attn-" + hashlib.sha1(
                fingerprint.encode("utf-8")
            ).hexdigest()[:12],
            "source": "workflow_resume",
            "fingerprint": fingerprint,
            "severity": severity,
            "status": "open",
            "title": "Workflow batch checkpoint can be resumed",
            "summary": (
                f"{pdd_id or fanout_id}: {action}; "
                f"failed_children={len(failed_children)}, "
                f"completed_task_ids={len(completed_task_ids)}."
            ),
            "task_id": "",
            "source_event_ids": source_event_ids,
            "source_ref": str(state_dir / "projections" / "workflow_resume.json"),
            "evidence_paths": [
                str(state_dir / "projections" / "supervisor" / "workflow-resume.json"),
                str(state_dir / "projections" / "workflow_resume.json"),
            ],
            "suggested_route": "run_manager_recovery",
            "suggested_action": {
                "kind": "workflow-batch-resume",
                "checkpoint_id": checkpoint_id,
                "safe_resume_action": action,
                "pdd_id": pdd_id,
                "fanout_id": fanout_id,
                "resume_scope": (
                    "failed_children_only"
                    if action == "repair_failed_children"
                    else "candidate_ready_reemit"
                    if action == "reemit_candidate_ready"
                    else "all_tasks_rework"
                ),
                "failed_children": failed_children,
                "completed_task_ids": completed_task_ids,
                "candidate_ref": str(checkpoint.get("candidate_ref") or ""),
                "candidate_head_commit": str(
                    checkpoint.get("candidate_head_commit") or ""
                ),
                "task_map_ref": str(checkpoint.get("task_map_ref") or ""),
            },
            "expected_output": [
                "validate_checkpoint_anchor",
                "run_zf_recover_workflow_resume_pending",
                "verify_idempotency",
            ],
        }))
    return items


def _stale_active_run_attention_items(
    *,
    state_dir: Path,
    tasks: list[Task],
    events: list[ZfEvent],
    now: datetime,
) -> list[dict[str, Any]]:
    session = _read_session(state_dir)
    runtime_state = str(session.get("runtime_state") or "").strip()
    if runtime_state != "active":
        return []
    active = [task for task in tasks if task.status not in {"done", "cancelled"}]
    if not active:
        return []
    in_progress = [task for task in active if task.status == "in_progress"]
    if len(in_progress) < max(1, int(len(active) * 0.75)):
        return []

    latest_liveness = _latest_event(events, _STALE_ACTIVE_RUN_LIVENESS_EVENTS)
    liveness_age = _age_seconds(latest_liveness.ts, now) if latest_liveness else None
    if liveness_age is not None and liveness_age < _STALE_ACTIVE_RUN_NO_PROGRESS_SECONDS:
        return []

    latest_progress = _latest_event(events, _STALE_ACTIVE_RUN_PROGRESS_EVENTS)
    progress_age = _age_seconds(latest_progress.ts, now) if latest_progress else None
    if progress_age is not None and progress_age < _STALE_ACTIVE_RUN_NO_PROGRESS_SECONDS:
        return []

    recent_failures = [
        event for event in events[-100:]
        if (
            event.type in _STALE_ACTIVE_RUN_FAILURE_EVENTS
            or event.type.endswith(".failed")
            or event.type.endswith(".rejected")
        )
    ]
    if not recent_failures:
        return []

    latest = recent_failures[-1]
    fingerprint = "stale-active-run:" + hashlib.sha1(
        f"{state_dir}:{len(in_progress)}:{latest.type}".encode("utf-8")
    ).hexdigest()[:12]
    return [redact_obj({
        "schema_version": "attention-item.v0",
        "attention_id": "attn-" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:12],
        "source": "stale_active_run",
        "fingerprint": fingerprint,
        "severity": "high",
        "status": "open",
        "title": "Active run appears stale",
        "summary": (
            f"{len(in_progress)}/{len(active)} active tasks remain in_progress; "
            f"latest blocking event is {latest.type}."
        ),
        "task_id": "",
        "source_event_ids": [latest.id],
        "source_ref": str(state_dir),
        "suggested_route": "l2_orchestrator",
        "suggested_action": {
            "kind": "recover_stale_active_run",
            "state_dir": str(state_dir),
            "options": ["resume", "stop --fast", "restart from checkpoint"],
            "latest_failure_event_type": latest.type,
            "latest_progress_age_sec": progress_age,
            "in_progress_task_ids": [task.id for task in in_progress[:20]],
        },
    })]


def _read_session(state_dir: Path) -> dict[str, Any]:
    path = Path(state_dir) / "session.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if isinstance(data, dict):
        session = data.get("session")
        if isinstance(session, dict):
            merged = dict(data)
            merged.update(session)
            return merged
        return data
    return {}


def _latest_event(events: list[ZfEvent], types: frozenset[str]) -> ZfEvent | None:
    for event in reversed(events):
        if event.type in types:
            return event
    return None


def _task_summary(tasks: list[Task]) -> dict[str, Any]:
    active = [t for t in tasks if t.status not in {"done", "cancelled"}]
    counts = Counter(t.status or "unknown" for t in tasks)
    return {
        "total": len(tasks),
        "active": len(active),
        "by_status": dict(sorted(counts.items())),
        "blocked": sum(1 for t in active if t.status == "blocked" or t.blocked_reason),
        "with_plan_ref": sum(1 for t in active if task_plan_refs(t)),
    }


def _worker_summary(state_dir: Path, events: list[ZfEvent], *, now: datetime) -> dict[str, Any]:
    meta = _role_session_meta(state_dir)
    states = Counter()
    workers = []
    last_heartbeat_ages: list[int] = []
    for instance_id, values in sorted(meta.items()):
        heartbeat = values.get("last_heartbeat_payload")
        payload = heartbeat if isinstance(heartbeat, dict) else {}
        state = str(payload.get("state") or values.get("state") or "unknown")
        current_task = str(payload.get("current_task_id") or "")
        last_at = str(values.get("last_heartbeat_at") or payload.get("ts") or "")
        age = _age_seconds(last_at, now)
        if age is not None:
            last_heartbeat_ages.append(age)
        states[state] += 1
        workers.append({
            "instance_id": instance_id,
            "backend": str(values.get("backend") or ""),
            "state": state,
            "current_task_id": current_task,
            "last_heartbeat_at": last_at,
            "last_heartbeat_age_sec": age,
        })
    stuck_events = [e for e in events if e.type in {"worker.stuck", "worker.stuck.recovery_failed"}]
    return redact_obj({
        "total": len(workers),
        "state_counts": dict(sorted(states.items())),
        "last_heartbeat_age_sec": max(last_heartbeat_ages) if last_heartbeat_ages else None,
        "stuck_event_count": len(stuck_events),
        "workers": workers[-50:],
    })


def _freshness(events: list[ZfEvent], *, now: datetime) -> dict[str, Any]:
    last = events[-1] if events else None
    return {
        "event_count": len(events),
        "last_event_id": last.id if last else "",
        "last_event_type": last.type if last else "",
        "last_event_at": last.ts if last else "",
        "last_event_age_sec": _age_seconds(last.ts, now) if last else None,
    }


def _role_session_meta(state_dir: Path) -> dict[str, dict[str, Any]]:
    path = Path(state_dir) / "role_sessions.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    meta = data.get("instance_meta") if isinstance(data, dict) else {}
    if not isinstance(meta, dict):
        return {}
    return {
        str(key): dict(value) if isinstance(value, dict) else {}
        for key, value in meta.items()
    }


def _spine_hint(state_dir: Path, project_id: str) -> dict[str, Any]:
    try:
        insight = project_spine_review_insight(state_dir, project_id=project_id)
    except Exception:
        return {"status": "unavailable"}
    return {
        "status": insight.get("status", ""),
        "review_id": insight.get("review_id", ""),
        "verdict": insight.get("verdict", ""),
        "runtime_status": insight.get("runtime_status", ""),
    }


def _stable_digest(value: dict[str, Any]) -> str:
    stable = _drop_volatile_fields(value)
    raw = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _drop_volatile_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_volatile_fields(child)
            for key, child in value.items()
            if key != "generated_at" and not key.endswith("_age_sec")
        }
    if isinstance(value, list):
        return [_drop_volatile_fields(child) for child in value]
    return value


def _age_seconds(value: str, now: datetime) -> int | None:
    parsed = _parse_ts(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_relative(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


__all__ = [
    "ATTENTION_SCHEMA_VERSION",
    "PLAN_INTEGRITY_SCHEMA_VERSION",
    "SNAPSHOT_SCHEMA_VERSION",
    "build_attention_items",
    "build_plan_integrity_projection",
    "build_supervisor_snapshot",
    "read_supervisor_snapshot",
    "run_supervisor_inspection",
    "supervisor_snapshot_ref",
    "write_supervisor_projection",
]
