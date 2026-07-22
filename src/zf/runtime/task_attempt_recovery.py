"""TaskAttempt recovery policy derived from shadow spine projections.

This module does not mutate kernel truth and does not invent workflow resume
checkpoints. It turns ``projections/task_attempts.json`` into Run Manager
pending actions that can be handled by existing deterministic routes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.runtime.run_manager_router import (
    decide_action_policy,
    expected_downstream_events,
    preflight_action,
)

TASK_ATTEMPT_RECOVERY_SCHEMA_VERSION = "task-attempt-recovery.v1"


def pending_task_attempt_recovery_actions(
    projections_dir: Path,
    *,
    now: datetime | None = None,
    lease_grace_s: float = 900.0,
    max_retry_attempts: int = 3,
) -> list[dict[str, Any]]:
    """Build Run Manager pending actions from task attempt projection state.

    The output is intentionally conservative:
    - expired open attempt with an owner -> existing worker lifecycle recovery;
    - retryable failed attempt -> diagnosis/autoresearch, not direct rework;
    - deadlettered/exhausted attempt -> human/safe-halt explanation.
    """

    data = _load_attempt_projection(Path(projections_dir))
    tasks = data.get("tasks")
    if not isinstance(tasks, dict):
        return []
    now = now or datetime.now(timezone.utc)
    lease_grace_s = max(float(lease_grace_s or 0.0), 0.0)
    max_retry_attempts = max(int(max_retry_attempts or 0), 0)

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
        "lease_token": str(latest.get("lease_token") or ""),
        "source_event_ids": _source_event_ids(latest),
        "source_refs": [_source_ref(task_id)],
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
            (latest.get("terminal") or {}).get("event_id")
            if isinstance(latest.get("terminal"), dict) else "",
        )
        if str(value or "").strip()
    ]


def _source_ref(task_id: str) -> str:
    return f"projections/task_attempts.json#tasks.{task_id}"


def _checkpoint_id(prefix: str, task_id: str, latest: dict[str, Any]) -> str:
    return f"{prefix}-{_fingerprint(task_id, latest, prefix)}"


def _fingerprint(task_id: str, latest: dict[str, Any], failure_class: str) -> str:
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
