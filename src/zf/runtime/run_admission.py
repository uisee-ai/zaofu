"""Event-derived Project Run admission and dispatch fences.

The EventLog is the occurrence and ordering authority. This module deliberately
does not persist a mutable queue file: active ownership, FIFO order,
pause/resume, and terminal state are rebuilt from canonical events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.model import ZfEvent
from zf.core.events.payload_schemas import SUCCESS_EVENT_TYPES
from zf.core.state.locks import locked_path
from zf.runtime.run_isolation import (
    concurrent_isolation_blocker as _concurrent_isolation_blocker,
)
from zf.runtime.run_scope import (
    event_run_id as scoped_event_run_id,
    run_aliases,
)


RUN_ADMISSION_SCHEMA_VERSION = "run-admission.v1"
RUN_ADMISSION_EVENT_TYPES = frozenset({
    "run.admission.requested",
    "run.admission.admitted",
    "run.admission.queued",
    "run.admission.released",
    "run.admission.rejected",
})
RUN_TERMINAL_EVENT_TYPES = frozenset({
    "run.goal.completed",
    "run.goal.blocked",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "run.abandoned",
    "workflow.result.available",
})
RUN_ADMISSION_RECONCILE_EVENT_TYPES = frozenset({
    "workflow.invoke.requested",
    "run.admission.rejected",
}) | RUN_TERMINAL_EVENT_TYPES
_COMPLETED_RUN_DUPLICATE_EVENT_TYPES = SUCCESS_EVENT_TYPES | frozenset({
    "fanout.child.completed",
    "impl.child.completed",
    "review.child.completed",
    "task.done.evidence",
    "verify.child.completed",
})
_LEGACY_RUNNING_EVENTS = frozenset({
    "run.started",
    "run.goal.started",
    "workflow.invoke.accepted",
})
_OUTCOME_EVENTS = frozenset({
    "workflow.invoke.accepted",
    "workflow.invoke.rejected",
})


class RunDispatchBlocked(RuntimeError):
    """A deterministic Run fence deferred one provider dispatch."""

    def __init__(self, *, role_name: str, reason: str, run_id: str = "") -> None:
        self.role_name = str(role_name or "")
        self.reason = str(reason or "")
        self.run_id = str(run_id or "")
        super().__init__(f"dispatch to {self.role_name} blocked: {self.reason}")


@dataclass
class RunAdmissionEntry:
    run_id: str
    request_id: str = ""
    status: str = "requested"
    source_event_id: str = ""
    task_id: str = ""
    first_seen_index: int = 0
    queued_index: int | None = None
    admitted_event_id: str = ""
    terminal_event_id: str = ""
    terminal_type: str = ""
    blocker: str = ""
    event_ids: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.status in {"running", "paused"}

    @property
    def terminal(self) -> bool:
        return self.status in {
            "completed",
            "blocked",
            "cancelled",
            "failed",
            "rejected",
            "abandoned",
        }


@dataclass(frozen=True)
class RunAdmissionDecision:
    status: str
    run_id: str
    request_id: str
    source_event_id: str
    queue_position: int = 0
    reason: str = ""
    replayed: bool = False

    @property
    def may_dispatch(self) -> bool:
        return self.status == "admitted"


@dataclass
class RunAdmissionProjection:
    runs: dict[str, RunAdmissionEntry]
    active_run_ids: list[str]
    queued_run_ids: list[str]

    def queue_position(self, run_id: str) -> int:
        try:
            return self.queued_run_ids.index(run_id) + 1
        except ValueError:
            return 0

    def entry_for_request(self, request_id: str) -> RunAdmissionEntry | None:
        request_id = str(request_id or "").strip()
        for entry in self.runs.values():
            if entry.request_id == request_id:
                return entry
        return None


def build_run_admission_projection(
    events: Iterable[ZfEvent],
) -> RunAdmissionProjection:
    """Fold Run admission state in append order."""

    runs: dict[str, RunAdmissionEntry] = {}
    rows = list(events)
    pending_invoke_run_ids = _pending_invoke_run_ids(rows)
    for index, event in enumerate(rows):
        payload = _payload(event)
        run_id = _event_run_id(event)
        if not run_id:
            continue
        relevant = (
            event.type in RUN_ADMISSION_EVENT_TYPES
            or event.type in RUN_TERMINAL_EVENT_TYPES
            or event.type in _LEGACY_RUNNING_EVENTS
            or event.type in {"run.paused", "run.resumed", "run.goal.updated"}
        )
        if not relevant:
            continue
        entry = runs.setdefault(
            run_id,
            RunAdmissionEntry(
                run_id=run_id,
                first_seen_index=index,
            ),
        )
        request_id = str(payload.get("request_id") or "").strip()
        if request_id:
            entry.request_id = request_id
        elif not entry.request_id and event.type in RUN_ADMISSION_EVENT_TYPES:
            entry.request_id = run_id
        source_event_id = str(payload.get("source_event_id") or "").strip()
        if source_event_id:
            entry.source_event_id = source_event_id
        task_id = str(event.task_id or payload.get("task_id") or "").strip()
        if task_id:
            entry.task_id = task_id
        if event.id:
            entry.event_ids.append(event.id)

        if event.type == "run.admission.requested":
            if not entry.terminal:
                # A dedicated admission request supersedes an earlier legacy
                # goal anchor emitted during submit. The Run is not active
                # until an admitted fact exists.
                entry.status = "requested"
        elif event.type == "run.admission.queued":
            if not entry.terminal:
                entry.status = "queued"
                entry.blocker = str(payload.get("reason") or "")
                if entry.queued_index is None:
                    entry.queued_index = index
        elif event.type in {"run.admission.admitted", "run.admission.released"}:
            if not entry.terminal:
                entry.status = "running"
                entry.blocker = ""
                if event.type == "run.admission.admitted":
                    entry.admitted_event_id = event.id
        elif event.type == "run.admission.rejected":
            entry.status = "rejected"
            entry.blocker = str(payload.get("reason") or "")
            entry.terminal_event_id = event.id
            entry.terminal_type = event.type
        elif event.type == "run.paused":
            if not entry.terminal:
                entry.status = "paused"
        elif event.type == "run.resumed":
            if not entry.terminal:
                entry.status = "running"
        elif event.type == "run.goal.updated":
            updated_status = str(payload.get("status") or "").strip()
            if updated_status in {"active", "running"} and (
                not entry.terminal or entry.terminal_type == "run.goal.blocked"
            ):
                entry.status = "running"
                entry.blocker = ""
                entry.terminal_event_id = ""
                entry.terminal_type = ""
        elif event.type in _LEGACY_RUNNING_EVENTS:
            if (
                not entry.terminal
                and not _has_dedicated_admission(entry)
                and run_id not in pending_invoke_run_ids
            ):
                entry.status = "running"
        elif event.type in RUN_TERMINAL_EVENT_TYPES:
            entry.status = _terminal_status(event.type, payload)
            entry.terminal_event_id = event.id
            entry.terminal_type = event.type

    active = sorted(
        (entry for entry in runs.values() if entry.active),
        key=lambda item: item.first_seen_index,
    )
    queued = sorted(
        (
            entry
            for entry in runs.values()
            if entry.status == "queued" and entry.queued_index is not None
        ),
        key=lambda item: (int(item.queued_index or 0), item.first_seen_index),
    )
    return RunAdmissionProjection(
        runs=runs,
        active_run_ids=[entry.run_id for entry in active],
        queued_run_ids=[entry.run_id for entry in queued],
    )


def fold_terminal_run_scope(
    events: Iterable[ZfEvent],
) -> tuple[dict[str, str], set[str]]:
    """Return canonical aliases and the runs that are currently terminal.

    ``run.goal.blocked`` is reversible through an explicit
    ``run.goal.updated(status=active)``. Other terminal outcomes remain
    irreversible. The singleton fallback preserves legacy unscoped events
    without leaking them across concurrent runs.
    """

    rows = list(events)
    aliases = run_aliases(rows)
    known_runs = set(aliases.values())
    singleton = next(iter(known_runs)) if len(known_runs) == 1 else ""
    terminal_types: dict[str, str] = {}
    for event in rows:
        run_id = scoped_event_run_id(event, aliases=aliases) or singleton
        if not run_id:
            continue
        if event.type in RUN_TERMINAL_EVENT_TYPES:
            terminal_types[run_id] = event.type
            continue
        if event.type != "run.goal.updated":
            continue
        payload = _payload(event)
        if (
            str(payload.get("status") or "").strip() in {"active", "running"}
            and terminal_types.get(run_id) == "run.goal.blocked"
        ):
            terminal_types.pop(run_id, None)
    return aliases, set(terminal_types)


def admit_workflow_invoke(
    runtime: Any,
    event: ZfEvent,
) -> RunAdmissionDecision:
    """Atomically admit or queue one canonical workflow invocation."""

    payload = _payload(event)
    run_id = _event_run_id(event)
    request_id = str(payload.get("request_id") or run_id).strip()
    if not run_id:
        return RunAdmissionDecision(
            status="rejected",
            run_id="",
            request_id=request_id,
            source_event_id=event.id,
            reason="workflow invoke requires workflow_run_id or run_id",
        )
    payload.setdefault("workflow_run_id", run_id)
    payload.setdefault("run_id", run_id)
    payload.setdefault("request_id", request_id)

    lock_target = Path(runtime.state_dir) / "locks" / "run-admission"
    with locked_path(lock_target):
        events = runtime.event_log.read_all()
        projection = build_run_admission_projection(events)
        existing = projection.runs.get(run_id)
        if existing is not None:
            if existing.terminal:
                return _decision(existing, projection, replayed=True)
            if existing.status == "paused":
                return _decision(existing, projection, replayed=True)
            if existing.status == "running" and existing.admitted_event_id:
                return RunAdmissionDecision(
                    status="admitted",
                    run_id=run_id,
                    request_id=existing.request_id or request_id,
                    source_event_id=existing.source_event_id or event.id,
                    replayed=True,
                )

        if not _admission_event_for_source(
            events,
            "run.admission.requested",
            event.id,
        ):
            runtime.event_writer.append(ZfEvent(
                type="run.admission.requested",
                actor="orchestrator",
                task_id=event.task_id,
                payload=_admission_payload(
                    event,
                    run_id=run_id,
                    request_id=request_id,
                ),
                causation_id=event.id,
                correlation_id=event.correlation_id or run_id,
            ))
            events = runtime.event_log.read_all()
            projection = build_run_admission_projection(events)

        policy = _policy(runtime)
        active_others = [
            active_id
            for active_id in projection.active_run_ids
            if active_id != run_id
        ]
        blocker = ""
        if len(active_others) >= policy["max_active_runs"]:
            blocker = "project active Run capacity reached"
        elif policy["mode"] == "concurrent" and active_others:
            blocker = concurrent_isolation_blocker(
                runtime,
                event,
                active_run_ids=active_others,
                events=events,
            )
        if blocker:
            if not _admission_event_for_source(
                events,
                "run.admission.queued",
                event.id,
            ):
                runtime.event_writer.append(ZfEvent(
                    type="run.admission.queued",
                    actor="orchestrator",
                    task_id=event.task_id,
                    payload={
                        **_admission_payload(
                            event,
                            run_id=run_id,
                            request_id=request_id,
                        ),
                        "reason": blocker,
                        "active_run_ids": active_others,
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id or run_id,
                ))
            projection = build_run_admission_projection(
                runtime.event_log.read_all()
            )
            entry = projection.runs[run_id]
            return RunAdmissionDecision(
                status="queued",
                run_id=run_id,
                request_id=request_id,
                source_event_id=event.id,
                queue_position=projection.queue_position(run_id),
                reason=blocker,
                replayed=existing is not None,
            )

        was_queued = bool(existing and existing.status == "queued")
        if was_queued and not _admission_event_for_source(
            events,
            "run.admission.released",
            event.id,
        ):
            runtime.event_writer.append(ZfEvent(
                type="run.admission.released",
                actor="orchestrator",
                task_id=event.task_id,
                payload={
                    **_admission_payload(
                        event,
                        run_id=run_id,
                        request_id=request_id,
                    ),
                    "reason": "Run capacity released",
                },
                causation_id=event.id,
                correlation_id=event.correlation_id or run_id,
            ))
        if not _admission_event_for_source(
            runtime.event_log.read_all(),
            "run.admission.admitted",
            event.id,
        ):
            from zf.runtime.workflow_budget_guard import usage_meter_snapshot

            runtime.event_writer.append(ZfEvent(
                type="run.admission.admitted",
                actor="orchestrator",
                task_id=event.task_id,
                payload={
                    **_admission_payload(
                        event,
                        run_id=run_id,
                        request_id=request_id,
                    ),
                    "policy_mode": policy["mode"],
                    "max_active_runs": policy["max_active_runs"],
                    "budget_snapshot": usage_meter_snapshot(runtime),
                    "run_limits": _run_limits_payload(runtime),
                },
                causation_id=event.id,
                correlation_id=event.correlation_id or run_id,
            ))
        return RunAdmissionDecision(
            status="admitted",
            run_id=run_id,
            request_id=request_id,
            source_event_id=event.id,
            replayed=existing is not None,
        )


def _run_limits_payload(runtime: Any) -> dict[str, float | int]:
    limits = getattr(getattr(runtime.config, "workflow", None), "run_limits", None)
    return {
        "timeout_seconds": float(getattr(limits, "timeout_seconds", 0.0) or 0.0),
        "token_budget": int(getattr(limits, "token_budget", 0) or 0),
        "cost_budget_usd": float(getattr(limits, "cost_budget_usd", 0.0) or 0.0),
    }


def reject_workflow_invoke_admission(
    runtime: Any,
    event: ZfEvent,
    *,
    reason: str,
) -> None:
    """Record one idempotent admission rejection for a canonical invoke."""

    run_id = _event_run_id(event)
    if not run_id:
        return
    payload = _payload(event)
    request_id = str(payload.get("request_id") or run_id).strip()
    lock_target = Path(runtime.state_dir) / "locks" / "run-admission"
    with locked_path(lock_target):
        events = runtime.event_log.read_all()
        existing = build_run_admission_projection(events).runs.get(run_id)
        if (
            existing is not None
            and existing.active
            and existing.admitted_event_id
            and existing.source_event_id != event.id
        ):
            # A replan/nested invoke is an operation inside the admitted Run.
            # Rejecting that operation must not terminalize the Run owner.
            return
        if not _admission_event_for_source(
            events,
            "run.admission.requested",
            event.id,
        ):
            runtime.event_writer.append(ZfEvent(
                type="run.admission.requested",
                actor="orchestrator",
                task_id=event.task_id,
                payload=_admission_payload(
                    event,
                    run_id=run_id,
                    request_id=request_id,
                ),
                causation_id=event.id,
                correlation_id=event.correlation_id or run_id,
            ))
            events = runtime.event_log.read_all()
        if _admission_event_for_source(
            events,
            "run.admission.rejected",
            event.id,
        ):
            return
        runtime.event_writer.append(ZfEvent(
            type="run.admission.rejected",
            actor="orchestrator",
            task_id=event.task_id,
            payload={
                **_admission_payload(
                    event,
                    run_id=run_id,
                    request_id=request_id,
                ),
                "reason": str(reason or "workflow invoke rejected"),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id or run_id,
        ))


def reconcile_run_admission(runtime: Any) -> int:
    """Replay eligible queued/crash-gap invokes.

    Serial mode releases at most one queue entry per pass. Concurrent mode
    refills all currently available slots. Each invoked handler performs the
    same locked admission check, so restart and concurrent watcher ticks cannot
    double-admit a source event.
    """

    attempted_sources: set[str] = set()
    replayed = 0
    while True:
        events = runtime.event_log.read_all()
        projection = build_run_admission_projection(events)
        policy = _policy(runtime)
        candidates: list[str] = []
        for run_id in projection.active_run_ids:
            entry = projection.runs[run_id]
            if (
                entry.status == "running"
                and entry.admitted_event_id
                and not _source_has_outcome(events, entry.source_event_id)
            ):
                candidates.append(run_id)
        if len(projection.active_run_ids) < policy["max_active_runs"]:
            candidates.extend(projection.queued_run_ids)

        source = None
        for run_id in dict.fromkeys(candidates):
            entry = projection.runs.get(run_id)
            if (
                entry is None
                or not entry.source_event_id
                or entry.source_event_id in attempted_sources
            ):
                continue
            source = next(
                (
                    event
                    for event in events
                    if event.id == entry.source_event_id
                    and event.type == "workflow.invoke.requested"
                ),
                None,
            )
            if source is not None:
                break
        if source is None:
            return replayed

        attempted_sources.add(source.id)
        runtime._on_workflow_invoke_requested(source)
        replayed += 1
        if policy["mode"] == "serial":
            return replayed


def run_dispatch_block_reason(
    runtime: Any,
    *,
    event: ZfEvent | None = None,
    task: Any = None,
    run_id: str = "",
) -> str:
    """Return a mechanical pause/terminal fence for a prospective dispatch."""

    events = runtime.event_log.read_all()
    candidate = str(run_id or "").strip()
    if not candidate and event is not None:
        candidate = _event_run_id(event)
    if not candidate and task is not None:
        candidate = task_workflow_run_id(task, events=events)
    if not candidate:
        return ""
    projection = build_run_admission_projection(events)
    entry = projection.runs.get(candidate)
    if entry is None:
        return ""
    if entry.status == "paused":
        return "run_paused"
    if entry.terminal:
        return f"run_terminal:{entry.status}"
    if entry.status != "running":
        return f"run_not_admitted:{entry.status}"
    return ""


def record_run_dispatch_blocked(
    runtime: Any,
    *,
    reason: str,
    event: ZfEvent | None = None,
    task: Any = None,
    run_id: str = "",
) -> None:
    """Append one deduplicated audit fact for a fenced dispatch edge."""

    if not reason:
        return
    if event is not None and event.type == "run.dispatch.blocked":
        return
    events = runtime.event_log.read_all()
    candidate = str(run_id or "").strip()
    if not candidate and event is not None:
        candidate = _event_run_id(event)
    if not candidate and task is not None:
        candidate = task_workflow_run_id(task, events=events)
    if not candidate:
        return
    source_event_id = str(event.id if event is not None else "")
    task_id = str(
        getattr(task, "id", "")
        or (event.task_id if event is not None else "")
        or ""
    )
    if any(
        existing.type == "run.dispatch.blocked"
        and str(_payload(existing).get("run_id") or "") == candidate
        and str(_payload(existing).get("source_event_id") or "")
        == source_event_id
        and str(existing.task_id or _payload(existing).get("task_id") or "")
        == task_id
        and str(_payload(existing).get("reason") or "") == reason
        for existing in events
    ):
        return
    runtime.event_writer.append(ZfEvent(
        type="run.dispatch.blocked",
        actor="orchestrator",
        task_id=task_id or None,
        payload={
            "schema_version": RUN_ADMISSION_SCHEMA_VERSION,
            "run_id": candidate,
            "workflow_run_id": candidate,
            "task_id": task_id,
            "source_event_id": source_event_id,
            "reason": reason,
        },
        causation_id=source_event_id or None,
        correlation_id=candidate,
    ))


def reject_late_run_result(
    runtime: Any,
    event: ZfEvent,
) -> str:
    """Fence worker lifecycle results after a Run terminal.

    Returns the rejection reason, or an empty string when the result may
    continue through the ordinary lifecycle gates. Paused Runs may settle
    already-dispatched work; pause only fences new dispatch.
    """

    events = runtime.event_log.read_all()
    payload = _payload(event)
    run_id = str(payload.get("workflow_run_id") or "").strip()
    fanout_id = str(payload.get("fanout_id") or "").strip()
    if not run_id and fanout_id:
        try:
            manifest = runtime._fanout_manifest(fanout_id)
        except Exception:
            manifest = {}
        trigger = (
            manifest.get("trigger_payload")
            if isinstance(manifest, dict)
            and isinstance(manifest.get("trigger_payload"), dict)
            else {}
        )
        run_id = str(
            trigger.get("workflow_run_id")
            or manifest.get("workflow_run_id")
            or ""
        ).strip()
    if not run_id and event.task_id:
        try:
            task = runtime.task_store.get(event.task_id)
        except Exception:
            task = None
        if task is not None:
            run_id = task_workflow_run_id(task, events=events)
    if not run_id:
        return ""
    projection = build_run_admission_projection(events)
    entry = projection.runs.get(run_id)
    if entry is None:
        return ""
    if entry.terminal:
        reason = f"run_terminal:{entry.status}"
    elif entry.status in {"requested", "queued"}:
        reason = f"run_not_admitted:{entry.status}"
    else:
        return ""
    audit_event_type = (
        "run.result.duplicate_suppressed"
        if entry.status == "completed"
        and event.type in _COMPLETED_RUN_DUPLICATE_EVENT_TYPES
        else "run.result.rejected"
    )
    if not any(
        existing.type == audit_event_type
        and str(_payload(existing).get("source_event_id") or "") == event.id
        and str(_payload(existing).get("run_id") or "") == run_id
        for existing in events
    ):
        runtime.event_writer.append(ZfEvent(
            type=audit_event_type,
            actor="orchestrator",
            task_id=event.task_id,
            payload={
                "schema_version": RUN_ADMISSION_SCHEMA_VERSION,
                "run_id": run_id,
                "workflow_run_id": run_id,
                "request_id": entry.request_id,
                "source_event_id": event.id,
                "source_event_type": event.type,
                "reason": reason,
                "terminal_event_id": entry.terminal_event_id,
                "terminal_type": entry.terminal_type,
                "duplicate_terminal_success": (
                    audit_event_type == "run.result.duplicate_suppressed"
                ),
            },
            causation_id=event.id,
            correlation_id=run_id,
        ))
    return reason


def task_workflow_run_id(task: Any, *, events: Iterable[ZfEvent] = ()) -> str:
    contract = getattr(task, "contract", None)
    evidence = (
        getattr(contract, "evidence_contract", {})
        if contract is not None
        else {}
    )
    if isinstance(evidence, dict):
        candidate = str(
            evidence.get("workflow_run_id")
            or evidence.get("run_id")
            or evidence.get("request_id")
            or ""
        ).strip()
        if candidate:
            return candidate
    task_id = str(getattr(task, "id", "") or "").strip()
    if not task_id:
        return ""
    for event in reversed(list(events)):
        payload = _payload(event)
        if str(event.task_id or payload.get("task_id") or "") != task_id:
            continue
        candidate = str(
            payload.get("workflow_run_id")
            or payload.get("project_run_id")
            or ""
        ).strip()
        if candidate:
            return candidate
    return ""


def concurrent_isolation_blocker(
    runtime: Any,
    event: ZfEvent,
    *,
    active_run_ids: list[str],
    events: list[ZfEvent],
) -> str:
    return _concurrent_isolation_blocker(
        runtime,
        event,
        active_run_ids=active_run_ids,
        events=events,
        run_id_for=_event_run_id,
    )


def request_admission_view(
    events: Iterable[ZfEvent],
    *,
    request_id: str,
    run_id: str = "",
    projection: RunAdmissionProjection | None = None,
) -> dict[str, Any]:
    """Small shared list/detail projection for one Project Request."""

    if projection is None:
        projection = build_run_admission_projection(events)
    entry = (
        projection.runs.get(str(run_id or "").strip())
        if run_id
        else None
    )
    if entry is None:
        entry = projection.entry_for_request(request_id)
    if entry is None:
        return {
            "status": "",
            "run_id": str(run_id or ""),
            "queue_position": 0,
            "active": False,
            "terminal": False,
        }
    return {
        "schema_version": RUN_ADMISSION_SCHEMA_VERSION,
        "status": entry.status,
        "run_id": entry.run_id,
        "request_id": entry.request_id,
        "queue_position": projection.queue_position(entry.run_id),
        "active": entry.active,
        "terminal": entry.terminal,
        "blocker": entry.blocker,
        "terminal_type": entry.terminal_type,
        "terminal_event_id": entry.terminal_event_id,
    }


def _policy(runtime: Any) -> dict[str, Any]:
    policy = getattr(
        getattr(getattr(runtime, "config", None), "workflow", None),
        "run_admission",
        None,
    )
    mode = str(getattr(policy, "mode", "serial") or "serial")
    limit = int(getattr(policy, "max_active_runs", 1) or 1)
    return {
        "mode": mode,
        "max_active_runs": 1 if mode == "serial" else max(2, min(limit, 8)),
    }


def _admission_payload(
    event: ZfEvent,
    *,
    run_id: str,
    request_id: str,
) -> dict[str, Any]:
    source = _payload(event)
    return {
        "schema_version": RUN_ADMISSION_SCHEMA_VERSION,
        "run_id": run_id,
        "workflow_run_id": run_id,
        "request_id": request_id,
        "task_id": str(event.task_id or source.get("task_id") or ""),
        "source_event_id": event.id,
        "effective_config_digest": str(
            source.get("effective_config_digest") or ""
        ),
        "run_contract_digest": str(source.get("run_contract_digest") or ""),
    }


def _admission_event_for_source(
    events: Iterable[ZfEvent],
    event_type: str,
    source_event_id: str,
) -> bool:
    return any(
        event.type == event_type
        and str(_payload(event).get("source_event_id") or "") == source_event_id
        for event in events
    )


def _source_has_outcome(events: Iterable[ZfEvent], source_event_id: str) -> bool:
    return any(
        event.type in _OUTCOME_EVENTS
        and str(_payload(event).get("source_event_id") or "") == source_event_id
        for event in events
    )


def _event_run_id(event: ZfEvent) -> str:
    payload = _payload(event)
    candidate = str(
        payload.get("workflow_run_id")
        or payload.get("run_id")
        or ""
    ).strip()
    if candidate:
        return candidate
    if event.type == "workflow.invoke.requested":
        return str(
            payload.get("request_id")
            or event.id
            or ""
        ).strip()
    if event.type in (
        RUN_ADMISSION_EVENT_TYPES
        | RUN_TERMINAL_EVENT_TYPES
        | _LEGACY_RUNNING_EVENTS
        | {"run.paused", "run.resumed"}
    ):
        return str(event.correlation_id or "").strip()
    return ""


def _has_dedicated_admission(entry: RunAdmissionEntry) -> bool:
    return bool(
        entry.admitted_event_id
        or entry.queued_index is not None
        or entry.source_event_id
    )


def _terminal_status(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "run.goal.completed":
        return "completed"
    if event_type == "workflow.result.available":
        return (
            "completed"
            if str(payload.get("result_kind") or "") == "research_report"
            and str(payload.get("status") or "") == "available"
            else "failed"
        )
    if event_type == "run.goal.blocked":
        return "blocked"
    if event_type == "run.cancelled":
        return "cancelled"
    if event_type == "run.abandoned":
        return "abandoned"
    if event_type == "run.failed":
        return "failed"
    status = str(payload.get("status") or "").strip().lower()
    if status in {"passed", "completed", "success", "succeeded"}:
        return "completed"
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    if status in {"blocked"}:
        return "blocked"
    return "failed"


def _decision(
    entry: RunAdmissionEntry,
    projection: RunAdmissionProjection,
    *,
    replayed: bool,
) -> RunAdmissionDecision:
    status = "admitted" if entry.status == "running" else entry.status
    return RunAdmissionDecision(
        status=status,
        run_id=entry.run_id,
        request_id=entry.request_id,
        source_event_id=entry.source_event_id,
        queue_position=projection.queue_position(entry.run_id),
        reason=entry.blocker,
        replayed=replayed,
    )


def _pending_invoke_run_ids(events: list[ZfEvent]) -> set[str]:
    invoke_by_id = {
        event.id: run_id
        for event in events
        if event.type == "workflow.invoke.requested"
        and (run_id := _event_run_id(event))
    }
    pending = set(invoke_by_id.values())
    for event in events:
        if event.type not in _OUTCOME_EVENTS:
            continue
        source_event_id = str(_payload(event).get("source_event_id") or "")
        if source_event_id in invoke_by_id:
            pending.discard(invoke_by_id[source_event_id])
        outcome_run_id = _event_run_id(event)
        if outcome_run_id:
            pending.discard(outcome_run_id)
    return pending


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = [
    "RUN_ADMISSION_EVENT_TYPES",
    "RUN_ADMISSION_RECONCILE_EVENT_TYPES",
    "RUN_ADMISSION_SCHEMA_VERSION",
    "RUN_TERMINAL_EVENT_TYPES",
    "RunAdmissionDecision",
    "RunAdmissionEntry",
    "RunAdmissionProjection",
    "RunDispatchBlocked",
    "admit_workflow_invoke",
    "build_run_admission_projection",
    "concurrent_isolation_blocker",
    "fold_terminal_run_scope",
    "reconcile_run_admission",
    "record_run_dispatch_blocked",
    "reject_late_run_result",
    "reject_workflow_invoke_admission",
    "request_admission_view",
    "run_dispatch_block_reason",
    "task_workflow_run_id",
]
