"""Candidate-level rework controlled-action handlers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from zf.core.config.schema import ZfConfig
from zf.core.events import EventLog, ZfEvent


class CandidateReworkActionsMixin:
    def _candidate_rework_apply(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        rework_action = str(payload.get("candidate_rework_action") or "").strip()
        if rework_action not in {"retrigger", "replan", "escalate"}:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="candidate_rework_action must be retrigger, replan, or escalate",
                status_code=422,
                status="rejected",
            )
        if not str(payload.get("pdd_id") or "").strip():
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="pdd_id is required",
                status_code=422,
                status="rejected",
            )
        if not str(payload.get("source_event_id") or "").strip():
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="source_event_id is required",
                status_code=422,
                status="rejected",
            )

        if rework_action == "retrigger":
            result = self._candidate_rework_retrigger(payload, requested=requested)
        elif rework_action == "replan":
            result = self._candidate_rework_replan(payload, requested=requested)
        else:
            result = self._candidate_rework_escalate(payload, requested=requested)

        event = self.writer.emit(
            "workflow.resume.control_action.result",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "schema_version": "candidate-rework.control-action-result.v1",
                "action": action,
                "candidate_rework_action": rework_action,
                "checkpoint_id": str(payload.get("checkpoint_id") or ""),
                "source_event_id": str(payload.get("source_event_id") or ""),
                "pdd_id": str(payload.get("pdd_id") or ""),
                "status": result["status"],
                "emitted_event_ids": list(result["emitted_event_ids"]),
                "reason": result["reason"],
            },
        )
        emitted = [*result["emitted_event_ids"], event.id]
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status=result["status"],
            task_id=None,
            extra={
                "checkpoint_id": str(payload.get("checkpoint_id") or ""),
                "candidate_rework_action": rework_action,
            },
        )
        return {
            "ok": result["status"] == "applied",
            "status": result["status"],
            "action": action,
            "requested_action": requested_action,
            "event_id": event.id,
            "checkpoint_id": str(payload.get("checkpoint_id") or ""),
            "candidate_rework_action": rework_action,
            "emitted_event_ids": emitted,
            "reason": result["reason"],
        }

    def _candidate_rework_retrigger(
        self,
        payload: dict[str, Any],
        *,
        requested: ZfEvent,
    ) -> dict[str, Any]:
        missing = [
            name for name in ("task_map_ref", "source_commit", "candidate_base_commit")
            if not str(payload.get(name) or "").strip()
        ]
        if missing:
            return {
                "status": "rejected",
                "reason": "missing " + ", ".join(missing),
                "emitted_event_ids": [],
            }
        rework_summary = (
            payload.get("rework_summary")
            if isinstance(payload.get("rework_summary"), dict)
            else {}
        )
        if str(payload.get("candidate_retry_mode") or "") == "integration_only":
            source_event_id = str(payload.get("source_event_id") or "")
            fanout_id = str(payload.get("fanout_id") or "")
            if not fanout_id:
                return {
                    "status": "rejected",
                    "reason": "integration-only candidate retry requires fanout_id",
                    "emitted_event_ids": [],
                }
            event = self.writer.emit(
                "workflow.resume.applied",
                actor="zf-cli",
                causation_id=source_event_id or requested.id,
                correlation_id=str(payload.get("trace_id") or "") or requested.correlation_id,
                payload={
                    "task_id": str(payload.get("pdd_id") or ""),
                    "safe_resume_action": "retry_candidate_integration",
                    "expected_next_stage": "candidate-integration",
                    "source_event_id": source_event_id,
                    "blocking_event_id": source_event_id,
                    "rework_of": source_event_id,
                    "rework_attempt": int(payload.get("rework_attempt") or 0),
                    "rework_source": str(payload.get("source_event_type") or ""),
                    "fanout_id": fanout_id,
                    "pdd_id": str(payload.get("pdd_id") or ""),
                    "feature_id": str(payload.get("feature_id") or ""),
                    "checkpoint_id": str(payload.get("checkpoint_id") or ""),
                    "integration_attempt_id": str(payload.get("integration_attempt_id") or ""),
                    "mode": "candidate_rework_integration_only",
                },
            )
            return {
                "status": "applied",
                "reason": "candidate integration retry requested",
                "emitted_event_ids": [event.id],
            }
        task_map_ref = str(payload.get("task_map_ref") or "")
        emitted: list[str] = []
        gap_task_ids: list[str] = []
        if rework_summary.get("gap_tasks"):
            try:
                amend = _write_gap_task_map_amend(
                    payload,
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                )
            except (OSError, ValueError) as exc:
                return {
                    "status": "rejected",
                    "reason": f"gap task_map amend failed: {exc}",
                    "emitted_event_ids": [],
                }
            task_map_ref = amend["task_map_ref"]
            gap_task_ids = list(amend["gap_task_ids"])
            amended = self.writer.emit(
                "task_map.amended",
                actor="zf-cli",
                causation_id=str(payload.get("source_event_id") or "") or requested.id,
                correlation_id=str(payload.get("trace_id") or "") or requested.correlation_id,
                payload={
                    "schema_version": "task-map-amended.v1",
                    "pdd_id": str(payload.get("pdd_id") or ""),
                    "feature_id": str(payload.get("feature_id") or ""),
                    "trace_id": str(payload.get("trace_id") or ""),
                    "task_map_ref": task_map_ref,
                    "supersedes_task_map_ref": str(payload.get("task_map_ref") or ""),
                    "gap_task_ids": gap_task_ids,
                    "gap_task_count": len(gap_task_ids),
                    "source_event_id": str(payload.get("source_event_id") or ""),
                    "source": "run_manager_gap_task_map_amend",
                },
            )
            emitted.append(amended.id)
        event_payload: dict[str, Any] = {
            "pdd_id": str(payload.get("pdd_id") or ""),
            "trace_id": str(payload.get("trace_id") or ""),
            "source_commit": str(payload.get("source_commit") or ""),
            "candidate_base_commit": str(payload.get("candidate_base_commit") or ""),
            "target_ref": str(payload.get("target_ref") or ""),
            "feature_id": str(payload.get("feature_id") or ""),
            "source_index_ref": str(payload.get("source_index_ref") or ""),
            "task_map_ref": task_map_ref,
            "rework_of": str(payload.get("source_event_id") or ""),
            "rework_attempt": int(payload.get("rework_attempt") or 0),
            "rework_source": str(payload.get("source_event_type") or ""),
            "rework_feedback": _string_list(payload.get("rework_feedback")),
            "rework_categories": _string_list(payload.get("rework_categories")),
            "rework_summary": rework_summary,
            "source": (
                "run_manager_gap_task_map_amend"
                if gap_task_ids else "run_manager_candidate_rework"
            ),
            "resume_checkpoint_ref": str(payload.get("checkpoint_id") or ""),
            "idempotency_key": str(payload.get("checkpoint_id") or ""),
        }
        failed_task_ids = _string_list(payload.get("failed_task_ids"))
        task_ids = gap_task_ids or failed_task_ids
        if failed_task_ids and not gap_task_ids:
            from zf.core.task.store import TaskStore
            from zf.runtime.rework_task_scope import expand_rework_task_ids

            completed_task_ids = {
                task.id
                for task in TaskStore(self.state_dir / "kanban.json").list_all()
                if task.status in {"done", "cancelled", "superseded"}
            }
            task_ids = expand_rework_task_ids(
                failed_task_ids,
                task_map_ref=task_map_ref,
                state_dir=self.state_dir,
                project_root=self.project_root or self.state_dir.parent,
                completed_task_ids=completed_task_ids,
            )
        if task_ids:
            event_payload["task_ids"] = task_ids
            if gap_task_ids:
                event_payload["resume_scope"] = "gap_tasks_only"
            elif task_ids == failed_task_ids:
                event_payload["resume_scope"] = "failed_children_only"
            else:
                event_payload["resume_scope"] = "failed_children_and_downstream"
                event_payload["failed_task_ids"] = failed_task_ids
                event_payload["downstream_task_ids"] = [
                    task_id for task_id in task_ids
                    if task_id not in set(failed_task_ids)
                ]
        if gap_task_ids:
            event_payload["amend_of"] = str(payload.get("task_map_ref") or "")
            event_payload["gap_task_ids"] = gap_task_ids
        event = self.writer.emit(
            "task_map.ready",
            actor="zf-cli",
            causation_id=str(payload.get("source_event_id") or "") or requested.id,
            correlation_id=str(payload.get("trace_id") or "") or requested.correlation_id,
            payload=event_payload,
        )
        emitted.append(event.id)
        return {
            "status": "applied",
            "reason": (
                "candidate rework gap task_map amended and emitted"
                if gap_task_ids else "candidate rework task_map.ready emitted"
            ),
            "emitted_event_ids": emitted,
        }

    def _candidate_rework_replan(
        self,
        payload: dict[str, Any],
        *,
        requested: ZfEvent,
    ) -> dict[str, Any]:
        scope_payload = _replan_task_scope_payload(
            payload,
            state_dir=self.state_dir,
            project_root=self.project_root,
        )
        event_payload = {
            "pdd_id": str(payload.get("pdd_id") or ""),
            "trace_id": str(payload.get("trace_id") or ""),
            "target_ref": str(payload.get("target_ref") or ""),
            "source_commit": str(payload.get("source_commit") or ""),
            "candidate_base_commit": str(payload.get("candidate_base_commit") or ""),
            "rework_of": str(payload.get("source_event_id") or ""),
            "rework_attempt": int(payload.get("rework_attempt") or 0),
            "rework_source": str(payload.get("source_event_type") or ""),
            "classification": str(payload.get("classification") or ""),
            "rework_feedback": _string_list(payload.get("rework_feedback")),
            "rework_categories": _string_list(payload.get("rework_categories")),
            "rework_summary": payload.get("rework_summary")
            if isinstance(payload.get("rework_summary"), dict) else {},
            "reason": (
                f"plan-level failure ({payload.get('classification') or ''}) from "
                f"{payload.get('source_event_type') or ''}; re-decompose the task_map "
                f"(do NOT re-implement the same slices)"
            ),
            "resume_checkpoint_ref": str(payload.get("checkpoint_id") or ""),
            "idempotency_key": str(payload.get("checkpoint_id") or ""),
            **scope_payload,
        }
        replan = self.writer.emit(
            "orchestrator.replan_requested",
            actor="zf-cli",
            causation_id=str(payload.get("source_event_id") or "") or requested.id,
            correlation_id=str(payload.get("trace_id") or "") or requested.correlation_id,
            payload=event_payload,
        )
        emitted = [replan.id]
        resynth = _build_resynth_event(
            {**payload, **scope_payload},
            state_dir=self.state_dir,
            config=self.config or ZfConfig(),
        )
        if resynth is not None:
            emitted.append(self.writer.append(resynth).id)
        return {
            "status": "applied",
            "reason": "candidate replan requested",
            "emitted_event_ids": emitted,
        }

    def _candidate_rework_escalate(
        self,
        payload: dict[str, Any],
        *,
        requested: ZfEvent,
    ) -> dict[str, Any]:
        if not bool(payload.get("orchestrator_triage_applied")):
            return self._candidate_rework_request_triage(
                payload,
                requested=requested,
            )
        findings = "; ".join(_string_list(payload.get("rework_feedback"))) or "(no findings captured)"
        attempt = int(payload.get("rework_attempt") or 0)
        reason = (
            f"candidate rework exhausted after {max(attempt - 1, 0)} attempts; "
            f"reviewer findings unresolved"
        )
        common = {
            "pdd_id": str(payload.get("pdd_id") or ""),
            "trace_id": str(payload.get("trace_id") or ""),
            "reason": reason,
            "rework_of": str(payload.get("source_event_id") or ""),
            "rework_attempt": attempt,
            "rework_source": str(payload.get("source_event_type") or ""),
            "rework_feedback": _string_list(payload.get("rework_feedback")),
            "rework_categories": _string_list(payload.get("rework_categories")),
            "rework_summary": payload.get("rework_summary")
            if isinstance(payload.get("rework_summary"), dict) else {},
            "resume_checkpoint_ref": str(payload.get("checkpoint_id") or ""),
            "idempotency_key": str(payload.get("checkpoint_id") or ""),
        }
        escalation = self.writer.emit(
            "human.escalate",
            actor="zf-cli",
            causation_id=str(payload.get("source_event_id") or "") or requested.id,
            correlation_id=str(payload.get("trace_id") or "") or requested.correlation_id,
            payload=common,
        )
        owner = self.writer.emit(
            "owner.visible_message.requested",
            actor="zf-cli",
            causation_id=escalation.id,
            correlation_id=str(payload.get("trace_id") or "") or requested.correlation_id,
            payload={
                "message_id": (
                    f"rework-exhausted-{payload.get('pdd_id')}-"
                    f"{payload.get('source_event_id')}-{attempt}"
                ),
                "severity": "high",
                "title": "Candidate rework exhausted - operator decision needed",
                "summary": (
                    f"pdd {payload.get('pdd_id')}: {max(attempt - 1, 0)} rework "
                    f"attempts, reviewer findings still unresolved. Findings: {findings}"
                ),
                "route": "owner",
                **common,
            },
        )
        emitted = [escalation.id, owner.id]
        emitted.extend(_write_quarantine_backlog(payload, self.project_root))
        return {
            "status": "applied",
            "reason": reason,
            "emitted_event_ids": emitted,
        }

    def _candidate_rework_request_triage(
        self,
        payload: dict[str, Any],
        *,
        requested: ZfEvent,
    ) -> dict[str, Any]:
        attempt = int(payload.get("rework_attempt") or 0)
        failure_count = max(attempt, 1)
        source_event_ids = _string_list(payload.get("source_event_ids"))
        source_event_id = str(payload.get("source_event_id") or "")
        if source_event_id and source_event_id not in source_event_ids:
            source_event_ids.append(source_event_id)
        fingerprint = str(
            payload.get("fingerprint")
            or payload.get("failure_fingerprint")
            or payload.get("checkpoint_id")
            or source_event_id
        )
        cap = self.writer.emit(
            "candidate.rework.capped",
            actor="run-manager",
            causation_id=source_event_id or requested.id,
            correlation_id=str(payload.get("trace_id") or "") or requested.correlation_id,
            payload={
                "schema_version": "candidate-rework-cap.v1",
                "pdd_id": str(payload.get("pdd_id") or ""),
                "feature_id": str(payload.get("feature_id") or ""),
                "trace_id": str(payload.get("trace_id") or ""),
                "failure_scope": "candidate",
                "failure_fingerprint": fingerprint,
                "failure_count": failure_count,
                "retry_count": failure_count,
                "failure_event_ids": source_event_ids,
                "trigger_event_id": source_event_id,
                "trigger_event_type": str(payload.get("source_event_type") or ""),
                "last_reason": "candidate rework cap reached; semantic triage required",
                "semantic_triage_required": True,
                "candidate_rework_context": _candidate_triage_context(payload),
            },
        )
        return {
            "status": "applied",
            "reason": "candidate rework cap recorded for semantic triage",
            "emitted_event_ids": [cap.id],
        }


def _candidate_triage_context(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "pdd_id",
        "feature_id",
        "trace_id",
        "target_ref",
        "fanout_id",
        "integration_attempt_id",
        "source_commit",
        "candidate_base_commit",
        "source_index_ref",
        "task_map_ref",
        "source_event_id",
        "source_event_type",
        "rework_attempt",
        "rework_feedback",
        "failed_task_ids",
        "classification",
        "rework_categories",
        "rework_summary",
    )
    return {
        key: payload.get(key)
        for key in keys
        if payload.get(key) not in (None, "", [], {})
    }


def _build_resynth_event(
    payload: dict[str, Any],
    *,
    state_dir,
    config: ZfConfig,
) -> ZfEvent | None:
    try:
        from zf.runtime.event_window import read_runtime_events
        from zf.runtime.replan_resynth import build_replan_resynth_event

        events = read_runtime_events(EventLog(state_dir / "events.jsonl"), state_dir)
        plan = SimpleNamespace(
            pdd_id=str(payload.get("pdd_id") or ""),
            trace_id=str(payload.get("trace_id") or ""),
            target_ref=str(payload.get("target_ref") or ""),
            source_event_id=str(payload.get("source_event_id") or ""),
            attempt=int(payload.get("rework_attempt") or 0),
            source_event_type=str(payload.get("source_event_type") or ""),
            feedback=tuple(_string_list(payload.get("rework_feedback"))),
            failure_categories=tuple(_string_list(payload.get("rework_categories"))),
            rework_summary=payload.get("rework_summary")
            if isinstance(payload.get("rework_summary"), dict) else {},
            classification=str(payload.get("classification") or ""),
            failed_task_ids=tuple(_string_list(payload.get("failed_task_ids"))),
            task_ids=tuple(_string_list(payload.get("task_ids"))),
            downstream_task_ids=tuple(
                _string_list(payload.get("downstream_task_ids"))
            ),
            resume_scope=str(payload.get("resume_scope") or ""),
        )
        return build_replan_resynth_event(plan=plan, events=events, config=config)
    except Exception:
        return None


def _replan_task_scope_payload(
    payload: dict[str, Any],
    *,
    state_dir: Path,
    project_root: Path | None,
) -> dict[str, Any]:
    failed_task_ids = _string_list(payload.get("failed_task_ids"))
    if not failed_task_ids:
        return {}

    from zf.core.task.store import TaskStore
    from zf.runtime.rework_task_scope import expand_rework_task_ids

    completed_task_ids = {
        task.id
        for task in TaskStore(state_dir / "kanban.json").list_all()
        if task.status in {"done", "cancelled", "superseded"}
    }
    task_ids = expand_rework_task_ids(
        failed_task_ids,
        task_map_ref=str(payload.get("task_map_ref") or ""),
        state_dir=state_dir,
        project_root=project_root or state_dir.parent,
        completed_task_ids=completed_task_ids,
    )
    scope: dict[str, Any] = {
        "failed_task_ids": failed_task_ids,
        "task_ids": task_ids,
        "resume_scope": (
            "failed_children_only"
            if task_ids == failed_task_ids
            else "failed_children_and_downstream"
        ),
    }
    downstream = [
        task_id for task_id in task_ids if task_id not in set(failed_task_ids)
    ]
    if downstream:
        scope["downstream_task_ids"] = downstream
    return scope


def _write_gap_task_map_amend(
    payload: dict[str, Any],
    *,
    state_dir: Path,
    project_root: Path | None,
) -> dict[str, Any]:
    from zf.runtime.module_gap_plan import (
        gap_tasks_from_rework_summary,
        write_gap_task_map_amend_artifact,
    )

    rework_summary = (
        payload.get("rework_summary")
        if isinstance(payload.get("rework_summary"), dict)
        else {}
    )
    gap_tasks = gap_tasks_from_rework_summary(rework_summary)
    if not gap_tasks:
        raise ValueError("rework_summary.gap_tasks is empty")
    task_map_ref = str(payload.get("task_map_ref") or "").strip()
    checkpoint_id = str(payload.get("checkpoint_id") or "").strip()
    return write_gap_task_map_amend_artifact(
        state_dir=state_dir,
        project_root=project_root,
        base_task_map_ref=task_map_ref,
        pdd_id=str(payload.get("pdd_id") or "unknown"),
        source_event_id=checkpoint_id or str(payload.get("source_event_id") or "event"),
        gap_tasks=gap_tasks,
        gap_plan_ref=str(rework_summary.get("gap_plan_ref") or ""),
    )


def _write_quarantine_backlog(payload: dict[str, Any], project_root) -> list[str]:
    if project_root is None:
        return []
    try:
        from zf.autoresearch.bug_candidates import write_candidate_backlogs
        from zf.runtime.candidate_rework import quarantine_candidate_from_plan

        plan = SimpleNamespace(
            pdd_id=str(payload.get("pdd_id") or ""),
            trace_id=str(payload.get("trace_id") or ""),
            source_event_type=str(payload.get("source_event_type") or ""),
            source_event_id=str(payload.get("source_event_id") or ""),
            attempt=int(payload.get("rework_attempt") or 0),
            feedback=tuple(_string_list(payload.get("rework_feedback"))),
        )
        candidate = quarantine_candidate_from_plan(plan)
        out = []
        for export in write_candidate_backlogs([candidate], out_dir=project_root / "backlogs"):
            if export.created:
                out.append(str(export.path))
        return out
    except Exception:
        return []


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


__all__ = ["CandidateReworkActionsMixin"]
