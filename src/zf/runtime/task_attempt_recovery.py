"""TaskAttempt recovery policy derived from canonical scheduler state.

This module does not mutate kernel truth and does not invent workflow resume
checkpoints. The canonical ``task_attempts.json`` store is authoritative when
present; the historical shadow projection remains a compatibility fallback.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.state.task_attempts import TaskAttemptStore, TaskAttemptStoreError
from zf.runtime.run_manager_router import (
    decide_action_policy,
    expected_downstream_events,
    preflight_action,
)
from zf.runtime.task_pipeline_attempt_recovery import (
    is_retryable_pipeline_provider_stop,
    is_shadow_provider_stop_attempt,
)

TASK_ATTEMPT_RECOVERY_SCHEMA_VERSION = "task-attempt-recovery.v1"


def pending_task_attempt_recovery_actions(
    projections_dir: Path,
    *,
    now: datetime | None = None,
    lease_grace_s: float = 900.0,
    max_retry_attempts: int = 3,
    canonical_store_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Build Run Manager pending actions from current TaskAttempt state.

    The output is intentionally conservative:
    - expired open attempt with an owner -> existing worker lifecycle recovery;
    - retryable failed attempt -> diagnosis/autoresearch, not direct rework;
    - deadlettered/exhausted attempt -> human/safe-halt explanation.
    """

    now = now or datetime.now(timezone.utc)
    lease_grace_s = max(float(lease_grace_s or 0.0), 0.0)
    max_retry_attempts = max(int(max_retry_attempts or 0), 0)
    canonical_store_path = (
        Path(canonical_store_path) if canonical_store_path is not None else None
    )
    if canonical_store_path is not None and canonical_store_path.exists():
        try:
            rows = TaskAttemptStore(canonical_store_path).current_rows()
        except (TaskAttemptStoreError, OSError, ValueError) as exc:
            return [_canonical_store_error_action(canonical_store_path, exc)]
        return _canonical_recovery_actions(
            rows,
            now=now,
            lease_grace_s=lease_grace_s,
            max_retry_attempts=max_retry_attempts,
        )

    data = _load_attempt_projection(Path(projections_dir))
    tasks = data.get("tasks")
    if not isinstance(tasks, dict):
        return []

    actions: list[dict[str, Any]] = []
    for task_id, entry in sorted(tasks.items()):
        if not isinstance(entry, dict):
            continue
        task_id = str(task_id)
        latest = _latest_attempt(entry)
        if not latest:
            continue
        state = str(entry.get("latest_state") or latest.get("state") or "")
        terminal = latest.get("terminal")
        terminal = terminal if isinstance(terminal, dict) else {}
        if state == "running" or (not terminal and str(latest.get("lease_state") or "") == "held"):
            action = _expired_lease_action(
                task_id,
                entry,
                latest,
                now=now,
                lease_grace_s=lease_grace_s,
            )
            if action is not None:
                actions.append(action)
            continue
        if state in {"failed", "deadlettered"}:
            actions.append(
                _failed_attempt_action(
                    task_id,
                    entry,
                    latest,
                    max_retry_attempts=max_retry_attempts,
                )
            )
    return [item for item in actions if item]


def _canonical_recovery_actions(
    rows: list[dict[str, Any]],
    *,
    now: datetime,
    lease_grace_s: float,
    max_retry_attempts: int,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for row in sorted(
        rows,
        key=lambda item: (
            str(item.get("run_id") or ""),
            str(item.get("task_id") or ""),
            str(item.get("attempt_id") or ""),
        ),
    ):
        status = str(row.get("status") or "")
        shadow_interruption = is_shadow_provider_stop_attempt(row)
        provider_interruption = shadow_interruption or (
            status == "expired" and is_retryable_pipeline_provider_stop(row)
        )
        if provider_interruption and (
            max_retry_attempts and int(row.get("ordinal") or 0) >= max_retry_attempts
        ):
            actions.append(_canonical_failed_attempt_action(
                row, max_retry_attempts=max_retry_attempts,
            ))
            continue
        if status in {"prepared", "delivering", "sent", "expired"} or shadow_interruption:
            action = _canonical_expired_lease_action(
                {**row, "status": "expired"} if shadow_interruption else row,
                now=now,
                lease_grace_s=lease_grace_s,
            )
            if action is not None:
                actions.append(action)
            continue
        if status not in {"failed", "deadlettered"}:
            continue
        if str(row.get("recovery_owner") or "") == "workflow":
            continue
        actions.append(_canonical_failed_attempt_action(
            row,
            max_retry_attempts=max_retry_attempts,
        ))
    return actions


def _canonical_expired_lease_action(
    row: dict[str, Any],
    *,
    now: datetime,
    lease_grace_s: float,
) -> dict[str, Any] | None:
    status = str(row.get("status") or "")
    expiry = _parse_ts(str(row.get("lease_expires_at") or ""))
    if status != "expired":
        if expiry is None:
            return _diagnosis_action(
                str(row.get("task_id") or ""),
                row,
                failure_class="task_attempt_lease_missing",
                reason="canonical task attempt has no valid lease expiry",
                intervention_class="diagnose",
                action_policy="needs_diagnosis",
                source_ref=_canonical_source_ref(row),
            )
        age_s = max((now - expiry).total_seconds(), 0.0)
        if age_s <= lease_grace_s:
            return None
    else:
        age_s = max((now - expiry).total_seconds(), 0.0) if expiry else 0.0

    task_id = str(row.get("task_id") or "")
    owner = str(row.get("instance_id") or row.get("role") or "")
    if not owner:
        return _diagnosis_action(
            task_id,
            row,
            failure_class="task_attempt_lease_expired",
            reason="canonical task attempt lease expired but owner is missing",
            intervention_class="diagnose",
            action_policy="needs_diagnosis",
            source_ref=_canonical_source_ref(row),
        )
    checkpoint_id = _checkpoint_id("attempt-lease-expired", task_id, row)
    action = {
        "schema_version": "run-manager.pending-action.v1",
        "action": "worker-lifecycle-recover",
        "checkpoint_id": checkpoint_id,
        "safe_resume_action": "worker_lifecycle_recover",
        "task_id": task_id,
        "instance_id": owner,
        "role_instance": owner,
        "briefing_ref": str(row.get("briefing_ref") or ""),
        "attempt_key": str(row.get("attempt_key") or ""),
        "workflow_run_id": str(row.get("run_id") or ""),
        "operation_id": str(row.get("operation_id") or ""),
        "attempt_id": str(row.get("attempt_id") or ""),
        "lease_id": str(row.get("lease_id") or ""),
        "lease_token": str(row.get("lease_id") or ""),
        "dispatch_id": str(row.get("dispatch_id") or ""),
        "identity_version": str(row.get("identity_version") or ""),
        "placement_epoch": int(row.get("placement_epoch") or 0),
        "source_event_ids": _source_event_ids(row),
        "source_refs": [_canonical_source_ref(row)],
        "reason": f"task attempt lease expired after {int(age_s)}s",
        "failure_class": "task_attempt_lease_expired",
        "owner_route": "controlled_action",
        "action_policy": "auto_decide",
        "intervention_class": "auto_recover",
        "expected_downstream_events": sorted(
            expected_downstream_events("worker_lifecycle_recover")
        ),
        "verify_condition": "expected_downstream_event:worker.respawn.requested",
        "route_registry": "task-attempt-recovery.v1",
        "recovery_decision_owner": "run_manager",
        "recovery_effect_owner": "workflow_runtime_coordinator",
    }
    action["preflight"] = preflight_action(
        action="worker-lifecycle-recover",
        payload=action,
    )
    action["policy_decision"] = decide_action_policy(
        action="worker-lifecycle-recover",
        payload=action,
    )
    return action


def _canonical_failed_attempt_action(
    row: dict[str, Any],
    *,
    max_retry_attempts: int,
) -> dict[str, Any]:
    task_id = str(row.get("task_id") or "")
    status = str(row.get("status") or "")
    ordinal = int(row.get("ordinal") or 0)
    retryable = row.get("retryable") is not False
    exhausted = bool(max_retry_attempts and ordinal >= max_retry_attempts)
    failure_class = str(row.get("failure_class") or "task_attempt_failed")
    source_ref = _canonical_source_ref(row)
    if status == "deadlettered" or exhausted or not retryable:
        reason = "task attempt is non-retryable"
        if status == "deadlettered":
            reason = "task attempt is deadlettered"
        elif exhausted:
            reason = (
                "task attempt retry budget exhausted "
                f"({ordinal}/{max_retry_attempts})"
            )
        return _diagnosis_action(
            task_id,
            row,
            failure_class=failure_class,
            reason=reason,
            intervention_class="safe_halt",
            action_policy="human_escalate",
            source_ref=source_ref,
        )
    return _diagnosis_action(
        task_id,
        row,
        failure_class=failure_class,
        reason=(
            "scheduler-owned attempt failed; a workflow resume checkpoint is "
            "required before deterministic re-dispatch"
        ),
        intervention_class="diagnose",
        action_policy="needs_diagnosis",
        source_ref=source_ref,
    )


def _canonical_store_error_action(
    store_path: Path,
    error: BaseException,
) -> dict[str, Any]:
    row = {
        "attempt_id": "canonical-store",
        "run_id": "",
        "terminal_event_id": "",
    }
    return _diagnosis_action(
        "",
        row,
        failure_class="task_attempt_store_unreadable",
        reason=f"canonical TaskAttempt store is unreadable: {error}",
        intervention_class="safe_halt",
        action_policy="human_escalate",
        source_ref=str(store_path),
    )


def _load_attempt_projection(projections_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((Path(projections_dir) / "task_attempts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _latest_attempt(entry: dict[str, Any]) -> dict[str, Any]:
    attempts = entry.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return {}
    latest = attempts[-1]
    return latest if isinstance(latest, dict) else {}


def _expired_lease_action(
    task_id: str,
    entry: dict[str, Any],
    latest: dict[str, Any],
    *,
    now: datetime,
    lease_grace_s: float,
) -> dict[str, Any] | None:
    anchor_ts = str(
        latest.get("last_activity_ts")
        or latest.get("last_heartbeat_ts")
        or latest.get("started_ts")
        or ""
    )
    anchor = _parse_ts(anchor_ts)
    if anchor is None:
        return None
    age_s = max((now - anchor).total_seconds(), 0.0)
    if age_s <= lease_grace_s:
        return None
    owner = str(
        entry.get("current_owner")
        or latest.get("role")
        or latest.get("role_instance")
        or ""
    )
    if not owner:
        return _diagnosis_action(
            task_id,
            latest,
            failure_class="task_attempt_lease_expired",
            reason=(
                f"task attempt lease expired after {int(age_s)}s "
                "but owner is missing"
            ),
            intervention_class="diagnose",
            action_policy="needs_diagnosis",
        )
    checkpoint_id = _checkpoint_id("attempt-lease-expired", task_id, latest)
    action = {
        "schema_version": "run-manager.pending-action.v1",
        "action": "worker-lifecycle-recover",
        "checkpoint_id": checkpoint_id,
        "safe_resume_action": "worker_lifecycle_recover",
        "task_id": task_id,
        "instance_id": owner,
        "role_instance": owner,
        "briefing_ref": str(latest.get("briefing_ref") or ""),
        "attempt_key": str(latest.get("attempt_key") or ""),
        "lease_token": str(latest.get("lease_token") or ""),
        "source_event_ids": _source_event_ids(latest),
        "source_refs": [_source_ref(task_id)],
        "reason": f"task attempt lease expired after {int(age_s)}s",
        "failure_class": "task_attempt_lease_expired",
        "owner_route": "controlled_action",
        "action_policy": "auto_decide",
        "intervention_class": "auto_recover",
        "expected_downstream_events": sorted(expected_downstream_events("worker_lifecycle_recover")),
        "verify_condition": "expected_downstream_event:worker.respawn.requested",
        "route_registry": "task-attempt-recovery.v1",
    }
    action["preflight"] = preflight_action(
        action="worker-lifecycle-recover",
        payload=action,
    )
    action["policy_decision"] = decide_action_policy(
        action="worker-lifecycle-recover",
        payload=action,
    )
    return action


def _failed_attempt_action(
    task_id: str,
    entry: dict[str, Any],
    latest: dict[str, Any],
    *,
    max_retry_attempts: int,
) -> dict[str, Any]:
    retryable = latest.get("retryable") is not False
    counted_failures = int(entry.get("counted_failures") or 0)
    exhausted = bool(max_retry_attempts and counted_failures >= max_retry_attempts)
    deadlettered = str(entry.get("latest_state") or latest.get("state") or "") == "deadlettered"
    failure_class = str(latest.get("failure_signature") or "task_attempt_failed")
    if deadlettered or exhausted or not retryable:
        reason = "task attempt is non-retryable"
        if deadlettered:
            reason = "task attempt is deadlettered"
        elif exhausted:
            reason = (
                f"task attempt retry budget exhausted "
                f"({counted_failures}/{max_retry_attempts})"
            )
        return _diagnosis_action(
            task_id,
            latest,
            failure_class=failure_class,
            reason=reason,
            intervention_class="safe_halt",
            action_policy="human_escalate",
        )
    return _diagnosis_action(
        task_id,
        latest,
        failure_class=failure_class,
        reason=(
            "task attempt failed; workflow resume checkpoint is required "
            "before deterministic re-dispatch"
        ),
        intervention_class="diagnose",
        action_policy="needs_diagnosis",
    )


def _diagnosis_action(
    task_id: str,
    latest: dict[str, Any],
    *,
    failure_class: str,
    reason: str,
    intervention_class: str,
    action_policy: str,
    source_ref: str = "",
) -> dict[str, Any]:
    checkpoint_id = _checkpoint_id(failure_class, task_id, latest)
    owner_route = "run_manager" if action_policy == "needs_diagnosis" else "human"
    action = {
        "schema_version": "run-manager.pending-action.v1",
        "action": "diagnose-attention",
        "checkpoint_id": checkpoint_id,
        "safe_resume_action": "diagnose_attention",
        "task_id": task_id,
        "attempt_key": str(latest.get("attempt_key") or ""),
        "workflow_run_id": str(latest.get("run_id") or ""),
        "attempt_id": str(latest.get("attempt_id") or ""),
        "lease_id": str(latest.get("lease_id") or ""),
        "lease_token": str(
            latest.get("lease_token") or latest.get("lease_id") or ""
        ),
        "dispatch_id": str(latest.get("dispatch_id") or ""),
        "source_event_ids": _source_event_ids(latest),
        "source_refs": [source_ref or _source_ref(task_id)],
        "fingerprint": _fingerprint(task_id, latest, failure_class),
        "reason": reason,
        "failure_class": failure_class,
        "owner_route": owner_route,
        "action_policy": action_policy,
        "intervention_class": intervention_class,
        "expected_downstream_events": sorted(expected_downstream_events("diagnose_attention")),
        "verify_condition": (
            "expected_downstream_event:"
            "run.manager.autoresearch.requested,run.manager.resident.prompted"
        ),
        "route_registry": "task-attempt-recovery.v1",
    }
    action["preflight"] = preflight_action(
        action="diagnose-attention",
        payload=action,
    )
    action["policy_decision"] = decide_action_policy(
        action="diagnose-attention",
        payload=action,
    )
    if action_policy == "human_escalate":
        action["policy_decision"] = {
            **action["policy_decision"],
            "decision": "human_escalate",
            "executable": False,
            "reason": reason,
            "intervention_class": "human_decision",
        }
    return action


def _source_event_ids(latest: dict[str, Any]) -> list[str]:
    return [
        str(value) for value in (
            latest.get("source_event_id"),
            latest.get("terminal_event_id"),
            (latest.get("terminal") or {}).get("event_id")
            if isinstance(latest.get("terminal"), dict) else "",
        )
        if str(value or "").strip()
    ]


def _source_ref(task_id: str) -> str:
    return f"projections/task_attempts.json#tasks.{task_id}"


def _canonical_source_ref(row: dict[str, Any]) -> str:
    attempt_id = str(row.get("attempt_id") or "")
    return f"task_attempts.json#attempts.{attempt_id}"


def _checkpoint_id(prefix: str, task_id: str, latest: dict[str, Any]) -> str:
    return f"{prefix}-{_fingerprint(task_id, latest, prefix)}"


def _fingerprint(task_id: str, latest: dict[str, Any], failure_class: str) -> str:
    if any(
        str(latest.get(key) or "")
        for key in ("run_id", "attempt_id", "dispatch_id", "lease_id")
    ):
        raw = "|".join([
            task_id,
            str(latest.get("run_id") or ""),
            str(latest.get("attempt_id") or ""),
            str(latest.get("lease_id") or ""),
            str(latest.get("dispatch_id") or ""),
            str(latest.get("terminal_event_id") or ""),
            failure_class,
        ])
    else:
        raw = "|".join([
            task_id,
            str(latest.get("attempt_key") or ""),
            str(latest.get("source_event_id") or ""),
            str((latest.get("terminal") or {}).get("event_id") or "")
            if isinstance(latest.get("terminal"), dict) else "",
            failure_class,
        ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _parse_ts(value: str) -> datetime | None:
    value = str(value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
