"""VCR — Verified Completion Rate metric."""

from __future__ import annotations

from dataclasses import dataclass

from zf.core.task.store import TaskStore


@dataclass
class VcrReport:
    verified: int
    attempted: int
    rate: float  # 0.0 to 1.0
    per_role: dict[str, float]


def calculate_vcr(store: TaskStore) -> VcrReport:
    """Calculate Verified Completion Rate from task store."""
    tasks = store.list_all_with_archive()
    attempted = [t for t in tasks if t.status in ("review", "testing", "done")]
    verified = [t for t in tasks if t.status == "done" and t.evidence is not None]

    rate = len(verified) / len(attempted) if attempted else 0.0

    # Per-role rates
    per_role: dict[str, float] = {}
    by_role: dict[str, list] = {}
    for t in attempted:
        role = t.assigned_to or "unassigned"
        by_role.setdefault(role, []).append(t)

    for role, role_tasks in by_role.items():
        role_verified = sum(1 for t in role_tasks if t.status == "done" and t.evidence is not None)
        per_role[role] = role_verified / len(role_tasks) if role_tasks else 0.0

    return VcrReport(
        verified=len(verified),
        attempted=len(attempted),
        rate=rate,
        per_role=per_role,
    )
