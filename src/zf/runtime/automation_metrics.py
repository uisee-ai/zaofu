"""Management-grade scorecard metrics for project automations.

Same-source principle (doc: tasks/2026-06-15-1422 P0): this module only
aggregates signals the kernel already emits. It computes the cross-cutting
scorecard (autonomy / reliability / governance) from event types and a
percentile helper for cycle-time p50/p90. It introduces no new judgement and
no second control plane — rendering into insights stays in
``automation_projection``.
"""
from __future__ import annotations

from typing import Any, Iterable

from zf.core.events.model import ZfEvent

# Human-touch events: a delivery needed the operator/owner, so it did not run
# fully autonomously. Higher count = lower "省心".
_AUTONOMY_EVENTS: dict[str, str] = {
    "human.escalate": "escalations",
    "runtime.attention.escalated": "escalations",
    "owner.visible_message.requested": "owner_messages",
    "replan.owner_decision.approved": "owner_decisions",
    "replan.owner_decision.rejected": "owner_decisions",
    "replan.owner_decision.deferred": "owner_decisions",
}

# Harness self-reliability incidents — the signals that kill an unattended
# long-horizon run. safe-halt / circuit are the most severe.
_RELIABILITY_EVENTS: dict[str, str] = {
    "dispatch.silent_stall": "silent_stall",
    "runtime.safe_halted": "safe_halt",
    "circuit.tripped": "circuit_tripped",
    "worker.stuck": "worker_stuck",
    "task.rework.capped": "rework_capped",
    "orchestrator.tick.failed": "tick_failed",
    "task.orphaned": "orphaned",
    "remediation.cascade": "remediation_cascade",
}
_RELIABILITY_CRITICAL = frozenset({"safe_halt", "circuit_tripped"})

# Governance / drift — the controlled-harness red lines being crossed quietly.
_GOVERNANCE_EVENTS: dict[str, str] = {
    "workflow.inline_override": "inline_override",
    "scope.violation": "scope_violation",
    "task.baseline_diverged": "baseline_diverged",
    "provider.permission.snapshot.drift": "permission_drift",
}


def percentile(values: Iterable[float | None], p: float) -> float | None:
    """Linear-interpolation percentile. ``p`` in [0, 100]. None values dropped.

    Returns None for an empty input. Replaces the mean as the headline cycle
    metric (mean hides the long tail that actually hurts).
    """
    nums = sorted(v for v in values if v is not None)
    if not nums:
        return None
    if len(nums) == 1:
        return round(float(nums[0]), 2)
    rank = (len(nums) - 1) * (p / 100.0)
    low = int(rank)
    high = min(low + 1, len(nums) - 1)
    frac = rank - low
    return round(nums[low] + (nums[high] - nums[low]) * frac, 2)


def _bucket(events: Iterable[ZfEvent], mapping: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {key: 0 for key in dict.fromkeys(mapping.values())}
    for event in events:
        key = mapping.get(event.type)
        if key is not None:
            counts[key] += 1
    return counts


def cross_cutting_scorecard(events: Iterable[ZfEvent]) -> dict[str, Any]:
    """Aggregate the autonomy / reliability / governance scorecard.

    Pure event-type counting — every number is traceable to a known event
    type, so the scorecard re-judges nothing.
    """
    ordered = list(events)
    autonomy = _bucket(ordered, _AUTONOMY_EVENTS)
    reliability = _bucket(ordered, _RELIABILITY_EVENTS)
    governance = _bucket(ordered, _GOVERNANCE_EVENTS)

    autonomy["interventions_total"] = sum(autonomy.values())
    reliability["incidents_total"] = sum(reliability.values())
    reliability["critical_total"] = sum(
        reliability[key] for key in _RELIABILITY_CRITICAL
    )
    governance["violations_total"] = sum(governance.values())

    return {
        "autonomy": autonomy,
        "reliability": reliability,
        "governance": governance,
    }


_ARCHETYPES = ("feature", "refactor", "bugfix")


def _event_feature_id(event: ZfEvent, task_feature: dict[str, str]) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    fid = str(payload.get("feature_id") or "")
    if not fid and event.task_id:
        fid = task_feature.get(event.task_id, "")
    if not fid and isinstance(payload.get("contract"), dict):
        fid = str(payload["contract"].get("feature_id") or "")
    return fid


def commit_counts_by_task(events: Iterable[ZfEvent]) -> dict[str, int]:
    """Per-task commit count from candidate integration events (latest wins).

    Same-source (doc 96 thin-kernel): reads ``candidate.task_ref.applied``
    payloads that the kernel-owned candidate integration already emits — no git
    shelling, no new capture. Line-level diff churn is not in events; that waits
    for doc 96 P6 Integration Arbiter enrichment.
    """
    counts: dict[str, int] = {}
    for event in events:
        if event.type != "candidate.task_ref.applied":
            continue
        tid = event.task_id or ""
        if not tid:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        count = payload.get("selected_commit_count")
        if count is None:
            task_commits = payload.get("task_commits")
            count = (
                len(task_commits) if isinstance(task_commits, list)
                else payload.get("commit_count", 0)
            )
        counts[tid] = int(count or 0)
    return counts


def build_archetype_matrix(
    tasks: list[Any],
    events: Iterable[ZfEvent],
    *,
    duration_hours: Any,
    rework_task_ids: set[str],
    commit_counts: dict[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """Bucket delivery into feature / refactor / bugfix and aggregate per bucket.

    Same-source: archetype comes from ``derive_workflow_archetype`` over each
    feature's events; cycle p50/p90 from done-task durations; first_pass_yield
    from done-without-rework. No new judgement, no per-feature full trace build.

    ``duration_hours`` is a callable(task) -> float | None supplied by the
    caller (which owns the timestamp parser), keeping this module dependency
    light. ``rework_task_ids`` are tasks that saw any rework/failure signal.
    """
    from zf.runtime.delivery_flow_metrics import derive_workflow_archetype

    task_feature = {
        task.id: (getattr(getattr(task, "contract", None), "feature_id", "") or "")
        for task in tasks
    }

    feature_events: dict[str, list[ZfEvent]] = {}
    for event in events:
        fid = _event_feature_id(event, task_feature)
        if fid:
            feature_events.setdefault(fid, []).append(event)
    feature_archetype = {
        fid: derive_workflow_archetype(evs) for fid, evs in feature_events.items()
    }

    commits = commit_counts or {}
    buckets: dict[str, dict[str, Any]] = {
        archetype: {
            "features": set(), "done": 0, "durations": [],
            "done_no_rework": 0, "commits": 0,
        }
        for archetype in _ARCHETYPES
    }
    for task in tasks:
        fid = task_feature.get(task.id, "")
        archetype = feature_archetype.get(fid, "feature")
        bucket = buckets[archetype]
        if fid:
            bucket["features"].add(fid)
        bucket["commits"] += commits.get(task.id, 0)
        if task.status == "done":
            bucket["done"] += 1
            hours = duration_hours(task)
            if hours is not None:
                bucket["durations"].append(hours)
            if task.id not in rework_task_ids:
                bucket["done_no_rework"] += 1

    matrix: dict[str, dict[str, Any]] = {}
    for archetype in _ARCHETYPES:
        bucket = buckets[archetype]
        done = bucket["done"]
        feature_count = len(bucket["features"])
        matrix[archetype] = {
            "features": feature_count,
            "done_tasks": done,
            "cycle_p50_hours": percentile(bucket["durations"], 50),
            "cycle_p90_hours": percentile(bucket["durations"], 90),
            "first_pass_yield": (
                round(bucket["done_no_rework"] / done, 4) if done else None
            ),
            "commits": bucket["commits"],
            "commits_per_feature": (
                round(bucket["commits"] / feature_count, 1) if feature_count else None
            ),
        }
    return matrix
