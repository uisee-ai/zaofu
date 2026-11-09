"""One-sweep reader fanout recovery projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.run_admission import (
    RUN_TERMINAL_EVENT_TYPES,
    fold_terminal_run_scope,
)
from zf.runtime.run_scope import event_run_id
from zf.runtime.task_pipeline_contexts import (
    CANDIDATE_FREEZE_RECEIPT_SCHEMA,
    TASK_PIPELINE_GENERATION_ADMITTED,
    TASK_PIPELINE_GENERATION_SCHEMA,
)


_TERMINAL_FANOUT_STATUSES = frozenset({
    "completed",
    "failed",
    "timed_out",
    "cancelled",
})
_RESULT_STATUSES = frozenset({
    "completed",
    "passed",
    "approved",
    "success",
    "failed",
    "failure",
    "rejected",
})


@dataclass(frozen=True)
class ReaderOrphanCandidate:
    fanout_id: str
    child_id: str


@dataclass(frozen=True)
class _GoalClaim:
    order: int
    claim_id: str
    run_id: str
    goal_keys: frozenset[str]


@dataclass(frozen=True)
class ReaderFanoutRecoverySnapshot:
    events: tuple[ZfEvent, ...]
    event_order: dict[str, int]
    manifests: tuple[tuple[str, dict[str, Any]], ...]
    manifests_by_id: dict[str, dict[str, Any]]
    currentness: dict[str, tuple[str, str]]
    orphan_candidates: dict[str, tuple[ReaderOrphanCandidate, ...]]
    result_event_types: frozenset[str]
    superseding_goal_claims: dict[str, str]
    event_log_token: tuple[object, ...]
    consistent: bool


def build_reader_fanout_recovery_snapshot(
    runtime: Any,
    events: list[ZfEvent],
) -> ReaderFanoutRecoverySnapshot:
    """Load read-only manifest/currentness context once for one sweep."""

    rows = tuple(events)
    token_before = event_log_snapshot_token(runtime.event_log)
    authority_rows = tuple(runtime.event_log.read_all())
    manifests: list[tuple[str, dict[str, Any]]] = []
    fanout_root = runtime.state_dir / "fanouts"
    if fanout_root.exists():
        for manifest_path in sorted(fanout_root.glob("*/manifest.json")):
            fanout_id = manifest_path.parent.name
            manifest = runtime._fanout_manifest(fanout_id)
            if isinstance(manifest, dict) and manifest:
                manifests.append((fanout_id, manifest))

    active_manifests = [
        (fanout_id, manifest)
        for fanout_id, manifest in manifests
        if _active_reader_manifest(manifest)
    ]
    currentness = (
        build_fanout_currentness(authority_rows)
        if active_manifests
        else {}
    )
    superseding_goal_claims = _superseding_goal_claims(
        authority_rows,
        manifests=active_manifests,
    )
    candidates: dict[str, list[ReaderOrphanCandidate]] = {}
    result_event_types: set[str] = set()
    for fanout_id, manifest in active_manifests:
        if currentness.get(fanout_id, ("", ""))[0]:
            continue
        aggregate_config = manifest.get("aggregate_config") or {}
        child_success_event, child_failure_event = (
            runtime._fanout_child_result_events(aggregate_config)
        )
        result_event_types.update({
            str(aggregate_config.get("success_event") or ""),
            str(aggregate_config.get("failure_event") or ""),
            child_success_event,
            child_failure_event,
        })
        for child in manifest.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            if str(child.get("status") or "") in {"completed", "failed"}:
                continue
            role_instance = str(child.get("role_instance") or "").strip()
            child_id = str(child.get("child_id") or "").strip()
            if not role_instance or not child_id:
                continue
            candidates.setdefault(role_instance, []).append(
                ReaderOrphanCandidate(
                    fanout_id=fanout_id,
                    child_id=child_id,
                )
            )

    token_after = event_log_snapshot_token(runtime.event_log)
    return ReaderFanoutRecoverySnapshot(
        events=authority_rows,
        event_order={event.id: index for index, event in enumerate(rows)},
        manifests=tuple(manifests),
        manifests_by_id=dict(manifests),
        currentness=currentness,
        orphan_candidates={
            role: tuple(items) for role, items in candidates.items()
        },
        result_event_types=frozenset(
            event_type for event_type in result_event_types if event_type
        ),
        superseding_goal_claims=superseding_goal_claims,
        event_log_token=token_after,
        consistent=token_before == token_after,
    )


def reader_recovery_event_may_match(
    snapshot: ReaderFanoutRecoverySnapshot,
    event: ZfEvent,
) -> bool:
    """Cheaply exclude history that cannot belong to an active reader child."""

    payload = event.payload if isinstance(event.payload, dict) else {}
    report = payload.get("report")
    report = report if isinstance(report, dict) else {}
    fanout_id = str(payload.get("fanout_id") or report.get("fanout_id") or "")
    child_id = str(
        payload.get("child_id")
        or payload.get("child_run")
        or report.get("child_id")
        or report.get("child_run")
        or ""
    )
    if fanout_id and child_id:
        return fanout_id in snapshot.manifests_by_id
    role_instance = str(
        payload.get("role_instance")
        or report.get("role_instance")
        or event.actor
        or ""
    ).strip()
    if role_instance not in snapshot.orphan_candidates:
        return False
    status = str(payload.get("status") or report.get("status") or "")
    return bool(
        status in _RESULT_STATUSES
        or report
        or event.type in snapshot.result_event_types
    )


def build_fanout_currentness(
    events: tuple[ZfEvent, ...] | list[ZfEvent],
) -> dict[str, tuple[str, str]]:
    """Fold terminal, generation, and fanout identity once per event snapshot."""

    rows = list(events)
    from zf.runtime.fanout_identity import build_fanout_identity_projection
    from zf.runtime.reader_candidate_rework import candidate_rework_reader_fences

    identity_projection = build_fanout_identity_projection(rows)
    from zf.runtime.dynamic_continuation_lifecycle import parent_fence
    from zf.runtime.workflow_operation import reduce_workflow_operations

    operations = reduce_workflow_operations(rows)
    rework_fences = candidate_rework_reader_fences(rows)
    identity_by_id = {
        str(item.get("fanout_id") or ""): item
        for item in identity_projection.get("instances", []) or []
        if isinstance(item, dict) and str(item.get("fanout_id") or "")
    }

    aliases, terminal_runs = fold_terminal_run_scope(rows)
    first_index: dict[str, int] = {}
    fanout_runs: dict[str, set[str]] = {}
    terminal_events: dict[str, list[tuple[int, ZfEvent]]] = {}
    started: dict[str, ZfEvent] = {}
    latest_generation: dict[str, dict[str, str]] = {}

    for index, event in enumerate(rows):
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "").strip()
        if fanout_id:
            first_index.setdefault(fanout_id, index)
            run_id = event_run_id(event, aliases=aliases)
            if run_id:
                fanout_runs.setdefault(fanout_id, set()).add(run_id)
            if event.type == "fanout.started":
                started[fanout_id] = event

        run_id = event_run_id(event, aliases=aliases)
        if event.type in RUN_TERMINAL_EVENT_TYPES and run_id in terminal_runs:
            terminal_events.setdefault(run_id, []).append((index, event))

        if event.type != TASK_PIPELINE_GENERATION_ADMITTED:
            continue
        if str(payload.get("schema_version") or "") != (
            TASK_PIPELINE_GENERATION_SCHEMA
        ):
            continue
        generation_run_id = str(
            payload.get("workflow_run_id") or event.correlation_id or ""
        ).strip()
        if generation_run_id:
            latest_generation[generation_run_id] = {
                "generation_id": str(payload.get("generation_id") or ""),
                "event_id": event.id,
            }

    fanout_ids = set(identity_by_id) | set(first_index) | set(started)
    result: dict[str, tuple[str, str]] = {}
    for fanout_id in fanout_ids:
        start = started.get(fanout_id)
        trigger = (start.payload or {}).get("trigger_payload") or {} if start else {}
        fence = parent_fence(operations, str(trigger.get("workflow_operation_id") or ""))
        if fence[0]:
            result[fanout_id] = fence
            continue
        terminal = _latest_terminal_for_fanout(
            fanout_id,
            first_index=first_index,
            fanout_runs=fanout_runs,
            terminal_events=terminal_events,
        )
        if terminal is not None:
            result[fanout_id] = (
                f"workflow_run_terminal:{terminal.type}",
                terminal.id,
            )
            continue

        generation_reason = _stale_generation_reason(
            started.get(fanout_id),
            latest_generation=latest_generation,
        )
        if generation_reason[0]:
            result[fanout_id] = generation_reason
            continue

        if fanout_id in rework_fences:
            result[fanout_id] = rework_fences[fanout_id]
            continue

        identity = identity_by_id.get(fanout_id)
        if identity is not None and not bool(identity.get("current")):
            result[fanout_id] = (
                str(identity.get("stale_reason") or "fanout_instance_not_current"),
                str(identity.get("superseded_by") or ""),
            )
            continue
        result[fanout_id] = ("", "")
    return result


def event_log_snapshot_token(event_log: Any) -> tuple[object, ...]:
    """Return an O(1) append token, including active-file rotation."""

    path = event_log.path
    try:
        stat = path.stat()
        active = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
    except OSError:
        active = (0, 0, 0, 0)
    archive_dir = getattr(event_log, "_archive_dir", path.parent / path.stem)
    try:
        archive_stat = archive_dir.stat()
        archive = (archive_stat.st_ino, archive_stat.st_mtime_ns)
    except OSError:
        archive = (0, 0)
    return active + archive


def _active_reader_manifest(manifest: dict[str, Any]) -> bool:
    if str(manifest.get("topology") or "") != "fanout_reader":
        return False
    aggregate = (
        manifest.get("aggregate")
        if isinstance(manifest.get("aggregate"), dict)
        else {}
    )
    return not (
        str(manifest.get("status") or "") in _TERMINAL_FANOUT_STATUSES
        or str(aggregate.get("status") or "") in _TERMINAL_FANOUT_STATUSES
    )


def _superseding_goal_claims(
    events: tuple[ZfEvent, ...],
    *,
    manifests: list[tuple[str, dict[str, Any]]],
) -> dict[str, str]:
    rejected = {
        str((event.payload or {}).get("claim_id") or "").strip()
        for event in events
        if event.type == "run.goal.completion.rejected"
        and isinstance(event.payload, dict)
    }
    claims_by_run: dict[str, list[_GoalClaim]] = {}
    claims_by_goal: dict[str, list[_GoalClaim]] = {}
    for order, event in enumerate(events):
        if event.type != "run.goal.completion.claimed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(payload.get("claim_type") or "") != (
            "admitted_goal_closure_result"
        ):
            continue
        claim_id = str(payload.get("claim_id") or event.id).strip()
        if not claim_id or claim_id in rejected:
            continue
        claim = _GoalClaim(
            order=order,
            claim_id=claim_id,
            run_id=str(
                payload.get("workflow_run_id")
                or payload.get("run_id")
                or event.correlation_id
                or ""
            ).strip(),
            goal_keys=frozenset(
                str(value).strip()
                for value in (
                    payload.get("goal_id"),
                    payload.get("pdd_id"),
                    payload.get("feature_id"),
                )
                if str(value or "").strip()
            ),
        )
        if claim.run_id:
            claims_by_run.setdefault(claim.run_id, []).append(claim)
        for goal_key in claim.goal_keys:
            claims_by_goal.setdefault(goal_key, []).append(claim)

    result: dict[str, str] = {}
    for fanout_id, manifest in manifests:
        run_id = str(
            manifest.get("workflow_run_id")
            or manifest.get("trace_id")
            or ""
        ).strip()
        goal_keys = {
            str(value).strip()
            for value in (
                manifest.get("pdd_id"),
                manifest.get("feature_id"),
            )
            if str(value or "").strip()
        }
        candidates = {
            claim.order: claim
            for claim in claims_by_run.get(run_id, [])
        }
        for goal_key in goal_keys:
            candidates.update({
                claim.order: claim
                for claim in claims_by_goal.get(goal_key, [])
            })
        for claim in sorted(
            candidates.values(),
            key=lambda item: item.order,
            reverse=True,
        ):
            if run_id and claim.run_id and run_id != claim.run_id:
                continue
            if goal_keys and claim.goal_keys and goal_keys.isdisjoint(
                claim.goal_keys
            ):
                continue
            if not (
                (run_id and claim.run_id)
                or (goal_keys and claim.goal_keys)
            ):
                continue
            result[fanout_id] = claim.claim_id
            break
    return result


def _latest_terminal_for_fanout(
    fanout_id: str,
    *,
    first_index: dict[str, int],
    fanout_runs: dict[str, set[str]],
    terminal_events: dict[str, list[tuple[int, ZfEvent]]],
) -> ZfEvent | None:
    start = first_index.get(fanout_id)
    if start is None:
        return None
    candidates = [
        (index, event)
        for run_id in fanout_runs.get(fanout_id, set())
        for index, event in terminal_events.get(run_id, [])
        if index > start
    ]
    return max(candidates, default=(-1, None), key=lambda item: item[0])[1]


def _stale_generation_reason(
    started_event: ZfEvent | None,
    *,
    latest_generation: dict[str, dict[str, str]],
) -> tuple[str, str]:
    if started_event is None:
        return "", ""
    payload = (
        started_event.payload if isinstance(started_event.payload, dict) else {}
    )
    trigger = (
        payload.get("trigger_payload")
        if isinstance(payload.get("trigger_payload"), dict)
        else {}
    )
    if str(trigger.get("schema_version") or "") != (
        CANDIDATE_FREEZE_RECEIPT_SCHEMA
    ):
        return "", ""
    workflow_run_id = str(
        trigger.get("workflow_run_id")
        or started_event.correlation_id
        or payload.get("trace_id")
        or ""
    ).strip()
    claimed_generation = str(trigger.get("generation_id") or "").strip()
    current = latest_generation.get(workflow_run_id, {})
    current_generation = str(current.get("generation_id") or "").strip()
    if (
        workflow_run_id
        and claimed_generation
        and current_generation
        and current_generation != claimed_generation
    ):
        return (
            "candidate_ready_stale_task_pipeline_generation",
            str(current.get("event_id") or ""),
        )
    return "", ""


__all__ = [
    "ReaderFanoutRecoverySnapshot",
    "ReaderOrphanCandidate",
    "build_fanout_currentness",
    "build_reader_fanout_recovery_snapshot",
    "event_log_snapshot_token",
    "reader_recovery_event_may_match",
]
