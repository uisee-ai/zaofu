"""Canonical current state for scheduler-owned TaskAttempts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path


TASK_ATTEMPT_STORE_SCHEMA_VERSION = "task-attempt-store.v1"
TASK_ATTEMPT_IDENTITY_ROLE_V1 = "role-v1"
TASK_ATTEMPT_IDENTITY_OPERATION_V2 = "operation-v2"
_ATTEMPT_IDENTITY_VERSIONS = frozenset({
    TASK_ATTEMPT_IDENTITY_ROLE_V1,
    TASK_ATTEMPT_IDENTITY_OPERATION_V2,
})
_ACTIVE_STATUSES = frozenset({"prepared", "delivering", "sent"})
_ATTEMPT_STATUSES = _ACTIVE_STATUSES | frozenset({
    "succeeded",
    "failed",
    "expired",
    "superseded",
    "deadlettered",
})


class TaskAttemptLimitError(RuntimeError):
    pass


class TaskAttemptStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class EnsureTaskAttemptResult:
    attempt: dict[str, Any]
    created: bool
    superseded_attempt_id: str = ""


class TaskAttemptStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        with locked_path(self.path):
            return self._load_unlocked()

    def get(self, attempt_id: str) -> dict[str, Any] | None:
        data = self.load()
        row = (data.get("attempts") or {}).get(str(attempt_id or ""))
        return dict(row) if isinstance(row, dict) else None

    def current(self, *, run_id: str, task_id: str) -> dict[str, Any] | None:
        """Resolve a Run/task only when its current attempt lane is unambiguous."""

        data = self.load()
        matches = _current_rows(
            data,
            run_id=str(run_id or ""),
            task_id=str(task_id or ""),
        )
        return _select_unambiguous(matches)

    def current_for_task(
        self,
        task_id: str,
        *,
        dispatch_id: str = "",
    ) -> dict[str, Any] | None:
        """Resolve a task only when its current Run binding is unambiguous."""

        task_id = str(task_id or "").strip()
        if not task_id:
            return None
        data = self.load()
        matches = _current_rows(
            data,
            task_id=task_id,
            dispatch_id=str(dispatch_id or "").strip(),
        )
        return _select_unambiguous(matches)

    def current_for_attempt(
        self,
        *,
        task_id: str,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        """Resolve the current row in the lane named by a supplied attempt."""

        task_id = str(task_id or "").strip()
        attempt_id = str(attempt_id or "").strip()
        if not task_id or not attempt_id:
            return None
        data = self.load()
        supplied = (data.get("attempts") or {}).get(attempt_id)
        if (
            not isinstance(supplied, dict)
            or str(supplied.get("task_id") or "") != task_id
        ):
            return None
        scope = _scope_key(
            str(supplied.get("run_id") or ""),
            task_id,
            str(supplied.get("attempt_key") or ""),
        )
        current_id = str((data.get("current") or {}).get(scope) or "")
        current = (data.get("attempts") or {}).get(current_id)
        return dict(current) if isinstance(current, dict) else None

    def ensure_for_dispatch(
        self,
        *,
        run_id: str,
        task_id: str,
        dispatch_id: str,
        role: str,
        instance_id: str,
        operation_id: str,
        briefing_ref: str,
        created_at: str,
        lease_expires_at: str,
        max_attempts: int,
        parent_task_id: str = "",
        identity_version: str = TASK_ATTEMPT_IDENTITY_ROLE_V1,
        placement_epoch: int = 0,
    ) -> EnsureTaskAttemptResult:
        run_id = str(run_id or "").strip()
        task_id = str(task_id or "").strip()
        dispatch_id = str(dispatch_id or "").strip()
        if not task_id or not dispatch_id:
            raise ValueError("TaskAttempt requires task_id and dispatch_id")
        identity_version = str(identity_version or "").strip()
        if identity_version not in _ATTEMPT_IDENTITY_VERSIONS:
            raise ValueError(
                f"unsupported TaskAttempt identity_version {identity_version!r}"
            )
        try:
            placement_epoch = int(placement_epoch)
        except (TypeError, ValueError) as exc:
            raise ValueError("TaskAttempt placement_epoch must be an integer") from exc
        if placement_epoch < 0:
            raise ValueError("TaskAttempt placement_epoch must be >= 0")
        if (
            identity_version == TASK_ATTEMPT_IDENTITY_OPERATION_V2
            and placement_epoch < 1
        ):
            raise ValueError(
                "operation-v2 TaskAttempt placement_epoch must be >= 1"
            )
        run_id = run_id or "legacy"
        attempt_id = task_attempt_id(run_id, task_id, dispatch_id)
        lease_id = task_attempt_lease_id(attempt_id)
        attempt_key = _attempt_key(
            run_id,
            task_id,
            str(operation_id or ""),
            str(role or instance_id or ""),
            identity_version=identity_version,
        )
        with locked_path(self.path):
            data = self._load_unlocked()
            attempts = data["attempts"]
            existing = attempts.get(attempt_id)
            if isinstance(existing, dict):
                return EnsureTaskAttemptResult(
                    attempt=dict(existing),
                    created=False,
                )

            scope = _scope_key(run_id, task_id, attempt_key)
            prior_id = str(data["current"].get(scope) or "")
            prior = attempts.get(prior_id)
            operation_rows = [
                row
                for row in attempts.values()
                if isinstance(row, dict)
                and str(row.get("run_id") or "") == run_id
                and str(row.get("task_id") or "") == task_id
                and str(row.get("attempt_key") or "") == attempt_key
            ]
            latest_operation = max(
                operation_rows,
                key=_attempt_order,
                default=None,
            )
            if (
                isinstance(latest_operation, dict)
                and str(latest_operation.get("status") or "") == "succeeded"
            ):
                series = int(latest_operation.get("series") or 1) + 1
                ordinal = 1
            else:
                series = int(
                    (latest_operation or {}).get("series") or 1
                )
                ordinal = int(
                    (latest_operation or {}).get("ordinal") or 0
                ) + 1
            if int(max_attempts or 0) > 0 and ordinal > int(max_attempts):
                raise TaskAttemptLimitError(
                    f"TaskAttempt budget exhausted for "
                    f"{run_id}/{task_id}/{operation_id}: "
                    f"{ordinal - 1}/{max_attempts}"
                )

            superseded_id = ""
            if (
                isinstance(prior, dict)
                and prior_id != attempt_id
                and (
                    str(prior.get("status") or "") in _ACTIVE_STATUSES
                    or (
                        str(prior.get("operation_id") or "")
                        == str(operation_id or "")
                        and str(prior.get("status") or "")
                        not in {"succeeded", "deadlettered"}
                    )
                )
            ):
                prior["superseded_from_status"] = str(
                    prior.get("status") or ""
                )
                prior["status"] = "superseded"
                prior["superseded_by"] = attempt_id
                prior["updated_at"] = created_at
                superseded_id = prior_id
            attempt = {
                "schema_version": "task-attempt.v1",
                "attempt_id": attempt_id,
                "lease_id": lease_id,
                "run_id": run_id,
                "task_id": task_id,
                "parent_task_id": str(parent_task_id or ""),
                "operation_id": str(operation_id or ""),
                "attempt_key": attempt_key,
                "identity_version": identity_version,
                "dispatch_id": dispatch_id,
                "role": str(role or ""),
                "instance_id": str(instance_id or ""),
                "placement_epoch": placement_epoch,
                "briefing_ref": str(briefing_ref or ""),
                "ordinal": ordinal,
                "series": series,
                "status": "prepared",
                "created_at": created_at,
                "updated_at": created_at,
                "lease_expires_at": lease_expires_at,
                "delivery_claimed_at": "",
                "sent_at": "",
                "superseded_by": "",
                "terminal_event_id": "",
                "failure_reason": "",
                "failure_class": "",
                "retryable": None,
                "recovery_owner": "",
            }
            attempts[attempt_id] = attempt
            data["current"][scope] = attempt_id
            self._save_unlocked(data)
            return EnsureTaskAttemptResult(
                attempt=dict(attempt),
                created=True,
                superseded_attempt_id=superseded_id,
            )

    def claim_delivery(
        self,
        attempt_id: str,
        *,
        updated_at: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        with locked_path(self.path):
            data = self._load_unlocked()
            row = data["attempts"].get(attempt_id)
            if not isinstance(row, dict):
                return None, False
            if row.get("status") != "prepared":
                return dict(row), False
            row["status"] = "delivering"
            row["updated_at"] = updated_at
            row["delivery_claimed_at"] = updated_at
            self._save_unlocked(data)
            return dict(row), True

    def mark_sent(
        self,
        attempt_id: str,
        *,
        updated_at: str,
    ) -> dict[str, Any] | None:
        with locked_path(self.path):
            data = self._load_unlocked()
            row = data["attempts"].get(attempt_id)
            if not isinstance(row, dict):
                return None
            if row.get("status") not in {"delivering", "sent"}:
                return dict(row)
            row["status"] = "sent"
            row["updated_at"] = updated_at
            row["sent_at"] = str(row.get("sent_at") or updated_at)
            self._save_unlocked(data)
            return dict(row)

    def renew_lease(
        self,
        attempt_id: str,
        *,
        updated_at: str,
        lease_expires_at: str,
    ) -> dict[str, Any] | None:
        """Return a row only when an active attempt's lease was renewed."""
        with locked_path(self.path):
            data = self._load_unlocked()
            row = data["attempts"].get(attempt_id)
            if not isinstance(row, dict):
                return None
            if row.get("status") not in _ACTIVE_STATUSES:
                return None
            row["updated_at"] = updated_at
            row["lease_expires_at"] = lease_expires_at
            self._save_unlocked(data)
            return dict(row)

    def update(
        self,
        attempt_id: str,
        *,
        status: str,
        updated_at: str,
        terminal_event_id: str = "",
        failure_reason: str = "",
        failure_class: str = "",
        retryable: bool | None = None,
        recovery_owner: str = "",
    ) -> dict[str, Any] | None:
        with locked_path(self.path):
            data = self._load_unlocked()
            row = data["attempts"].get(attempt_id)
            if not isinstance(row, dict):
                return None
            row["status"] = str(status)
            row["updated_at"] = updated_at
            if terminal_event_id:
                row["terminal_event_id"] = terminal_event_id
            if failure_reason:
                row["failure_reason"] = failure_reason
            if failure_class:
                row["failure_class"] = failure_class
            if retryable is not None:
                row["retryable"] = bool(retryable)
            if recovery_owner:
                row["recovery_owner"] = recovery_owner
            self._save_unlocked(data)
            return dict(row)

    def expire(
        self,
        *,
        now_iso: str,
        is_expired: Callable[[str], bool],
    ) -> list[dict[str, Any]]:
        expired: list[dict[str, Any]] = []
        with locked_path(self.path):
            data = self._load_unlocked()
            for row in data["attempts"].values():
                if not isinstance(row, dict):
                    continue
                if str(row.get("status") or "") not in _ACTIVE_STATUSES:
                    continue
                if not is_expired(str(row.get("lease_expires_at") or "")):
                    continue
                row["status"] = "expired"
                row["updated_at"] = now_iso
                row["failure_reason"] = "lease_expired"
                row["failure_class"] = "lease_expired"
                row["retryable"] = True
                row["recovery_owner"] = "scheduler"
                expired.append(dict(row))
            if expired:
                self._save_unlocked(data)
        return expired

    def rows(self) -> list[dict[str, Any]]:
        data = self.load()
        return [
            dict(row)
            for row in (data.get("attempts") or {}).values()
            if isinstance(row, dict)
        ]

    def current_rows(self) -> list[dict[str, Any]]:
        """Return one canonical current row for every Run/task/operation lane."""

        return _current_rows(self.load())

    def _load_unlocked(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = {}
        except json.JSONDecodeError as exc:
            raise TaskAttemptStoreError(
                f"TaskAttempt store is not valid JSON: {self.path}"
            ) from exc
        except OSError as exc:
            raise TaskAttemptStoreError(
                f"TaskAttempt store cannot be read: {self.path}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise TaskAttemptStoreError(
                f"TaskAttempt store root must be an object: {self.path}"
            )
        schema_version = str(raw.get("schema_version") or "")
        if schema_version and schema_version != TASK_ATTEMPT_STORE_SCHEMA_VERSION:
            raise TaskAttemptStoreError(
                "Unsupported TaskAttempt store schema "
                f"{schema_version!r}: {self.path}"
            )
        attempts = raw.get("attempts")
        current = raw.get("current")
        if attempts is not None and not isinstance(attempts, dict):
            raise TaskAttemptStoreError(
                f"TaskAttempt store attempts must be an object: {self.path}"
            )
        if current is not None and not isinstance(current, dict):
            raise TaskAttemptStoreError(
                f"TaskAttempt store current must be an object: {self.path}"
            )
        try:
            revision = int(raw.get("revision") or 0)
        except (TypeError, ValueError) as exc:
            raise TaskAttemptStoreError(
                f"TaskAttempt store revision must be an integer: {self.path}"
            ) from exc
        attempts = attempts if isinstance(attempts, dict) else {}
        current = current if isinstance(current, dict) else {}
        malformed_attempt = next(
            (
                str(attempt_id)
                for attempt_id, row in attempts.items()
                if not isinstance(row, dict)
            ),
            None,
        )
        if malformed_attempt is not None:
            raise TaskAttemptStoreError(
                "TaskAttempt store row must be an object: "
                f"{malformed_attempt}: {self.path}"
            )
        for attempt_id, row in attempts.items():
            reason = _attempt_row_error(str(attempt_id), row)
            if reason:
                raise TaskAttemptStoreError(
                    f"TaskAttempt store row is invalid: {reason}: {self.path}"
                )
        dangling_scope = next(
            (
                str(scope)
                for scope, attempt_id in current.items()
                if not isinstance(attempt_id, str)
                or attempt_id not in attempts
            ),
            None,
        )
        if dangling_scope is not None:
            raise TaskAttemptStoreError(
                "TaskAttempt store current pointer is invalid: "
                f"{dangling_scope}: {self.path}"
            )
        mismatched_scope = next(
            (
                str(scope)
                for scope, attempt_id in current.items()
                if str(scope) != _scope_key(
                    str(attempts[attempt_id].get("run_id") or ""),
                    str(attempts[attempt_id].get("task_id") or ""),
                    str(attempts[attempt_id].get("attempt_key") or ""),
                )
            ),
            None,
        )
        if mismatched_scope is not None:
            raise TaskAttemptStoreError(
                "TaskAttempt store current scope is invalid: "
                f"{mismatched_scope}: {self.path}"
            )
        return {
            "schema_version": TASK_ATTEMPT_STORE_SCHEMA_VERSION,
            "revision": revision,
            "attempts": attempts,
            "current": current,
        }

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        data = {
            **data,
            "schema_version": TASK_ATTEMPT_STORE_SCHEMA_VERSION,
            "revision": int(data.get("revision") or 0) + 1,
        }
        atomic_write_text(
            self.path,
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )


def _scope_key(run_id: str, task_id: str, attempt_key: str) -> str:
    return f"{run_id}::{task_id}::{attempt_key}"


def _current_rows(
    data: dict[str, Any],
    *,
    run_id: str = "",
    task_id: str = "",
    dispatch_id: str = "",
) -> list[dict[str, Any]]:
    attempts = data.get("attempts") or {}
    rows: list[dict[str, Any]] = []
    for attempt_id in dict.fromkeys((data.get("current") or {}).values()):
        row = attempts.get(str(attempt_id or ""))
        if not isinstance(row, dict):
            continue
        if run_id and str(row.get("run_id") or "") != run_id:
            continue
        if task_id and str(row.get("task_id") or "") != task_id:
            continue
        if dispatch_id and str(row.get("dispatch_id") or "") != dispatch_id:
            continue
        rows.append(dict(row))
    return rows


def _select_unambiguous(
    matches: list[dict[str, Any]],
) -> dict[str, Any] | None:
    active = [
        row
        for row in matches
        if str(row.get("status") or "") in _ACTIVE_STATUSES
    ]
    if len(active) == 1:
        return active[0]
    if active:
        return None
    return matches[0] if len(matches) == 1 else None


def _attempt_order(row: dict[str, Any]) -> tuple[int, int, str, str]:
    return (
        int(row.get("series") or 1),
        int(row.get("ordinal") or 0),
        str(row.get("created_at") or ""),
        str(row.get("attempt_id") or ""),
    )


def _attempt_row_error(attempt_id: str, row: dict[str, Any]) -> str:
    required = (
        "attempt_id",
        "lease_id",
        "run_id",
        "task_id",
        "operation_id",
        "attempt_key",
        "dispatch_id",
        "status",
    )
    missing = [
        key for key in required if not str(row.get(key) or "").strip()
    ]
    if missing:
        return f"{attempt_id} missing {', '.join(missing)}"
    if str(row.get("schema_version") or "") != "task-attempt.v1":
        return f"{attempt_id} has unsupported schema"
    if str(row.get("attempt_id") or "") != attempt_id:
        return f"{attempt_id} identity does not match its key"
    identity_version = str(
        row.get("identity_version") or TASK_ATTEMPT_IDENTITY_ROLE_V1
    )
    if identity_version not in _ATTEMPT_IDENTITY_VERSIONS:
        return f"{attempt_id} has unsupported identity_version {identity_version!r}"
    expected_key = _attempt_key(
        str(row.get("run_id") or ""),
        str(row.get("task_id") or ""),
        str(row.get("operation_id") or ""),
        str(row.get("role") or row.get("instance_id") or ""),
        identity_version=identity_version,
    )
    if str(row.get("attempt_key") or "") != expected_key:
        return f"{attempt_id} attempt_key does not match its identity"
    status = str(row.get("status") or "")
    if status not in _ATTEMPT_STATUSES:
        return f"{attempt_id} has unsupported status {status!r}"
    try:
        ordinal = int(row.get("ordinal"))
        series = int(row.get("series"))
    except (TypeError, ValueError):
        return f"{attempt_id} ordinal/series must be integers"
    if ordinal < 1 or series < 1:
        return f"{attempt_id} ordinal/series must be positive"
    try:
        placement_epoch = int(row.get("placement_epoch") or 0)
    except (TypeError, ValueError):
        return f"{attempt_id} placement_epoch must be an integer"
    if placement_epoch < 0:
        return f"{attempt_id} placement_epoch must be >= 0"
    if (
        identity_version == TASK_ATTEMPT_IDENTITY_OPERATION_V2
        and placement_epoch < 1
    ):
        return f"{attempt_id} operation-v2 placement_epoch must be >= 1"
    return ""


def task_attempt_id(run_id: str, task_id: str, dispatch_id: str) -> str:
    """Return the deterministic scheduler attempt identity for a dispatch."""

    digest = hashlib.sha256(
        f"{run_id}|{task_id}|{dispatch_id}".encode("utf-8")
    ).hexdigest()[:20]
    return f"ta-{digest}"


def _attempt_key(
    run_id: str,
    task_id: str,
    operation_id: str,
    role: str,
    *,
    identity_version: str = TASK_ATTEMPT_IDENTITY_ROLE_V1,
) -> str:
    identity_parts = [run_id, task_id, operation_id]
    if identity_version == TASK_ATTEMPT_IDENTITY_ROLE_V1:
        identity_parts.append(role)
    digest = hashlib.sha256(
        "|".join(identity_parts).encode("utf-8")
    ).hexdigest()[:20]
    return f"tak-{digest}"


def task_attempt_lease_id(attempt_id: str) -> str:
    digest = hashlib.sha256(
        f"lease|{attempt_id}".encode("utf-8")
    ).hexdigest()[:20]
    return f"lease-{digest}"


__all__ = [
    "EnsureTaskAttemptResult",
    "TASK_ATTEMPT_IDENTITY_OPERATION_V2",
    "TASK_ATTEMPT_IDENTITY_ROLE_V1",
    "TASK_ATTEMPT_STORE_SCHEMA_VERSION",
    "TaskAttemptLimitError",
    "TaskAttemptStore",
    "TaskAttemptStoreError",
    "task_attempt_id",
    "task_attempt_lease_id",
]
