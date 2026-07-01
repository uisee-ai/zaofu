"""Projections layer: tasks (moved verbatim from web/server.py)."""
from __future__ import annotations

from dataclasses import asdict
from fastapi import HTTPException
from pathlib import Path
from typing import Any
from zf.core.config.schema import ZfConfig
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.kanban_projection import kanban_column_label
from zf.core.task.kanban_projection import kanban_column_projection
from zf.core.task.kanban_projection import workflow_projection
from zf.core.task.lifecycle import derive_phase
from zf.core.task.schema import Task
from zf.core.task.schema import TaskContract
from zf.core.task.schema import TaskEvidence
from zf.core.task.store import TaskStore
from zf.integrations.feishu.views import TaskView
from zf.runtime.execution_route import project_execution_route
from zf.runtime.execution_route import project_route_summary
from zf.runtime.run_archive import read_task_runs
from zf.web.operator_contract import kanban_agent_evidence_model
from zf.web.operator_contract import kanban_agent_status_model
import json
from zf.web.projections.common import _artifact_ref_warnings_from_events, _deep_kanban_enabled, _first_artifact_ref_path, _first_nonempty, _git, _optional_str, _payload_mentions, _resolve_project_root_for_state, _string_list
from zf.web.projections.summaries import _refs_from_events, _safe_handoff_summary_projection, _safe_task_capsule_projection, _safe_task_operations_projection, _safe_task_progress_projection, _safe_task_run_panel_projection
from zf.web.projections.events import _EVENT_LOG_RUN_ID, _diagnostics, _event_log_fingerprint, _event_log_run_summary, _event_to_dict, _events_with_exact_task_id, _events_with_seq, _stage_summary, _trace_id_from_events
from zf.web.projections.workflow_graph import _workflow_judge_configured, _workflow_terminal_success_event
from zf.web.projections.operator import _operator_task_evidence
from zf.web.projections.agents import _workdir_for_instance
from zf.web.projections.fanouts import _candidate_detail, _fanout_child_projection, _fanout_manifest, _fanout_progress


_TASK_TIMELINE_CACHE: dict[tuple[str, str, int, bool, int, int], dict[str, Any]] = {}


_TASK_TIMELINE_CACHE_MAX = 128
_FANOUT_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "blocked",
    "cancelled",
    "timed_out",
    "suspended",
}
_FANOUT_CHILD_PHASES = {
    "queued": "fanout_child_queued",
    "dispatched": "fanout_child_running",
    "running": "fanout_child_running",
    "started": "fanout_child_running",
    "completed": "fanout_child_completed",
    "passed": "fanout_child_completed",
    "failed": "fanout_child_failed",
    "blocked": "fanout_child_failed",
    "timed_out": "fanout_child_failed",
}
_GLOBAL_FAILURE_EVENTS = {
    "candidate.conflict",
    "candidate.stale",
    "candidate.quality.failed",
    "integration.failed",
    "plan.rejected",
    "product.plan.blocked",
    "prd.blocked",
    "refactor.plan.blocked",
    "task.contract.invalid",
    "test.failed",
    "verify.failed",
    "judge.failed",
    "zaofu.refactor.plan.blocked",
}
_PLAN_FAILURE_EVENTS = {
    "plan.rejected",
    "product.plan.blocked",
    "prd.blocked",
    "refactor.plan.blocked",
    "task.contract.invalid",
    "zaofu.refactor.plan.blocked",
}
_PLAN_FAILURE_SUPERSEDING_EVENTS = {
    "plan.ready",
    "product.plan.ready",
    "prd.ready",
    "prd.approved",
    "refactor.plan.ready",
    "zaofu.refactor.plan.ready",
    "task_map.ready",
    "task_map.amended",
    "gap_plan.ready",
    "goal.gap_plan.ready",
    "module.parity.gap_plan.ready",
}
_GLOBAL_FAILURE_REF_KEYS = {
    "candidate_ref",
    "candidate_branch",
    "candidate_id",
    "candidate_task_map_ref",
    "feature_id",
    "latest_task_map_ref",
    "new_task_map_ref",
    "old_task_map_ref",
    "pdd_id",
    "plan_ref",
    "source_index_ref",
    "target_ref",
    "task_map_ref",
}
_TERMINAL_TASK_STATUSES = {"done", "cancelled", "superseded", "archived"}
_RUN_COMPLETED_PROJECTION_RESET_EVENTS = {
    "run.goal.started",
    "task_map.ready",
    "task_map.amended",
    "gap_plan.ready",
    "module.parity.gap_plan.ready",
    "task.assigned",
    "task.dispatched",
    "fanout.started",
    "fanout.child.dispatched",
    "candidate.ready",
    "verify.failed",
    "test.failed",
    "judge.failed",
    "run.failed",
}


def _task_counts(state_dir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    path = state_dir / "kanban.json"
    if not path.exists():
        return counts
    try:
        tasks = TaskStore(path).list_all_with_archive()
    except Exception:
        return counts
    for task in tasks:
        status = str(getattr(task, "status", "") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _workflow_events_with_candidate_context(
    task: Task | str,
    task_events: list[tuple[int, ZfEvent]],
    all_events: list[tuple[int, ZfEvent]] | None = None,
) -> list[tuple[int, ZfEvent]]:
    """Add projection-only workflow hints from candidate-level fanout events.

    Lane-pipeline fanouts publish candidate/stage events with ``task_id=None``.
    They still carry the task id in payload fields such as
    ``completed_task_ids``, ``upstream_task_id`` or ``findings``. The task card
    should display those workflow facts without mutating kernel truth.
    """
    task_id = task.id if isinstance(task, Task) else str(task)
    if not task_id:
        return task_events
    projected: list[tuple[int, ZfEvent]] = list(task_events)
    has_impl_gate = any(
        event.type in {
            "static_gate.passed",
            "static_gate.failed",
            "static_gate.skipped",
        }
        for _, event in task_events
    )
    for seq, event in task_events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if not has_impl_gate and _candidate_impl_completed_for_task(
            event.type,
            payload,
            task_id,
        ):
            projected.append((
                seq,
                _projection_workflow_event(
                    "static_gate.passed",
                    task_id,
                    event,
                    reason="candidate implementation fanout completed",
                ),
            ))
            has_impl_gate = True
        failure_type = _candidate_failure_workflow_event_type(
            event.type,
            payload,
            task_id,
        )
        if failure_type:
            projected.append((
                seq,
                _projection_workflow_event(
                    failure_type,
                    task_id,
                    event,
                    reason=_candidate_failure_reason(payload, task_id, event.type),
                    rework_target="dev",
                ),
            ))
    if isinstance(task, Task) and all_events:
        context_refs = _task_failure_context_refs(task, task_events)
        existing_event_ids = {
            str(getattr(event, "id", "") or "")
            for _, event in projected
            if getattr(event, "id", "")
        }
        for seq, event in all_events:
            event_id = str(getattr(event, "id", "") or "")
            if event_id and event_id in existing_event_ids:
                continue
            if not _global_failure_applies_to_task(
                task.id,
                event,
                context_refs=context_refs,
            ):
                continue
            if _global_failure_superseded_for_task(
                task.id,
                seq,
                event,
                all_events,
                context_refs=context_refs,
            ):
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            projected.append((
                seq,
                _projection_workflow_event(
                    _global_failure_projection_type(event.type),
                    task.id,
                    event,
                    reason=_candidate_failure_reason(payload, task.id, event.type),
                    rework_target=_global_failure_rework_target(event.type, payload),
                ),
            ))
            if event_id:
                existing_event_ids.add(event_id)
    if len(projected) == len(task_events):
        return task_events
    return sorted(projected, key=lambda item: item[0])


def _task_failure_context_refs(
    task: Task,
    task_events: list[tuple[int, ZfEvent]],
) -> set[str]:
    refs = {
        str(value).strip()
        for value in _refs_from_events(task_events, task=task).values()
        if _usable_failure_ref(value)
    }
    contract = task.contract
    for attr in (
        "feature_id",
        "plan_ref",
        "source_index_ref",
        "spec_ref",
        "tdd_ref",
        "critic_gate_ref",
        "product_contract_ref",
    ):
        refs.add(str(getattr(contract, attr, "") or "").strip())
    evidence_contract = getattr(contract, "evidence_contract", {}) or {}
    if isinstance(evidence_contract, dict):
        source_refs = evidence_contract.get("source_refs") or {}
        if isinstance(source_refs, dict):
            refs.update(
                str(value).strip()
                for value in source_refs.values()
                if _usable_failure_ref(value)
            )
        elif isinstance(source_refs, list):
            refs.update(
                str(value).strip()
                for value in source_refs
                if _usable_failure_ref(value)
            )
    return {ref for ref in refs if _usable_failure_ref(ref)}


def _usable_failure_ref(value: object) -> bool:
    ref = str(value or "").strip()
    if len(ref) < 3:
        return False
    return ref.lower() not in {"head", "main", "master", "true", "false"}


def _global_failure_applies_to_task(
    task_id: str,
    event: ZfEvent,
    *,
    context_refs: set[str],
) -> bool:
    event_type = str(getattr(event, "type", "") or "")
    if event_type not in _GLOBAL_FAILURE_EVENTS:
        return False
    return _event_context_applies_to_task(task_id, event, context_refs=context_refs)


def _event_context_applies_to_task(
    task_id: str,
    event: ZfEvent,
    *,
    context_refs: set[str],
) -> bool:
    payload = event.payload if isinstance(event.payload, dict) else {}
    if getattr(event, "task_id", None) == task_id:
        return True
    if task_id in _payload_task_ids(payload):
        return True
    for key in _GLOBAL_FAILURE_REF_KEYS:
        value = payload.get(key)
        if _usable_failure_ref(value) and str(value).strip() in context_refs:
            return True
    return any(_payload_mentions(payload, ref) for ref in context_refs)


def _global_failure_superseded_for_task(
    task_id: str,
    failure_seq: int,
    failure: ZfEvent,
    all_events: list[tuple[int, ZfEvent]],
    *,
    context_refs: set[str],
) -> bool:
    if str(getattr(failure, "type", "") or "") not in _PLAN_FAILURE_EVENTS:
        return False
    for seq, event in all_events:
        if seq <= failure_seq:
            continue
        if str(getattr(event, "type", "") or "") not in _PLAN_FAILURE_SUPERSEDING_EVENTS:
            continue
        if _event_context_applies_to_task(task_id, event, context_refs=context_refs):
            return True
    return False


def _global_failure_projection_type(event_type: str) -> str:
    if event_type in {"test.failed", "verify.failed", "judge.failed"}:
        return event_type
    if event_type in _PLAN_FAILURE_EVENTS:
        return "review.rejected"
    return "verify.failed"


def _global_failure_rework_target(event_type: str, payload: dict[str, Any]) -> str:
    target = str(
        payload.get("rework_target")
        or payload.get("target_role")
        or payload.get("route_to")
        or ""
    ).strip()
    if target:
        return target
    if event_type in _PLAN_FAILURE_EVENTS:
        return "plan"
    return "dev"


def _projection_workflow_event(
    event_type: str,
    task_id: str,
    source: ZfEvent,
    *,
    reason: str,
    rework_target: str = "",
) -> ZfEvent:
    payload = {
        "reason": reason,
        "source_event_id": source.id,
        "source_event_type": source.type,
        "projection_only": True,
    }
    if rework_target:
        payload["rework_target"] = rework_target
    return ZfEvent(
        type=event_type,
        actor="zf-web-projection",
        task_id=task_id,
        payload=payload,
        correlation_id=source.correlation_id,
    )


def _candidate_impl_completed_for_task(
    event_type: str,
    payload: dict[str, Any],
    task_id: str,
) -> bool:
    if event_type == "candidate.ready":
        return task_id in _payload_task_ids(payload)
    if event_type != "fanout.aggregate.completed":
        return False
    status = str(payload.get("status") or "").strip().lower()
    if status != "completed":
        return False
    stage_id = str(payload.get("stage_id") or "").strip().lower()
    if stage_id and not any(token in stage_id for token in ("impl", "implementation")):
        return False
    return task_id in _payload_task_ids(payload)


def _candidate_failure_workflow_event_type(
    event_type: str,
    payload: dict[str, Any],
    task_id: str,
) -> str:
    if task_id not in _payload_task_ids(payload):
        return ""
    if event_type in {"verify.failed", "test.failed", "judge.failed"}:
        return event_type
    if event_type not in {
        "fanout.child.failed",
        "review.child.failed",
        "verify.child.failed",
        "test.child.failed",
        "judge.child.failed",
        "workflow.child.failed",
    }:
        return ""
    stage_id = str(payload.get("stage_id") or "").lower()
    child_id = str(payload.get("child_id") or "").lower()
    marker = f"{stage_id} {child_id} {event_type}".lower()
    if "judge" in marker:
        return "judge.failed"
    if "test" in marker or "verify" in marker:
        return "verify.failed"
    if "review" in marker:
        return "review.rejected"
    return ""


def _payload_task_ids(payload: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("task_id", "upstream_task_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            ids.add(value)
    for key in ("completed_task_ids", "failed_task_ids", "task_ids"):
        raw = payload.get(key)
        if isinstance(raw, list):
            ids.update(str(item).strip() for item in raw if str(item).strip())
    findings = payload.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            for key in ("task_id", "upstream_task_id"):
                value = str(item.get(key) or "").strip()
                if value:
                    ids.add(value)
    return ids


def _candidate_failure_reason(
    payload: dict[str, Any],
    task_id: str,
    fallback: str,
) -> str:
    findings = payload.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            if task_id not in {
                str(item.get("task_id") or "").strip(),
                str(item.get("upstream_task_id") or "").strip(),
            }:
                continue
            reason = str(
                item.get("message")
                or item.get("summary")
                or item.get("title")
                or item.get("reason")
                or ""
            ).strip()
            if reason:
                return reason
    return str(payload.get("reason") or fallback).strip() or fallback


def _latest_task_fanout_runtime(
    state_dir: Path,
    task_id: str,
    all_events: list[tuple[int, ZfEvent]],
) -> dict[str, Any]:
    """Return the newest fanout child runtime for a task, projection-only."""
    if not task_id:
        return {}
    last_seq_by_fanout: dict[str, int] = {}
    started_seq_by_fanout: dict[str, int] = {}
    for seq, event in all_events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "").strip()
        if not fanout_id:
            continue
        last_seq_by_fanout[fanout_id] = max(seq, last_seq_by_fanout.get(fanout_id, 0))
        if event.type == "fanout.started":
            started_seq_by_fanout[fanout_id] = seq

    latest: dict[str, Any] = {}
    for manifest_path in sorted((state_dir / "fanouts").glob("*/manifest.json")):
        fanout_id = manifest_path.parent.name
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        seq = started_seq_by_fanout.get(
            fanout_id,
            last_seq_by_fanout.get(fanout_id, 0),
        )
        children = [
            child for child in manifest.get("children", []) or []
            if isinstance(child, dict)
        ]
        for child in children:
            if str(child.get("task_id") or "").strip() != task_id:
                continue
            projected_child = _fanout_child_projection(child)
            projected = {
                "fanout_id": fanout_id,
                "stage_id": str(manifest.get("stage_id") or ""),
                "fanout_status": str(manifest.get("status") or "observed"),
                "child": projected_child,
                "child_id": str(projected_child.get("child_id") or ""),
                "child_status": str(projected_child.get("status") or "observed"),
                "role_instance": str(projected_child.get("role_instance") or ""),
                "lane_id": str(child.get("lane_id") or ""),
                "affinity_tag": str(child.get("affinity_tag") or ""),
                "assignment_strategy": str(child.get("assignment_strategy") or ""),
                "run_id": str(projected_child.get("run_id") or ""),
                "source_branch": str(projected_child.get("source_branch") or ""),
                "source_commit": str(projected_child.get("source_commit") or ""),
                "task_ref": str(projected_child.get("task_ref") or ""),
                "workdir": str(projected_child.get("workdir") or ""),
                "progress": _fanout_progress([
                    _fanout_child_projection(item) for item in children
                ]),
                "seq": seq,
            }
            if not latest or seq >= int(latest.get("seq") or 0):
                latest = projected
    return latest


def _fanout_phase_override(latest_fanout: dict[str, Any]) -> str:
    fanout_status = str(latest_fanout.get("fanout_status") or "").lower()
    if not latest_fanout or fanout_status in _FANOUT_TERMINAL_STATUSES:
        return ""
    child_status = str(latest_fanout.get("child_status") or "").lower()
    return _FANOUT_CHILD_PHASES.get(child_status, "fanout_child_running")


def _fanout_workflow_override(
    workflow: dict[str, Any],
    latest_fanout: dict[str, Any],
) -> dict[str, Any]:
    phase = _fanout_phase_override(latest_fanout)
    if not phase:
        return workflow
    child_status = str(latest_fanout.get("child_status") or "observed")
    out = dict(workflow)
    out["workflow_phase"] = "impl"
    out["impl_exit_gate_state"] = "pending"
    if out.get("verify_state") in {"empty", "pending"}:
        out["verify_state"] = "waiting"
    badges = [
        dict(item) for item in out.get("badges", []) or []
        if not (
            item.get("kind") == "verify"
            and item.get("state") in {"pending", "empty"}
        )
    ]
    badges.append({
        "kind": "fanout_child",
        "label": f"fanout {child_status}",
        "tone": _fanout_child_badge_tone(child_status),
        "state": child_status,
    })
    out["badges"] = badges
    return out


def _kanban_display_projection(
    task: Task,
    *,
    phase: str | None,
    ready: bool,
    workflow: dict[str, Any],
) -> dict[str, Any]:
    projection = kanban_column_projection(
        task,
        phase=phase,
        ready=ready,
        extra_badges=_workflow_column_badges(workflow),
    )
    column = projection.column
    label = projection.label
    reason = projection.reason
    badges = list(projection.badges)
    if (
        _workflow_has_failure(workflow)
        and column != "done"
        and str(task.status or "").strip().lower() not in _TERMINAL_TASK_STATUSES
    ):
        column = "blocked"
        label = kanban_column_label(column)
        reason = _workflow_failure_reason(workflow)
        badges = [*badges, "failed"]
    return {
        "column": column,
        "label": label,
        "reason": reason,
        "badges": list(dict.fromkeys(badges)),
    }


def _workflow_has_failure(workflow: dict[str, Any]) -> bool:
    return any(
        str(workflow.get(key) or "") == "failed"
        for key in ("impl_exit_gate_state", "verify_state", "judge_state")
    )


def _workflow_column_badges(workflow: dict[str, Any]) -> list[str]:
    badges: list[str] = []
    if _workflow_has_failure(workflow):
        badges.append("failed")
    for badge in workflow.get("badges", []) or []:
        if not isinstance(badge, dict):
            continue
        if str(badge.get("tone") or "") == "err":
            label = str(badge.get("label") or badge.get("kind") or "").strip()
            badges.append(label or "failed")
    return badges


def _workflow_failure_reason(workflow: dict[str, Any]) -> str:
    reason = str(workflow.get("rework_reason") or "").strip()
    if reason:
        return f"workflow_failure:{reason}"
    for key in ("judge_state", "verify_state", "impl_exit_gate_state"):
        if str(workflow.get(key) or "") == "failed":
            return f"workflow_failure:{key}=failed"
    return "workflow_failure"


def _fanout_child_badge_tone(status: str) -> str:
    value = str(status or "").lower()
    if value in {"completed", "passed"}:
        return "ok"
    if value in {"failed", "blocked", "timed_out"}:
        return "err"
    if value in {"queued", "dispatched", "running", "started"}:
        return "warn"
    return "muted"


def _task_detail(
    state_dir: Path,
    task_id: str,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> dict | None:
    path = state_dir / "kanban.json"
    if not path.exists():
        return None
    task = TaskStore(path).get(task_id)
    if task is None:
        return None

    task_events = _task_events_with_seq(
        state_dir,
        task_id,
        config=config,
        include_payload_mentions=True,
    )
    # NOTE: kept on the full replay deliberately. _global_failure_applies_to_task
    # matches via _payload_mentions() scanning the WHOLE payload for refs, so a
    # slim/truncated payload silently drops failure-context matches (caught by
    # test_candidate_integration_failure_projects_to_task_detail_and_snapshot).
    # Making task detail fast needs ref-scoped hydration, tracked as P1.
    all_events = _events_with_seq(state_dir, config=config)
    latest_fanout = _latest_task_fanout_runtime(state_dir, task.id, all_events)
    trace_id = _trace_id_from_events(task_events)
    refs = _refs_from_events(task_events, task=task)
    if latest_fanout:
        refs = {**refs, **{
            key: latest_fanout.get(key, "")
            for key in (
                "fanout_id",
                "child_id",
                "run_id",
                "workdir",
                "source_branch",
                "source_commit",
                "task_ref",
                "lane_id",
                "affinity_tag",
                "assignment_strategy",
            )
            if latest_fanout.get(key, "")
        }}
    role_instance = task.assigned_to or refs.get("role_instance") or ""
    workdir = _workdir_for_instance(
        state_dir,
        role_instance,
        config=config,
        project_root=project_root,
    )
    briefing = _task_briefing(state_dir, task_id)
    diagnostics = _diagnostics(state_dir, trace_id) if trace_id else {
        "trace_id": "",
        "items": [],
        "empty": True,
    }
    phase = derive_phase(task, [event for _, event in task_events]) if task_events else None
    phase = _fanout_phase_override(latest_fanout) or phase
    workflow = workflow_projection(
        task,
        _workflow_events_with_candidate_context(task, task_events, all_events),
        phase=phase,
        judge_configured=_workflow_judge_configured(config),
        terminal_success_event=_workflow_terminal_success_event(config),
    ).to_dict()
    workflow = _fanout_workflow_override(workflow, latest_fanout)
    kanban_display = _kanban_display_projection(
        task,
        phase=phase,
        ready=False,
        workflow=workflow,
    )
    verify = _stage_summary(task_events, {"test.", "verify.", "judge."})
    review = _stage_summary(task_events, {"review."})
    try:
        runs = _task_runs(
            state_dir,
            project_root=_resolve_project_root_for_state(state_dir, project_root),
            task_id=task_id,
        ).get("runs", [])
    except Exception:
        runs = []
    status_model = {
        **kanban_agent_status_model(),
        "task_status": task.status,
        "terminal": task.status in {"done", "cancelled"},
    }
    interaction_evidence = _operator_task_evidence(state_dir, task_id)
    evidence_model = {
        **kanban_agent_evidence_model(),
        "task_status": task.status,
        "task_status_source": "TaskStore(active/archive)",
        "run_completed_implies_task_done": False,
        "execution": {
            "event_count": len(task_events),
            "run_count": len(runs) if isinstance(runs, list) else 0,
            "trace_id": trace_id or "",
            "fanout_id": refs.get("fanout_id") or "",
            "fanout_child_status": latest_fanout.get("child_status") or "",
            "verify_state": verify["state"],
            "review_state": review["state"],
        },
        "interaction": interaction_evidence,
    }
    progress_projection = _safe_task_progress_projection(state_dir, task_id)
    task_capsule = _safe_task_capsule_projection(state_dir, task)
    operations_projection = _safe_task_operations_projection(state_dir, task_id)
    execution_route = project_execution_route(
        task_events,
        task_id=task_id,
        trace_id=str(trace_id or ""),
    )
    task_run_panel = _safe_task_run_panel_projection(
        task=task,
        task_events=task_events,
        operations_projection=operations_projection,
        progress_projection=progress_projection,
        runs=runs,
        execution_route=execution_route,
        workdir=workdir,
        role_instance=role_instance,
        transcript_count=int(interaction_evidence.get("transcript_count") or 0),
    )
    handoff_summary = _safe_handoff_summary_projection(
        state_dir,
        task_id,
        task=task,
        task_events=task_events,
        config=config,
        project_root=project_root,
    )
    artifact_refs = _task_artifact_refs(
        state_dir,
        task_id,
        project_root=project_root,
    )
    artifact_ref_warnings = _artifact_ref_warnings_from_events(task_events)
    if artifact_ref_warnings:
        artifact_refs = dict(artifact_refs or {})
        diagnostics = artifact_refs.get("diagnostics", [])
        if not isinstance(diagnostics, list):
            diagnostics = []
        artifact_refs["diagnostics"] = [*diagnostics, *artifact_ref_warnings]

    task_payload = redact_obj(asdict(task))
    if isinstance(task_payload, dict):
        task_payload.update({
            "phase": phase,
            "workflow_phase": workflow["workflow_phase"],
            "impl_exit_gate_state": workflow["impl_exit_gate_state"],
            "verify_state": workflow["verify_state"],
            "judge_state": workflow["judge_state"],
            "verify_lanes": workflow["verify_lanes"],
            "workflow_badges": workflow["badges"],
            "workflow_projection": workflow,
            "kanban_column": kanban_display["column"],
            "kanban_column_label": kanban_display["label"],
            "kanban_column_reason": kanban_display["reason"],
            "kanban_column_badges": kanban_display["badges"],
        })

    return {
        "task": task_payload,
        "contract": redact_obj(asdict(task.contract)),
        "artifact_refs": redact_obj(artifact_refs),
        "evidence": redact_obj(asdict(task.evidence)) if task.evidence else {},
        "status_model": status_model,
        "evidence_model": redact_obj(evidence_model),
        "runs": redact_obj(runs),
        "progress_projection": redact_obj(progress_projection),
        "task_capsule": redact_obj(task_capsule),
        "operations": redact_obj(operations_projection),
        "execution_route": redact_obj(execution_route),
        "task_run_panel": redact_obj(task_run_panel),
        "handoff_summary": redact_obj(handoff_summary),
        "events": [_event_to_dict(seq, event) for seq, event in task_events[-80:]],
        "trace_id": trace_id,
        "correlation_id": trace_id,
        "links": {
            "trace": trace_id or "",
            "candidate": (
                refs.get("pdd_id")
                or refs.get("candidate_ref")
                or refs.get("candidate_branch")
                or refs.get("candidate_id")
                or ""
            ),
            "fanout": refs.get("fanout_id") or "",
        },
        "role_instance": role_instance,
        "workdir": workdir,
        "briefing": briefing,
        "git": refs,
        "fanout": _task_fanout_projection(state_dir, refs, latest_fanout),
        "phase": phase,
        "workflow_projection": workflow,
        "verify": verify,
        "review": review,
        "diagnostics": diagnostics,
    }


def _task_timeline(
    state_dir: Path,
    task_id: str,
    config: ZfConfig | None = None,
    *,
    limit: int = 200,
    deep: bool = False,
) -> dict | None:
    path = state_dir / "kanban.json"
    if not path.exists():
        return None
    if TaskStore(path).get(task_id) is None:
        return None
    event_limit = max(1, min(int(limit or 200), 1000))
    if not deep:
        try:
            from zf.web.projections import read_model

            projected = read_model.task_timeline(
                state_dir,
                task_id,
                limit=event_limit,
                config=config,
            )
            if projected is not None:
                raw_events: list[tuple[int, ZfEvent]] = []
                for item in projected.get("timeline", []):
                    if not isinstance(item, dict):
                        continue
                    event = read_model.hydrate_event_by_seq(
                        state_dir,
                        int(item.get("seq") or 0),
                        config=config,
                    )
                    if event is not None:
                        raw_events.append((int(item.get("seq") or 0), event))
                trace_id = _trace_id_from_events(raw_events) or projected.get("timeline", [{}])[-1].get("correlation_id", "")
                execution_route = project_execution_route(
                    raw_events,
                    task_id=task_id,
                    trace_id=str(trace_id or ""),
                )
                projected.update({
                    "trace_id": trace_id,
                    "correlation_id": trace_id,
                    "links": {"trace": trace_id or ""},
                    "execution_route": redact_obj(execution_route),
                    "query": {
                        "limit": event_limit,
                        "deep": False,
                        "match": "task_id",
                    },
                    "empty": not projected.get("timeline"),
                })
                return projected
        except Exception:
            pass
    cache_key = (
        str(state_dir.resolve()),
        task_id,
        event_limit,
        bool(deep),
        *_event_log_fingerprint(state_dir),
    )
    cached = _TASK_TIMELINE_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)
    task_events = _task_events_with_seq(
        state_dir,
        task_id,
        config=config,
        include_payload_mentions=deep,
        limit=event_limit,
    )
    trace_id = _trace_id_from_events(task_events)
    execution_route = project_execution_route(
        task_events,
        task_id=task_id,
        trace_id=str(trace_id or ""),
    )
    result = {
        "schema_version": "task-timeline.v1",
        "task_id": task_id,
        "event_count": len(task_events),
        "timeline": [_event_to_dict(seq, event) for seq, event in task_events],
        "trace_id": trace_id,
        "correlation_id": trace_id,
        "links": {"trace": trace_id or ""},
        "execution_route": redact_obj(execution_route),
        "query": {
            "limit": event_limit,
            "deep": bool(deep),
            "match": "task_id_or_payload" if deep else "task_id",
        },
        "empty": not task_events,
    }
    _TASK_TIMELINE_CACHE[cache_key] = result
    if len(_TASK_TIMELINE_CACHE) > _TASK_TIMELINE_CACHE_MAX:
        _TASK_TIMELINE_CACHE.pop(next(iter(_TASK_TIMELINE_CACHE)), None)
    return result


def _task_artifact_refs(
    state_dir: Path,
    task_id: str,
    *,
    project_root: Path | None = None,
) -> dict:
    path = state_dir / "refs" / "task-index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    entry = data.get(task_id)
    if not isinstance(entry, dict):
        return {}
    artifact_refs = [
        dict(item) for item in entry.get("artifact_refs", [])
        if isinstance(item, dict)
    ]
    hash_status = [
        dict(item) for item in entry.get("hash_status", [])
        if isinstance(item, dict)
    ]
    accepted_refs = [
        item for item in artifact_refs
        if str(item.get("status") or "accepted") in {"", "accepted"}
    ]
    stale_refs = [
        item for item in artifact_refs
        if str(item.get("status") or "accepted") not in {"", "accepted"}
    ]
    diagnostics = []
    bad_hashes = [
        item for item in hash_status
        if str(item.get("status") or "") in {"missing", "mismatch"}
        and str(item.get("ledger_status") or "accepted") in {"", "accepted"}
    ]
    if bad_hashes:
        diagnostics.append({
            "type": "artifact_hash_failure",
            "severity": "error",
            "count": len(bad_hashes),
        })
    task_map_summary = {}
    task_map_ref = _first_artifact_ref_path(accepted_refs, {"task_map", "work_unit_map"})
    if task_map_ref:
        try:
            from zf.runtime.task_map import resolve_artifact_file, summarize_task_map_file

            task_map_path = resolve_artifact_file(
                task_map_ref,
                project_root=project_root or state_dir.parent,
                state_dir=state_dir,
            )
            task_map_summary = summarize_task_map_file(task_map_path)
        except Exception as exc:
            task_map_summary = {
                "path": task_map_ref,
                "passed": False,
                "errors": [str(exc)],
            }
    return {
        "schema_version": "task-artifact-ledger.v1",
        "task_index_path": str(path),
        "manifest_event_id": entry.get("manifest_event_id", ""),
        "manifest_role": entry.get("manifest_role", ""),
        "contract_refs": entry.get("contract_refs", {}),
        "artifact_refs_by_kind": entry.get("artifact_refs_by_kind", {}),
        "artifact_refs": artifact_refs,
        "accepted_artifact_refs": accepted_refs,
        "stale_artifact_refs": stale_refs,
        "hash_status": hash_status,
        "handoff_contract": entry.get("handoff_contract", {}),
        "task_map_summary": task_map_summary,
        "diagnostics": diagnostics,
    }


def _task_diff(
    state_dir: Path,
    task_id: str,
    config: ZfConfig | None = None,
    project_root: Path | None = None,
) -> dict:
    path = state_dir / "kanban.json"
    if not path.exists():
        raise HTTPException(404, f"task {task_id!r} not found")
    task = TaskStore(path).get(task_id)
    if task is None:
        raise HTTPException(404, f"task {task_id!r} not found")

    task_events = _task_events_with_seq(
        state_dir,
        task_id,
        config=config,
        include_payload_mentions=False,
    )
    git = _refs_from_events(task_events, task=task)
    project_root = _resolve_project_root_for_state(state_dir, project_root)
    role_instance = task.assigned_to or git.get("role_instance") or ""
    workdir = _workdir_for_instance(
        state_dir,
        str(role_instance),
        config=config,
        project_root=project_root,
    )
    project_path = Path(str(workdir.get("project_path") or project_root))
    cwd = project_path if (project_path / ".git").exists() else project_root
    if not (cwd / ".git").exists():
        return {
            "task_id": task_id,
            "base": "",
            "head": "",
            "files": [],
            "diff": "",
            "truncated": False,
            "error": "git repository not available",
        }

    base = str(git.get("base_commit") or git.get("base_ref") or "")
    head = str(
        git.get("task_ref")
        or git.get("source_commit")
        or git.get("commit")
        or ""
    )
    if not head:
        return {
            "task_id": task_id,
            "base": base,
            "head": "",
            "files": [],
            "diff": "",
            "truncated": False,
            "error": "task has no source_commit or task_ref",
        }

    range_spec = f"{base}..{head}" if base else f"{head}^!"
    files_out = _git(
        cwd,
        ["diff", "--name-only", range_spec, "--", ".", ":(exclude).zf/**"],
    )
    diff_out = _git(
        cwd,
        [
            "diff",
            "--no-ext-diff",
            "--find-renames",
            "--unified=3",
            range_spec,
            "--",
            ".",
            ":(exclude).zf/**",
        ],
        max_bytes=90_000,
    )
    return {
        "task_id": task_id,
        "base": base,
        "head": head,
        "range": range_spec,
        "cwd": str(cwd),
        "files": [line for line in files_out.text.splitlines() if line],
        "diff": redact_obj(diff_out.text),
        "truncated": diff_out.truncated,
        "error": files_out.error or diff_out.error,
    }


def _kanban(state_dir: Path, config: ZfConfig | None = None) -> list[dict]:
    """Tasks list with derived phase (R-TASK-STATE-AXIS-01)."""
    path = state_dir / "kanban.json"
    if not path.exists():
        return []
    ts = TaskStore(path)
    tasks = ts.list_all()
    try:
        ready_ids = {t.id for t in ts.ready()}
    except Exception:
        ready_ids = set()
    events_path = state_dir / "events.jsonl"
    events = []
    if events_path.exists():
        try:
            events = list(event_log_from_project(state_dir, config=config).read_days(1))
        except Exception:
            events = []
    all_events = _events_with_seq(state_dir, config=config)
    completed_closeout = _current_completed_run_closeout_for_projection(all_events)
    out = []
    for t in tasks:
        task_events = [
            (seq, event)
            for seq, event in all_events
            if getattr(event, "task_id", None) == t.id
            or _payload_mentions(getattr(event, "payload", {}) or {}, t.id)
        ]
        refs = _refs_from_events(task_events, task=t)
        latest_fanout = _latest_task_fanout_runtime(state_dir, t.id, all_events)
        if latest_fanout:
            refs = {**refs, **{
                key: latest_fanout.get(key, "")
                for key in (
                    "fanout_id",
                    "child_id",
                    "run_id",
                    "workdir",
                    "source_branch",
                    "source_commit",
                    "task_ref",
                    "lane_id",
                    "affinity_tag",
                    "assignment_strategy",
                )
                if latest_fanout.get(key, "")
            }}
        latest = _event_to_dict(*task_events[-1]) if task_events else None
        candidate_ref = (
            refs.get("pdd_id")
            or refs.get("candidate_ref")
            or refs.get("candidate_branch")
            or refs.get("candidate_id")
            or ""
        )
        evidence_badges = _task_evidence_badges(
            state_dir=state_dir,
            ready=t.id in ready_ids,
            refs=refs,
            latest=latest,
        )
        why_not_done = {}
        workpad = {}
        retry_metadata = {}
        if _deep_kanban_enabled():
            try:
                from zf.runtime.long_horizon import project_why_not_done

                why_not_done = project_why_not_done(
                    state_dir,
                    t.id,
                    config=config,
                    project_root=_resolve_project_root_for_state(state_dir, None),
                ).to_dict()
            except Exception:
                why_not_done = {}
            try:
                from zf.runtime.long_horizon import (
                    project_retry_metadata,
                    project_workpad,
                )

                workpad = project_workpad(
                    state_dir,
                    t.id,
                    config=config,
                    project_root=_resolve_project_root_for_state(state_dir, None),
                ).to_dict()
                retry_metadata = project_retry_metadata(state_dir, t.id).to_dict()
            except Exception:
                workpad = {}
                retry_metadata = {}
        source = _task_source_from_events(task_events)
        phase = derive_phase(t, events) if events else None
        phase = _fanout_phase_override(latest_fanout) or phase
        route_summary = project_route_summary(task_events, task_id=t.id)
        fanout_projection = _task_fanout_projection(state_dir, refs, latest_fanout)
        workflow = workflow_projection(
            t,
            _workflow_events_with_candidate_context(t, task_events, all_events),
            phase=phase,
            judge_configured=_workflow_judge_configured(config),
            terminal_success_event=_workflow_terminal_success_event(config),
        ).to_dict()
        workflow = _fanout_workflow_override(workflow, latest_fanout)
        kanban_display = _kanban_display_projection(
            t,
            phase=phase,
            ready=t.id in ready_ids,
            workflow=workflow,
        )
        projection_reconciled = False
        display_status = t.status
        if (
            completed_closeout is not None
            and str(t.status or "").strip().lower() not in _TERMINAL_TASK_STATUSES
        ):
            projection_reconciled = True
            display_status = "done"
            kanban_display = {
                "column": "done",
                "label": kanban_column_label("done"),
                "reason": "run_completed_projection_reconciliation",
                "badges": list(dict.fromkeys([
                    *kanban_display.get("badges", []),
                    "run_completed",
                ])),
            }
        out.append({
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "display_status": display_status,
            "projection_reconciled": projection_reconciled,
            "projection_reconcile_reason": (
                "run_completed"
                if projection_reconciled else ""
            ),
            "projection_reconcile_event_id": (
                completed_closeout.id
                if projection_reconciled and completed_closeout is not None
                else ""
            ),
            "kanban_column": kanban_display["column"],
            "kanban_column_label": kanban_display["label"],
            "kanban_column_reason": kanban_display["reason"],
            "kanban_column_badges": kanban_display["badges"],
            "workflow_phase": workflow["workflow_phase"],
            "impl_exit_gate_state": workflow["impl_exit_gate_state"],
            "verify_state": workflow["verify_state"],
            "judge_state": workflow["judge_state"],
            "verify_lanes": workflow["verify_lanes"],
            "workflow_badges": workflow["badges"],
            "workflow_projection": workflow,
            "source": source,
            "priority": getattr(t, "priority", 3),
            "assigned_to": t.assigned_to or "",
            "retry_count": t.retry_count,
            "blocked_reason": t.blocked_reason,
            "phase": phase,
            "created_at": t.created_at,
            "blocked_by": list(t.blocked_by),
            "ready": t.id in ready_ids,
            "skills_required": list(t.skills_required),
            "links": {
                "trace": _first_nonempty([
                    getattr(event, "correlation_id", None)
                    for _, event in reversed(task_events)
                ]) or "",
                "candidate": candidate_ref,
                "fanout": refs.get("fanout_id") or "",
                "fanout_child": refs.get("child_id") or "",
                "fanout_run": refs.get("run_id") or "",
            },
            "fanout": fanout_projection,
            "git": {
                key: refs.get(key, "")
                for key in (
                    "source_commit",
                    "task_ref",
                    "worker_branch",
                    "candidate_ref",
                    "candidate_branch",
                    "pdd_id",
                    "fanout_id",
                )
                if refs.get(key, "")
            },
            "latest_event": latest,
            "evidence_badges": evidence_badges,
            "route_summary": route_summary,
            "work_unit": why_not_done.get("work_unit"),
            "why_not_done": why_not_done.get("why_not_done", []),
            "recommended_action": why_not_done.get("recommended_action", {}),
            "next_required_event": why_not_done.get("next_required_event", ""),
            "freshness": why_not_done.get("freshness", {}),
            "workpad": workpad,
            "retry_metadata": retry_metadata,
        })
    return out


def _current_completed_run_closeout_for_projection(
    events: list[tuple[int, ZfEvent]],
) -> ZfEvent | None:
    completed_index = -1
    completed_event: ZfEvent | None = None
    for index, (_, event) in enumerate(events):
        if event.type != "run.completed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        status = str(
            payload.get("status")
            or payload.get("completion_status")
            or ""
        ).strip()
        if status in {"", "passed", "complete", "completed"}:
            completed_index = index
            completed_event = event
    if completed_event is None:
        return None
    for _, event in events[completed_index + 1:]:
        if event.type in _RUN_COMPLETED_PROJECTION_RESET_EVENTS:
            return None
    return completed_event


def _kanban_column(
    task: Task,
    *,
    phase: str | None = None,
    ready: bool = False,
) -> str:
    """Project runtime task state to the Web Kanban display column.

    ``Task.status`` remains the durable business state. During normal
    harness handoff it commonly stays ``in_progress`` while phase and
    assignee move through review/test/judge, so the Web board needs a
    separate read-only display column.
    """
    return kanban_column_projection(task, phase=phase, ready=ready).column


def _task_source_from_events(task_events: list[tuple[int, ZfEvent]]) -> str:
    for _, event in task_events:
        if getattr(event, "type", "") != "task.created":
            continue
        payload = getattr(event, "payload", {}) or {}
        if not isinstance(payload, dict):
            continue
        request = payload.get("request")
        if isinstance(request, dict):
            source = str(request.get("source") or "").strip()
            if source:
                return source
        source = str(payload.get("source") or "").strip()
        if source:
            return source
    return ""


def _task_evidence_badges(
    *,
    state_dir: Path,
    ready: bool,
    refs: dict,
    latest: dict | None,
) -> list[dict]:
    badges = []
    if ready:
        badges.append({"kind": "todo", "label": "todo", "tone": "ok"})
    if refs.get("source_commit") or refs.get("task_ref"):
        badges.append({"kind": "code", "label": "code", "tone": "info"})
    if refs.get("candidate_ref") or refs.get("candidate_branch") or refs.get("pdd_id"):
        badges.append({"kind": "candidate", "label": "candidate", "tone": "info"})
    if refs.get("fanout_id"):
        fanout_id = str(refs.get("fanout_id") or "")
        manifest = _fanout_manifest(state_dir, fanout_id)
        children = [
            _fanout_child_projection(child)
            for child in manifest.get("children", []) or []
            if isinstance(child, dict)
        ]
        progress = _fanout_progress(children)
        label = (
            f"fanout {progress['done']}/{progress['total']}"
            if progress["total"] else "fanout"
        )
        badges.append({"kind": "fanout", "label": label, "tone": "warn"})
    if latest and str(latest.get("type") or "").endswith((".failed", ".rejected")):
        badges.append({"kind": "failed", "label": "failed", "tone": "err"})
    return badges


def _task_fanout_projection(
    state_dir: Path,
    refs: dict,
    latest_fanout: dict[str, Any] | None = None,
) -> dict:
    projection = {
        key: refs.get(key, "")
        for key in (
            "fanout_id",
            "child_id",
            "run_id",
            "workdir",
            "source_branch",
            "task_map_ref",
            "source_index_ref",
            "lane_id",
            "affinity_tag",
            "assignment_strategy",
        )
        if refs.get(key, "")
    }
    if latest_fanout:
        projection.update({
            key: latest_fanout.get(key, "")
            for key in (
                "fanout_id",
                "child_id",
                "run_id",
                "workdir",
                "source_branch",
                "source_commit",
                "task_ref",
                "lane_id",
                "affinity_tag",
                "assignment_strategy",
                "role_instance",
                "stage_id",
                "fanout_status",
                "child_status",
            )
            if latest_fanout.get(key, "")
        })
        child = latest_fanout.get("child")
        if isinstance(child, dict):
            projection["current_child"] = child
        progress = latest_fanout.get("progress")
        if isinstance(progress, dict) and progress.get("total"):
            projection["progress"] = progress
        projection["current"] = True
    fanout_id = str(refs.get("fanout_id") or "")
    if not fanout_id:
        return projection
    manifest = _fanout_manifest(state_dir, fanout_id)
    children = [
        _fanout_child_projection(child)
        for child in manifest.get("children", []) or []
        if isinstance(child, dict)
    ]
    progress = _fanout_progress(children)
    if progress.get("total"):
        projection["progress"] = progress
    return projection


def _task_runs(state_dir: Path, *, project_root: Path, task_id: str) -> dict:
    runs = redact_obj(read_task_runs(
        project_root=project_root,
        state_dir=state_dir,
        task_id=task_id,
    ))
    fallback = _event_log_run_summary(state_dir)
    if (
        fallback is not None
        and task_id in set(fallback.get("summary", {}).get("task_ids", []) or [])
        and not any(str(item.get("run_id") or "") == _EVENT_LOG_RUN_ID for item in runs)
    ):
        runs.append(redact_obj(fallback))
    return {
        "task_id": task_id,
        "runs": runs,
    }


def _task_id_from_payload(payload: dict) -> str | None:
    task_id = str(payload.get("task_id") or "")
    return task_id or None


def _task_contract_from_payload(value: object) -> TaskContract:
    if not isinstance(value, dict):
        return TaskContract()
    return TaskContract(
        behavior=str(value.get("behavior") or ""),
        verification=str(value.get("verification") or ""),
        verification_tiers=_string_list(value.get("verification_tiers")),
        validation=(
            value.get("validation") if isinstance(value.get("validation"), dict) else {}
        ),
        scope=_string_list(value.get("scope")),
        exclusions=_string_list(value.get("exclusions")),
        acceptance=str(value.get("acceptance") or "exit_code=0"),
        rework_to=str(value.get("rework_to") or ""),
    )


def _task_updates_from_payload(task: Task, payload: dict) -> dict:
    updates: dict[str, Any] = {}
    if "status" in payload:
        status = str(payload.get("status") or "").strip()
        if status in {
            "backlog",
            "ready",
            "todo",
            "in_progress",
            "review",
            "testing",
            "blocked",
            "done",
            "cancelled",
        }:
            updates["status"] = "backlog" if status in {"ready", "todo"} else status
    for key in ("title", "blocked_reason"):
        if key in payload:
            updates[key] = str(payload.get(key) or "")
    if "priority" in payload:
        updates["priority"] = _task_priority(payload.get("priority"))
    if "assigned_to" in payload or "owner" in payload:
        updates["assigned_to"] = _optional_str(payload.get("assigned_to") or payload.get("owner"))
    if "skills_required" in payload or "skills" in payload:
        updates["skills_required"] = _string_list(payload.get("skills_required") or payload.get("skills"))
    if "blocked_by" in payload:
        updates["blocked_by"] = _string_list(payload.get("blocked_by"))
    if isinstance(payload.get("contract"), dict):
        current = asdict(task.contract)
        current.update(payload["contract"])
        updates["contract"] = _task_contract_from_payload(current)
    if isinstance(payload.get("evidence"), dict):
        evidence = _task_evidence_from_payload(task, payload["evidence"])
        if evidence is not None:
            updates["evidence"] = evidence
    return updates


def _task_metadata_payload(payload: dict) -> dict:
    return {
        key: payload.get(key)
        for key in ("labels", "notes", "description", "pdd_id", "tdd_id", "trace_id", "fanout_id", "run_id")
        if key in payload
    }


def _task_priority(value: object) -> int:
    try:
        priority = int(value)
    except (TypeError, ValueError):
        priority = 3
    return max(0, min(5, priority))


def _task_evidence_from_payload(task: Task, value: object) -> TaskEvidence | None:
    if not isinstance(value, dict):
        return None
    current = asdict(task.evidence) if task.evidence is not None else asdict(TaskEvidence())
    changed = False
    for key in ("commit", "output_summary", "verified_at"):
        if key in value:
            current[key] = str(value.get(key) or "")
            changed = True
    if "files_touched" in value:
        current["files_touched"] = _string_list(value.get("files_touched"))
        changed = True
    if "commits" in value:
        current["commits"] = _string_list(value.get("commits"))
        changed = True
    return TaskEvidence(**current) if changed else None


def _ship_blockers(
    state_dir: Path,
    payload: dict,
    config: ZfConfig | None = None,
) -> list[str]:
    candidate_id = _first_nonempty([
        payload.get("candidate_ref"),
        payload.get("candidate_id"),
        payload.get("pdd_id"),
    ])
    task_id = str(payload.get("task_id") or "")
    if not candidate_id and task_id:
        detail = _task_detail(state_dir, task_id, config=config)
        links = detail.get("links", {}) if isinstance(detail, dict) else {}
        git = detail.get("git", {}) if isinstance(detail, dict) else {}
        candidate_id = _first_nonempty([
            links.get("candidate") if isinstance(links, dict) else None,
            git.get("pdd_id") if isinstance(git, dict) else None,
            git.get("candidate_ref") if isinstance(git, dict) else None,
            git.get("candidate_branch") if isinstance(git, dict) else None,
        ])

    blockers: list[str] = []
    if not candidate_id:
        blockers.append("candidate_ref or pdd_id required")
        return blockers

    candidate = _candidate_detail(state_dir, str(candidate_id), config=config)
    if candidate.get("empty"):
        blockers.append(f"candidate projection {candidate_id!r} not found")
    blockers.extend(str(blocker) for blocker in candidate.get("blockers", []) or [])
    if not candidate.get("ship_ready"):
        blockers.append("candidate is not ship_ready")
    return sorted(set(blockers))


def _task_events_with_seq(
    state_dir: Path,
    task_id: str,
    config: ZfConfig | None = None,
    *,
    include_payload_mentions: bool = True,
    limit: int | None = None,
) -> list[tuple[int, object]]:
    if not task_id:
        return []
    if not include_payload_mentions:
        events = _events_with_exact_task_id(state_dir, task_id, config=config)
    else:
        events = [
            (seq, event)
            for seq, event in _events_with_seq(state_dir, config=config)
            if getattr(event, "task_id", None) == task_id
            or _payload_mentions(getattr(event, "payload", {}), task_id)
        ]
    if limit is not None and limit > 0:
        return events[-limit:]
    return events


def _task_briefing(state_dir: Path, task_id: str) -> dict:
    root = state_dir / "briefings"
    if not root.exists():
        return {"path": "", "text": "", "truncated": False}
    matches = sorted(path for path in root.glob("*.md") if task_id in path.name)
    if not matches:
        return {"path": "", "text": "", "truncated": False}
    text = matches[0].read_text(encoding="utf-8")
    truncated = len(text) > 24_000
    if truncated:
        text = text[:24_000]
    return {
        "path": str(matches[0]),
        "text": redact_obj(text),
        "truncated": truncated,
    }


def _task_index_with_archive(state_dir: Path) -> dict[str, object]:
    path = state_dir / "kanban.json"
    if not path.exists():
        return {}
    try:
        return {
            task.id: task
            for task in TaskStore(path).list_all_with_archive(last_days=None)
        }
    except Exception:
        return {}


def _task_views(state_dir: Path) -> list[TaskView]:
    """Reuse Feishu's TaskView shape."""
    path = state_dir / "kanban.json"
    if not path.exists():
        return []
    ts = TaskStore(path)
    out: list[TaskView] = []
    for t in ts.list_all():
        out.append(TaskView(
            task_id=t.id,
            title=t.title,
            status=t.status,
            assigned_to=t.assigned_to or "",
            blocked_reason=t.blocked_reason,
        ))
    return out
