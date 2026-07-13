"""Dispatch-time recovery policies kept outside the frozen dispatch module."""

from __future__ import annotations

from zf.core.config.schema import RoleConfig
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.runtime.attempt_ledger import counted_failure_events, failure_fingerprint
from zf.runtime.event_window import read_runtime_events


class DispatchRecoveryPolicyMixin:
    """Skills admission and repeated-failure policy for ``DispatchMixin``."""

    def _emit_skills_required_unmatched(
        self,
        task: Task,
        *,
        role: RoleConfig | None,
    ) -> None:
        gap = self._skills_required_gap(task)
        if gap is None:
            return
        self._emit_dispatch_skipped(
            task=task,
            role=role,
            reason="skills_required_unmatched",
        )
        emitted = getattr(self, "_skills_required_unmatched_emitted", None)
        if emitted is None:
            emitted = set()
            self._skills_required_unmatched_emitted = emitted
        key = (
            task.id,
            tuple(gap["required_skills"]),
            str(gap["target_role"]),
        )
        if key in emitted:
            return
        emitted.add(key)
        self.event_writer.append(ZfEvent(
            type="dispatch.skills_unmatched",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "task_id": task.id,
                "reason": "no intended role covers task.skills_required",
                **gap,
                "recovery_owner": "run_manager",
            },
        ))

    def _emit_rework_capped(
        self,
        task: Task,
        role: RoleConfig,
        trigger_event: ZfEvent,
        *,
        max_attempts: int | None = None,
        max_attempts_source: str = "role",
    ) -> None:
        """Emit a cap fact for Run Manager; do not create a second owner."""

        effective_max_attempts = (
            int(max_attempts)
            if max_attempts is not None
            else int(role.max_rework_attempts)
        )
        payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
        try:
            fingerprint = failure_fingerprint(trigger_event)
        except Exception:
            fingerprint = trigger_event.id
        failures: list[ZfEvent] = []
        try:
            failures = counted_failure_events(
                read_runtime_events(self.event_log, self.state_dir),
                task.id,
                fingerprint=fingerprint,
            )
        except Exception:
            failures = []
        strict = getattr(self.config.workflow, "strict_triggers", None)
        semantic_threshold = int(
            getattr(strict, "rework_attempts_gte", 0) or 0
        )
        semantic_required = bool(
            semantic_threshold > 0 and len(failures) >= semantic_threshold
        )
        try:
            self.event_writer.append(ZfEvent(
                type="task.rework.capped",
                actor="zf-cli",
                task_id=task.id,
                payload={
                    "role": role.name,
                    "retry_count": task.retry_count,
                    "max_attempts": effective_max_attempts,
                    "max_attempts_source": max_attempts_source,
                    "last_reason": str(payload.get("reason") or trigger_event.type),
                    "trigger_event_type": trigger_event.type,
                    "trigger_event_id": trigger_event.id,
                    "failure_fingerprint": fingerprint,
                    "failure_count": len(failures),
                    "failure_event_ids": [event.id for event in failures],
                    "semantic_triage_required": semantic_required,
                    "recovery_owner": "run_manager",
                },
                causation_id=trigger_event.id,
                correlation_id=trigger_event.correlation_id,
            ))
        except Exception:
            pass

    def _semantic_rework_triage_required(
        self,
        task: Task,
        role: RoleConfig,
        trigger_event: ZfEvent,
        *,
        events: list[ZfEvent],
    ) -> bool:
        """Stop blind re-dispatch at the configured same-fingerprint limit."""

        strict = getattr(self.config.workflow, "strict_triggers", None)
        threshold = int(getattr(strict, "rework_attempts_gte", 0) or 0)
        if threshold <= 0:
            return False
        try:
            if not events:
                events = read_runtime_events(self.event_log, self.state_dir)
            fingerprint = failure_fingerprint(trigger_event)
            failures = counted_failure_events(
                events,
                task.id,
                fingerprint=fingerprint,
            )
        except Exception:
            return False
        if len(failures) < threshold:
            return False
        for event in events:
            if event.type != "task.rework.capped" or event.task_id != task.id:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if (
                bool(payload.get("semantic_triage_required"))
                and str(payload.get("failure_fingerprint") or "") == fingerprint
            ):
                return True
        self.event_writer.append(ZfEvent(
            type="task.rework.capped",
            actor="zf-cli",
            task_id=task.id,
            payload={
                "role": role.name,
                "retry_count": task.retry_count,
                "max_attempts": role.max_rework_attempts,
                "max_attempts_source": "semantic_triage_threshold",
                "last_reason": str(
                    (trigger_event.payload or {}).get("reason")
                    if isinstance(trigger_event.payload, dict)
                    else trigger_event.type
                ),
                "trigger_event_type": trigger_event.type,
                "trigger_event_id": trigger_event.id,
                "failure_fingerprint": fingerprint,
                "failure_count": len(failures),
                "failure_event_ids": [event.id for event in failures],
                "semantic_triage_required": True,
                "recovery_owner": "run_manager",
            },
            causation_id=trigger_event.id,
            correlation_id=trigger_event.correlation_id,
        ))
        return True


__all__ = ["DispatchRecoveryPolicyMixin"]
