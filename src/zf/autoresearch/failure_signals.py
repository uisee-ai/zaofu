"""Autoresearch failure signal extraction.

Signals are read-only projections derived from runtime evidence. They are
inputs for bug-candidate backlog export and trigger policy decisions; they do
not mutate kernel truth.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.runtime.event_problem_registry import RUN_MANAGER_PENDING_EVENT_TYPES, spec_for_event
from zf.runtime.fanout_identity import fanout_current_status


_READONLY_GATE_SUCCESS = frozenset({
    "review.approved",
    "verify.passed",
    "test.passed",
    "judge.passed",
})
_TERMINAL_DONE = frozenset({
    "task.done",
    "task.done.accepted",
    "task.archived",
})
_GATE_EVIDENCE = frozenset({
    "task.done.evidence",
    "dev.build.done",
    "review.approved",
    "test.passed",
    "judge.passed",
    "gate.passed",
    "discriminator.passed",
})
_FATAL_TYPES = frozenset({
    "run.failed",
    "orchestrator.dispatch_failed",
    "worker.respawn.failed",
    "worker_stuck.recovery_failed",
    "worker.stuck.recovery_failed",
    "autoresearch.run.failed",
})
_STUCK_TYPES = frozenset({
    "worker.stuck",
    "worker_stuck",
})
_WORKER_ACTIVE_STATES = frozenset({
    "idle",
    "busy",
    "awaiting_review",
    "blocked",
})
_WORKER_NONBUSY_STATES = frozenset({
    "idle",
    "awaiting_review",
    "blocked",
})
_FATAL_RECOVERY_TYPES = frozenset({
    "task.assigned",
    "task.contract.update",
    "task.dispatched",
    "task.rework.requested",
    "task.requeued",
    "task.done",
    "task.done.accepted",
    "task.archived",
})
_TASK_PROGRESS_RECOVERY_TYPES = frozenset({
    "arch.proposal.done",
    "design.critique.done",
    "dev.build.done",
    "static_gate.passed",
    "static_gate.skipped",
    "static_gate.failed",
    "review.approved",
    "review.rejected",
    "test.passed",
    "test.failed",
    "judge.passed",
    "judge.failed",
    "gate.passed",
    "gate.failed",
    "discriminator.passed",
    "discriminator.failed",
    "task.ref.updated",
})

_SUCCESS_HANDOFF_EXPECTATIONS: dict[str, str] = {
    "static_gate.passed": "review",
    "static_gate.skipped": "review",
}
_ROLE_TERMINAL_EVENTS: dict[str, frozenset[str]] = {
    "review": frozenset({"review.approved", "review.rejected"}),
}
_HANDOFF_STALL_GRACE = timedelta(minutes=3)
_DOWNSTREAM_PROGRESS_TYPES = frozenset({
    "task.assigned",
    "task.dispatched",
    "fanout.started",
    "fanout.child.dispatched",
    "fanout.aggregate.completed",
    "candidate.ready",
    "review.approved",
    "review.rejected",
    "test.passed",
    "test.failed",
    "judge.passed",
    "judge.failed",
    "gate.passed",
    "gate.failed",
    "discriminator.passed",
    "discriminator.failed",
    "task.done.evidence",
    "task.ref.updated",
})
_REPLAN_MARKER_NON_PROGRESS_TYPES = frozenset({
    "orchestrator.replan_requested",
    "autoresearch.invocation.requested",
    "autoresearch.invocation.accepted",
    "autoresearch.trigger.accepted",
    "autoresearch.bug_candidate.created",
    "autoresearch.loop.requested",
    "automation.proposal.created",
    "supervisor.decision.recorded",
    "owner.visible_message.requested",
})
_RUN_COMPLETED_REOPEN_EVENTS = frozenset({
    "run.failed",
    "run.goal.started",
    "run.goal.blocked",
    "flow.discovery.failed",
    "flow.goal.blocked",
    "goal.rescan.failed",
    "goal.closure.blocked",
    "module.parity.blocked",
    "verify.failed",
    "test.failed",
    "judge.failed",
    "candidate.quality.failed",
    "integration.failed",
})
_TOOL_NOT_FOUND_MARKERS = (
    "not found",
    "command not found",
    "no such file or directory",
)


def severity_rank(severity: str) -> int:
    order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    return order.get(str(severity or "").lower(), 0)


@dataclass(frozen=True)
class FailureSignal:
    signal_id: str
    source_kind: str
    source_path: str
    event_ids: list[str] = field(default_factory=list)
    fingerprint: str = ""
    category: str = ""
    severity: str = "medium"
    summary: str = ""
    expected: str = ""
    actual: str = ""
    repro_command: str = ""
    evidence_paths: list[str] = field(default_factory=list)
    metric_impacts: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FailureSignal":
        return cls(
            signal_id=str(data.get("signal_id") or ""),
            source_kind=str(data.get("source_kind") or ""),
            source_path=str(data.get("source_path") or ""),
            event_ids=[str(v) for v in data.get("event_ids") or []],
            fingerprint=str(data.get("fingerprint") or ""),
            category=str(data.get("category") or ""),
            severity=str(data.get("severity") or "medium"),
            summary=str(data.get("summary") or ""),
            expected=str(data.get("expected") or ""),
            actual=str(data.get("actual") or ""),
            repro_command=str(data.get("repro_command") or ""),
            evidence_paths=[str(v) for v in data.get("evidence_paths") or []],
            metric_impacts={
                str(k): float(v)
                for k, v in (data.get("metric_impacts") or {}).items()
            },
        )


def _stable_signal_id(*parts: str) -> str:
    raw = "|".join(part for part in parts if part)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"sig-{digest}"


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


def _event_task_id(event: ZfEvent) -> str:
    return str(event.task_id or _payload(event).get("task_id") or "").strip()


def _worker_key(event: ZfEvent) -> str:
    p = _payload(event)
    return str(
        p.get("worker")
        or p.get("instance")
        or p.get("instance_id")
        or event.actor
        or ""
    ).strip()


def _worker_stuck_recovered(
    events: list[ZfEvent],
    *,
    stuck_idx: int,
    stuck_event: ZfEvent,
) -> bool:
    worker = _worker_key(stuck_event)
    if not worker:
        return False
    for event in events[stuck_idx + 1:]:
        if _worker_key(event) != worker:
            continue
        if event.type in {"worker.stuck.recovered", "worker_stuck.recovered"}:
            return True
        if event.type == "worker.heartbeat":
            return True
        if event.type == "worker.state.changed":
            if str(_payload(event).get("to") or "").strip() in _WORKER_ACTIVE_STATES:
                return True
    return False


def _worker_idle_when_stuck(
    events: list[ZfEvent],
    *,
    stuck_idx: int,
    stuck_event: ZfEvent,
) -> bool:
    worker = _worker_key(stuck_event)
    if not worker:
        return False
    for event in reversed(events[:stuck_idx]):
        if _worker_key(event) != worker:
            continue
        if event.type == "worker.heartbeat":
            state = str(_payload(event).get("state") or "").strip().lower()
            return state in _WORKER_NONBUSY_STATES
        if event.type == "worker.state.changed":
            state = str(_payload(event).get("to") or "").strip().lower()
            return state in _WORKER_NONBUSY_STATES
    return False


def _worker_task_terminal_when_stuck(
    events: list[ZfEvent],
    *,
    stuck_idx: int,
    stuck_event: ZfEvent,
) -> bool:
    worker = _worker_key(stuck_event)
    if not worker:
        return False
    current_task_id = ""
    for event in reversed(events[:stuck_idx]):
        if _worker_key(event) == worker:
            payload = _payload(event)
            current_task_id = str(
                payload.get("current_task_id") or payload.get("task_id") or ""
            ).strip()
            if current_task_id:
                break
        if event.type == "task.dispatched":
            payload = _payload(event)
            assignee = str(
                payload.get("assignee") or payload.get("instance_id") or ""
            ).strip()
            if assignee == worker:
                current_task_id = _event_task_id(event)
                if current_task_id:
                    break
    if not current_task_id:
        return False
    return any(
        _event_task_id(event) == current_task_id and _is_terminal_done(event)
        for event in events[:stuck_idx]
    )


def _changed_files(payload: dict[str, Any]) -> list[str]:
    changed = payload.get("changed_files")
    if isinstance(changed, list):
        return [str(item).strip() for item in changed if str(item).strip()]
    if isinstance(changed, str) and changed.strip():
        return [changed.strip()]
    return []


def _event_source(state_dir: Path) -> str:
    return str((state_dir / "events.jsonl").resolve())


def _is_terminal_done(event: ZfEvent) -> bool:
    if event.type in _TERMINAL_DONE:
        return True
    if event.type == "task.status_changed":
        return _payload(event).get("to") == "done"
    return False


def _signal(
    *,
    state_dir: Path,
    source_kind: str,
    fingerprint: str,
    category: str,
    severity: str,
    summary: str,
    expected: str,
    actual: str,
    event_ids: Iterable[str] = (),
    evidence_paths: Iterable[str] = (),
    repro_command: str = "",
    metric_impacts: dict[str, float] | None = None,
    source_path: str | None = None,
) -> FailureSignal:
    ids = [str(event_id) for event_id in event_ids if str(event_id).strip()]
    evidence = [str(path) for path in evidence_paths if str(path).strip()]
    return FailureSignal(
        signal_id=_stable_signal_id(fingerprint, ",".join(ids), source_path or ""),
        source_kind=source_kind,
        source_path=source_path or _event_source(state_dir),
        event_ids=ids,
        fingerprint=fingerprint,
        category=category,
        severity=severity,
        summary=summary,
        expected=expected,
        actual=actual,
        repro_command=repro_command,
        evidence_paths=evidence or [source_path or _event_source(state_dir)],
        metric_impacts=dict(metric_impacts or {}),
    )


def _read_events(state_dir: Path) -> list[ZfEvent]:
    try:
        return EventLog(state_dir / "events.jsonl").read_all()
    except Exception:
        return []


def detect_readonly_gate_mutations(
    events: list[ZfEvent],
    *,
    state_dir: Path,
) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    for event in events:
        if event.type not in _READONLY_GATE_SUCCESS:
            continue
        changed = _changed_files(_payload(event))
        if not changed:
            continue
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="event_log",
            fingerprint=f"readonly_gate_mutation:{event.type}:{','.join(changed)}",
            category="evaluator_drift",
            severity="high",
            summary=f"{event.type} carried changed_files in a read-only gate",
            expected="review/test/judge success events must be read-only evidence",
            actual=f"changed_files={changed}",
            event_ids=[event.id],
            metric_impacts={"instrument_score": -0.4, "eval_strength": -0.3},
        ))
    return signals


def detect_worker_stuck(events: list[ZfEvent], *, state_dir: Path) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    for idx, event in enumerate(events):
        if event.type not in _STUCK_TYPES and event.type not in {
            "worker.stuck.recovery_failed",
            "worker_stuck.recovery_failed",
        }:
            continue
        worker = _worker_key(event)
        if event.type in _STUCK_TYPES and _worker_stuck_recovered(
            events,
            stuck_idx=idx,
            stuck_event=event,
        ):
            continue
        if event.type in _STUCK_TYPES and _worker_idle_when_stuck(
            events,
            stuck_idx=idx,
            stuck_event=event,
        ):
            continue
        if event.type in _STUCK_TYPES and _worker_task_terminal_when_stuck(
            events,
            stuck_idx=idx,
            stuck_event=event,
        ):
            continue
        severity = "critical" if "failed" in event.type else "high"
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="event_log",
            fingerprint=f"worker_stuck:{worker or event.task_id or event.id}",
            category="worker_stuck",
            severity=severity,
            summary="Worker stuck or stuck recovery failed",
            expected="stuck worker is recovered or escalated with bounded evidence",
            actual=f"{event.type} worker={worker or 'unknown'}",
            event_ids=[event.id],
            metric_impacts={"runtime_reliability": -0.3},
        ))
    return signals


def _fatal_recovered(
    events: list[ZfEvent],
    *,
    fatal_idx: int,
    fatal_event: ZfEvent,
) -> bool:
    task_id = _event_task_id(fatal_event)
    if not task_id:
        return False
    for event in events[fatal_idx + 1:]:
        if _event_task_id(event) != task_id:
            continue
        if event.type in _FATAL_RECOVERY_TYPES | _TASK_PROGRESS_RECOVERY_TYPES:
            return True
        if event.type == "task.status_changed" and _payload(event).get("to") in {
            "backlog",
            "blocked",
            "done",
        }:
            return True
    return False


def detect_fatal_events(events: list[ZfEvent], *, state_dir: Path) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    for idx, event in enumerate(events):
        if event.type not in _FATAL_TYPES:
            continue
        if _fatal_recovered(events, fatal_idx=idx, fatal_event=event):
            continue
        p = _payload(event)
        reason = str(p.get("reason") or p.get("error") or event.type)
        dead_reason = str(p.get("dead_reason") or "").strip()
        is_pane_dead = (
            event.type == "orchestrator.dispatch_failed"
            and (
                dead_reason == "pane_dead"
                or "pane is not running an agent process" in reason
                or "reason=pane_dead" in reason
            )
        )
        if is_pane_dead:
            role = str(p.get("assignee") or p.get("role") or "unknown").strip()
            signals.append(_signal(
                state_dir=state_dir,
                source_kind="event_log",
                fingerprint=(
                    f"pane_dead_dispatch:{event.type}:{role}:{reason[:80]}"
                ),
                category="orchestrator_pane_dead",
                severity="critical",
                summary=(
                    "Dispatch failed because the target pane was not running "
                    "an agent process"
                ),
                expected=(
                    "supervisor/autoresearch respawns the pane and retries "
                    "the same pending dispatch once"
                ),
                actual=reason,
                event_ids=[event.id],
                repro_command=(
                    "uv run zf recover workflow --apply "
                    "# then verify the target role pane is respawned and "
                    "the pending dispatch is retried"
                ),
                metric_impacts={"runtime_reliability": -0.5},
            ))
            continue
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="event_log",
            fingerprint=f"fatal:{event.type}:{reason[:80]}",
            category="runtime_fatal",
            severity="critical" if event.type == "orchestrator.dispatch_failed" else "high",
            summary=f"Runtime fatal event observed: {event.type}",
            expected="runtime either completes or routes bounded recovery",
            actual=reason,
            event_ids=[event.id],
            metric_impacts={"runtime_reliability": -0.4},
        ))
    return signals


def detect_semantic_flow_failures(
    events: list[ZfEvent],
    *,
    state_dir: Path,
) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    for idx, event in enumerate(events):
        spec = spec_for_event(event.type)
        if spec is None:
            continue
        if event.type not in RUN_MANAGER_PENDING_EVENT_TYPES:
            continue
        if spec.event_class != "expected_negative":
            continue
        if spec.problem_class not in {"artifact_contract", "product_gap"}:
            continue
        if _semantic_event_recovered(events, event_idx=idx, source_event=event):
            continue
        payload = _payload(event)
        reason = str(
            payload.get("reason")
            or payload.get("error")
            or payload.get("summary")
            or spec.title
            or event.type
        )
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="event_log",
            fingerprint=f"semantic-flow:{spec.failure_class}:{_semantic_scope(event, payload)}",
            category=spec.failure_class,
            severity=spec.severity or "high",
            summary=f"{spec.title or event.type}: {reason}",
            expected=(
                "semantic failure is followed by a gap plan, closed goal, "
                "or bounded recovery owner"
            ),
            actual=reason,
            event_ids=[event.id],
            repro_command=(
                "uv run zf run-manager tick "
                "# verify a pending action or autoresearch trigger is produced"
            ),
            metric_impacts={"goal_convergence": -0.35},
        ))
    return signals


def _semantic_event_recovered(
    events: list[ZfEvent],
    *,
    event_idx: int,
    source_event: ZfEvent,
) -> bool:
    source_payload = _payload(source_event)
    source_keys = _runtime_context_keys(source_event, source_payload)
    for event in events[event_idx + 1:]:
        if event.type in {
            "flow.gap_plan.ready",
            "goal.gap_plan.ready",
            "gap_plan.ready",
            "task_map.amended",
            "task_map.ready",
            "flow.discovery.completed",
            "goal.rescan.completed",
            "module.parity.scan.completed",
            "cangjie.module.parity.scan.completed",
            "flow.goal.closed",
            "goal.closure.closed",
            "module.parity.closed",
            "workflow.resume.applied",
        }:
            payload = _payload(event)
            success_keys = _runtime_context_keys(event, payload)
            if not source_keys or not success_keys or source_keys & success_keys:
                return True
    return False


def _semantic_scope(event: ZfEvent, payload: dict[str, Any]) -> str:
    for key in (
        "trace_id",
        "pdd_id",
        "feature_id",
        "target_ref",
        "candidate_ref",
        "task_id",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    if event.correlation_id:
        return f"trace_id:{event.correlation_id}"
    if event.task_id:
        return f"task_id:{event.task_id}"
    return event.id or event.type


def detect_fanout_failures(
    events: list[ZfEvent],
    *,
    state_dir: Path,
) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    aggregate_by_fanout: dict[str, dict[str, Any]] = {}
    context_by_fanout: dict[str, set[str]] = {}
    terminal_success_keys = _terminal_success_keys(events)
    terminal_fanouts: set[str] = set()
    terminal_children: set[tuple[str, str]] = set()
    dispatched_children: set[tuple[str, str]] = set()

    for event in events:
        payload = _payload(event)
        fanout_id = str(payload.get("fanout_id") or "").strip()
        child_id = str(payload.get("child_id") or payload.get("child_run") or "").strip()
        if fanout_id:
            context_by_fanout.setdefault(fanout_id, set()).update(
                _runtime_context_keys(event, payload)
            )
        if event.type == "fanout.started" and fanout_id:
            aggregate = payload.get("aggregate")
            if isinstance(aggregate, dict):
                aggregate_by_fanout[fanout_id] = aggregate
        if event.type == "fanout.aggregate.completed" and fanout_id:
            status = str(payload.get("status") or "").strip()
            if status == "completed":
                terminal_fanouts.add(fanout_id)
        if event.type == "fanout.cancelled" and fanout_id:
            terminal_fanouts.add(fanout_id)
        if event.type == "fanout.child.dispatched" and fanout_id and child_id:
            dispatched_children.add((fanout_id, child_id))
        if event.type in {"fanout.child.completed", "fanout.child.failed"} and fanout_id and child_id:
            terminal_children.add((fanout_id, child_id))

    for idx, event in enumerate(events):
        payload = _payload(event)
        fanout_id = str(payload.get("fanout_id") or "").strip()
        child_id = str(payload.get("child_id") or payload.get("child_run") or "").strip()
        if fanout_id and _fanout_is_stale_or_terminal(
            events=events,
            fanout_id=fanout_id,
            context_keys=context_by_fanout.get(fanout_id, set()),
            terminal_success_keys=terminal_success_keys,
        ):
            continue
        if event.type == "fanout.child.failed":
            reason = str(payload.get("reason") or payload.get("error") or "").strip()
            if reason == "stale_task_map":
                if _stale_task_map_recovered(
                    events,
                    failed_idx=idx,
                    failed_event=event,
                ):
                    continue
                pdd_id = str(payload.get("pdd_id") or "").strip()
                stale_task_ids = [
                    str(task_id).strip()
                    for task_id in payload.get("stale_task_ids") or []
                    if str(task_id).strip()
                ]
                stale_key = ",".join(stale_task_ids) or child_id
                signals.append(_signal(
                    state_dir=state_dir,
                    source_kind="event_log",
                    fingerprint=(
                        f"stale_task_map_writer_fanout:"
                        f"{pdd_id or fanout_id}:{stale_key}"
                    ),
                    category="fanout_runtime_failure",
                    severity="high",
                    summary="Writer fanout child failed with stale task-map",
                    expected=(
                        "stale writer fanout completion triggers bounded "
                        "candidate rework using the latest task_map.ready payload"
                    ),
                    actual=(
                        f"fanout.child.failed reason=stale_task_map "
                        f"fanout={fanout_id} child={child_id} stale={stale_task_ids}"
                    ),
                    event_ids=[event.id],
                    repro_command=(
                        "zf events --last 120"
                        + (f" | rg {pdd_id}" if pdd_id else "")
                    ),
                    metric_impacts={
                        "runtime_reliability": -0.35,
                        "loop_progress": -0.3,
                    },
                ))
                continue
        if event.type == "fanout.timed_out" and fanout_id:
            pending = payload.get("pending_children")
            pending_children = [
                str(child).strip()
                for child in pending
                if str(child).strip()
            ] if isinstance(pending, list) else []
            signals.append(_signal(
                state_dir=state_dir,
                source_kind="event_log",
                fingerprint=f"fanout_timed_out:{fanout_id}:{','.join(pending_children)}",
                category="fanout_runtime_failure",
                severity="high",
                summary="Fanout timed out before all children reached terminal state",
                expected="fanout child dispatches either complete, fail, retry, or escalate with bounded evidence",
                actual=f"fanout.timed_out fanout={fanout_id} pending={pending_children}",
                event_ids=[event.id],
                metric_impacts={"runtime_reliability": -0.35},
            ))
            continue

        if not fanout_id or not child_id or event.type.startswith("fanout."):
            continue
        aggregate = aggregate_by_fanout.get(fanout_id, {})
        aggregate_events = {
            str(aggregate.get("success_event") or ""),
            str(aggregate.get("failure_event") or ""),
        }
        aggregate_events.discard("")
        if event.type not in aggregate_events:
            continue
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="event_log",
            fingerprint=f"fanout_child_emitted_aggregate:{fanout_id}:{child_id}:{event.type}",
            category="fanout_event_contract",
            severity="high",
            summary="Fanout child emitted aggregate event directly",
            expected=(
                "child workers emit child_success_event/child_failure_event; "
                "kernel or synth publishes aggregate success/failure"
            ),
            actual=f"{event.type} carried fanout child_id={child_id}",
            event_ids=[event.id],
            metric_impacts={"runtime_reliability": -0.3, "control_plane_safety": -0.2},
        ))

    for fanout_id, child_id in sorted(dispatched_children - terminal_children):
        if fanout_id in terminal_fanouts:
            continue
        if not _fanout_child_pending_grace_expired(
            events,
            fanout_id=fanout_id,
            child_id=child_id,
        ):
            continue
        if _fanout_is_stale_or_terminal(
            events=events,
            fanout_id=fanout_id,
            context_keys=context_by_fanout.get(fanout_id, set()),
            terminal_success_keys=terminal_success_keys,
        ):
            continue
        if any(
            event.type == "fanout.timed_out"
            and _payload(event).get("fanout_id") == fanout_id
            for event in events
        ):
            continue
        # Without wall-clock context this is a low-confidence observability
        # signal; autoresearch can still surface it when a run archive ends with
        # pending fanout children.
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="event_log",
            fingerprint=f"fanout_child_pending:{fanout_id}:{child_id}",
            category="fanout_runtime_pending",
            severity="medium",
            summary="Fanout child dispatched without a terminal child event",
            expected="each fanout.child.dispatched is followed by fanout.child.completed or fanout.child.failed",
            actual=f"pending fanout child fanout={fanout_id} child={child_id}",
            event_ids=[],
            metric_impacts={"runtime_reliability": -0.15},
        ))
    return signals


def _fanout_child_pending_grace_expired(
    events: list[ZfEvent],
    *,
    fanout_id: str,
    child_id: str,
    grace_seconds: int = 120,
) -> bool:
    dispatch_ts: datetime | None = None
    latest_ts: datetime | None = None
    role_instance = ""
    for event in events:
        event_ts = _parse_event_ts(event)
        if event_ts is not None:
            latest_ts = event_ts if latest_ts is None else max(latest_ts, event_ts)
        payload = _payload(event)
        if (
            event.type == "fanout.child.dispatched"
            and str(payload.get("fanout_id") or "") == fanout_id
            and str(payload.get("child_id") or payload.get("child_run") or "") == child_id
        ):
            dispatch_ts = event_ts
            role_instance = str(payload.get("role_instance") or "")
    if dispatch_ts is None or latest_ts is None:
        return True
    # 2026-07-08 live 三轮实锚:verify/judge 子任务健康跑 3-6 分钟,纯按
    # 派发时长判停滞每轮必产假候选(→ proposal → escalate 噪音)。宽限从
    # "该 worker 最后一次可见活动"起算——agent.usage / codex.hook.* /
    # worker.* 都是它 actor 发的;静默超宽限才是真停滞。
    last_seen = dispatch_ts
    if role_instance:
        for event in events:
            if str(getattr(event, "actor", "") or "") != role_instance:
                continue
            event_ts = _parse_event_ts(event)
            if event_ts is not None and event_ts > last_seen:
                last_seen = event_ts
    return (latest_ts - last_seen).total_seconds() >= grace_seconds


def _parse_event_ts(event: ZfEvent) -> datetime | None:
    raw = str(getattr(event, "ts", "") or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fanout_is_stale_or_terminal(
    *,
    events: list[ZfEvent],
    fanout_id: str,
    context_keys: set[str],
    terminal_success_keys: set[str],
) -> bool:
    status = fanout_current_status(events, fanout_id)
    if status.known and not status.current:
        return True
    return bool(context_keys & terminal_success_keys)


def _terminal_success_keys(events: list[ZfEvent]) -> set[str]:
    keys: set[str] = set()
    for event in events:
        if event.type not in _READONLY_GATE_SUCCESS:
            continue
        keys.update(_runtime_context_keys(event, _payload(event)))
    return keys


def _runtime_context_keys(event: ZfEvent, payload: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for name in (
        "trace_id",
        "pdd_id",
        "feature_id",
        "task_map_ref",
        "candidate_ref",
        "target_ref",
    ):
        value = str(payload.get(name) or "").strip()
        if value:
            keys.add(f"{name}:{value}")
    if event.correlation_id:
        keys.add(f"trace_id:{event.correlation_id}")
    return keys


def detect_replan_followthrough_gaps(
    events: list[ZfEvent],
    *,
    state_dir: Path,
) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    for idx, event in enumerate(events):
        if event.type != "orchestrator.replan_requested":
            continue
        if _replan_marker_has_downstream(
            events,
            marker_idx=idx,
            marker_event=event,
        ):
            continue
        payload = _payload(event)
        pdd_id = str(payload.get("pdd_id") or "").strip()
        rework_of = str(payload.get("rework_of") or "").strip()
        rework_source = str(payload.get("rework_source") or "").strip()
        classification = str(payload.get("classification") or "").strip()
        key = rework_of or event.id
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="event_log",
            fingerprint=f"replan_followthrough_missing:{pdd_id}:{key}",
            category="replan_followthrough_missing",
            severity="high",
            summary="Replan marker has no downstream synth/task-map progress",
            expected=(
                "orchestrator.replan_requested is followed by plan synth "
                "trigger/fanout, task_map.ready, or human escalation"
            ),
            actual=(
                f"orchestrator.replan_requested rework_of={rework_of} "
                f"source={rework_source} classification={classification} "
                "without downstream progress"
            ),
            event_ids=[event.id],
            repro_command=(
                "zf events --last 120"
                + (f" | rg {pdd_id}" if pdd_id else "")
            ),
            metric_impacts={
                "runtime_reliability": -0.3,
                "loop_progress": -0.3,
            },
        ))
    return signals


def _stale_task_map_recovered(
    events: list[ZfEvent],
    *,
    failed_idx: int,
    failed_event: ZfEvent,
) -> bool:
    failed_payload = _payload(failed_event)
    pdd_id = str(failed_payload.get("pdd_id") or "").strip()
    for marker_idx, event in enumerate(events[failed_idx + 1:], start=failed_idx + 1):
        payload = _payload(event)
        if str(payload.get("rework_of") or "").strip() == failed_event.id:
            if event.type in {"task_map.ready", "human.escalate"}:
                return True
            if event.type == "orchestrator.replan_requested":
                return _replan_marker_has_downstream(
                    events,
                    marker_idx=marker_idx,
                    marker_event=event,
                )
            if _is_replan_downstream_event(
                marker_source_id=failed_event.id,
                marker_pdd_id=pdd_id,
                marker_trace_id="",
                event=event,
            ):
                return True
        if (
            pdd_id
            and event.type == "task_map.ready"
            and str(payload.get("pdd_id") or "").strip() == pdd_id
            and str(payload.get("rework_source") or "").strip()
            == "fanout.child.failed"
        ):
            return True
    return False


def _replan_marker_has_downstream(
    events: list[ZfEvent],
    *,
    marker_idx: int,
    marker_event: ZfEvent,
) -> bool:
    payload = _payload(marker_event)
    marker_source_id = str(payload.get("rework_of") or "").strip()
    marker_pdd_id = str(payload.get("pdd_id") or "").strip()
    marker_trace_id = str(
        payload.get("trace_id") or marker_event.correlation_id or ""
    ).strip()
    for event in events[marker_idx + 1:]:
        if _is_replan_downstream_event(
            marker_source_id=marker_source_id,
            marker_pdd_id=marker_pdd_id,
            marker_trace_id=marker_trace_id,
            event=event,
        ):
            return True
    return False


def _is_replan_downstream_event(
    *,
    marker_source_id: str,
    marker_pdd_id: str,
    marker_trace_id: str,
    event: ZfEvent,
) -> bool:
    payload = _payload(event)
    event_rework_of = _payload_rework_of(payload)
    same_rework = bool(marker_source_id and event_rework_of == marker_source_id)
    event_pdd_id = str(payload.get("pdd_id") or payload.get("feature_id") or "").strip()
    same_pdd = bool(marker_pdd_id and event_pdd_id == marker_pdd_id)
    event_trace_id = str(payload.get("trace_id") or event.correlation_id or "").strip()
    same_trace = bool(marker_trace_id and event_trace_id == marker_trace_id)

    if event.type == "human.escalate" and (same_rework or same_pdd or same_trace):
        return True
    if event.type in {"task_map.ready", "zaofu.refactor.plan.ready"}:
        return same_rework or same_pdd or same_trace
    if event.type == "fanout.started":
        return same_rework or same_trace
    if not same_rework:
        return False
    if event.type in _REPLAN_MARKER_NON_PROGRESS_TYPES:
        return False
    if event.type.startswith(("autoresearch.", "supervisor.", "owner.")):
        return False
    return True


def _payload_rework_of(payload: dict[str, Any]) -> str:
    direct = str(payload.get("rework_of") or "").strip()
    if direct:
        return direct
    trigger_payload = payload.get("trigger_payload")
    if isinstance(trigger_payload, dict):
        return str(trigger_payload.get("rework_of") or "").strip()
    return ""


def _task_ref_rejection_recovered(
    events: list[ZfEvent],
    *,
    rejected_idx: int,
    rejected_event: ZfEvent,
) -> bool:
    task_id = _event_task_id(rejected_event)
    if not task_id:
        return False
    rejected_payload = _payload(rejected_event)
    trigger_event_id = str(rejected_payload.get("trigger_event_id") or "").strip()
    for event in events[rejected_idx + 1:]:
        if _event_task_id(event) != task_id:
            continue
        payload = _payload(event)
        if event.type == "task.ref.updated":
            updated_trigger = str(payload.get("trigger_event_id") or "").strip()
            if not trigger_event_id or updated_trigger == trigger_event_id:
                return True
            return True
        if event.type in {
            "task.contract.update",
            "task.dispatched",
            "task.rework.requested",
            "task.requeued",
        } or _is_terminal_done(event):
            return True
    return False


def _missing_task_ref_fanout_recovered(
    events: list[ZfEvent],
    *,
    failed_idx: int,
    failed_event: ZfEvent,
) -> bool:
    payload = _payload(failed_event)
    fanout_id = str(payload.get("fanout_id") or "").strip()
    child_id = str(payload.get("child_id") or payload.get("child_run") or "").strip()
    task_id = _event_task_id(failed_event)
    for event in events[failed_idx + 1:]:
        event_payload = _payload(event)
        if (
            fanout_id
            and child_id
            and str(event_payload.get("fanout_id") or "").strip() == fanout_id
            and str(
                event_payload.get("child_id")
                or event_payload.get("child_run")
                or ""
            ).strip() == child_id
            and event.type == "fanout.child.completed"
        ):
            return True
        if fanout_id and event.type == "fanout.aggregate.completed":
            if str(event_payload.get("fanout_id") or "").strip() == fanout_id:
                return True
        if task_id and _event_task_id(event) == task_id:
            if event.type == "task.ref.updated" or _is_terminal_done(event):
                return True
    return False


def detect_task_ref_handoff_deadends(
    events: list[ZfEvent],
    *,
    state_dir: Path,
) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    for idx, event in enumerate(events):
        payload = _payload(event)
        task_id = _event_task_id(event) or "unknown"
        if event.type == "task.ref.rejected":
            if _task_ref_rejection_recovered(
                events,
                rejected_idx=idx,
                rejected_event=event,
            ):
                continue
            reason = str(payload.get("reason") or "task ref rejected").strip()
            trigger_event_id = str(payload.get("trigger_event_id") or "").strip()
            signals.append(_signal(
                state_dir=state_dir,
                source_kind="event_log",
                fingerprint=(
                    f"task_ref_rejected:{task_id}:"
                    f"{trigger_event_id or event.id}:{reason[:80]}"
                ),
                category="task_ref_handoff_deadend",
                severity="high",
                summary="Task ref handoff was rejected after writer completion",
                expected=(
                    "dev.build.done either creates task.ref.updated or routes "
                    "bounded rework before fanout/review progression stalls"
                ),
                actual=reason,
                event_ids=[event.id],
                repro_command=f"zf events --task {task_id} --last 80",
                metric_impacts={
                    "runtime_reliability": -0.4,
                    "loop_progress": -0.35,
                },
            ))
            continue

        if event.type != "fanout.child.failed":
            continue
        reason = str(payload.get("reason") or payload.get("error") or "").strip()
        if "missing task ref after dev.build.done" not in reason:
            continue
        if _missing_task_ref_fanout_recovered(
            events,
            failed_idx=idx,
            failed_event=event,
        ):
            continue
        fanout_id = str(payload.get("fanout_id") or "").strip()
        child_id = str(payload.get("child_id") or payload.get("child_run") or "").strip()
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="event_log",
            fingerprint=(
                "missing_task_ref_after_dev_build_done:"
                f"{task_id}:{fanout_id}:{child_id}"
            ),
            category="task_ref_handoff_deadend",
            severity="high",
            summary="Writer fanout child failed because task ref was missing",
            expected=(
                "writer completion creates a replayable task ref before fanout "
                "marks the child terminal"
            ),
            actual=reason,
            event_ids=[event.id],
            repro_command=f"zf events --task {task_id} --last 80",
            metric_impacts={
                "runtime_reliability": -0.4,
                "loop_progress": -0.35,
            },
        ))
    return signals


def detect_dispatch_preflight_blockers(
    events: list[ZfEvent],
    *,
    state_dir: Path,
) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    by_task: dict[str, list[tuple[int, ZfEvent]]] = {}
    for idx, event in enumerate(events):
        if event.task_id:
            by_task.setdefault(event.task_id, []).append((idx, event))

    recovered_types = {
        "task.contract.update",
        "task.dispatched",
        "task.requeued",
        "task.done",
        "task.done.accepted",
        "task.archived",
    }
    for task_id, task_events in by_task.items():
        latest_invalid: tuple[int, ZfEvent] | None = None
        for idx, event in task_events:
            if event.type != "task.contract.invalid":
                continue
            payload = _payload(event)
            if payload.get("source") != "dispatch_preflight":
                continue
            latest_invalid = (idx, event)
        if latest_invalid is None:
            continue
        invalid_idx, invalid_event = latest_invalid
        recovered = False
        for idx, event in task_events:
            if idx <= invalid_idx:
                continue
            if event.type in recovered_types:
                recovered = True
                break
            if event.type == "task.status_changed" and _payload(event).get("to") in {
                "backlog",
                "blocked",
                "done",
            }:
                recovered = True
                break
        if recovered:
            continue

        payload = _payload(invalid_event)
        errors = [
            str(item)
            for item in payload.get("errors", [])
            if str(item).strip()
        ]
        role = str(payload.get("role") or payload.get("assignee") or "")
        summary = "Dispatch preflight blocked a task without recovery"
        if role:
            summary = f"{summary}: role={role}"
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="event_log",
            fingerprint=(
                f"dispatch_preflight_blocker:{task_id}:{role}:"
                f"{'|'.join(errors)[:120]}"
            ),
            category="dispatch_preflight_blocker",
            severity="high",
            summary=summary,
            expected=(
                "dispatch preflight either passes or is followed by "
                "task.contract.update/requeue/dispatch recovery"
            ),
            actual="; ".join(errors) or "task.contract.invalid",
            event_ids=[invalid_event.id],
            repro_command=f"zf events --task {task_id} --last 40",
            metric_impacts={
                "runtime_reliability": -0.35,
                "control_plane_safety": -0.25,
            },
        ))
    return signals


def _role_matches(value: Any, role: str) -> bool:
    text = str(value or "").strip()
    return text == role or text.startswith(f"{role}-")


def _event_matches_role(event: ZfEvent, role: str) -> bool:
    payload = _payload(event)
    return (
        _role_matches(payload.get("role"), role)
        or _role_matches(payload.get("assignee"), role)
        or _role_matches(payload.get("instance_id"), role)
        or _role_matches(event.actor, role)
    )


def _passed_equivalent_handoff(event: ZfEvent) -> bool:
    if event.type == "static_gate.skipped":
        payload = _payload(event)
        return payload.get("skipped") is True and payload.get("passed") is True
    return event.type in _SUCCESS_HANDOFF_EXPECTATIONS


def _handoff_recovered(
    events: list[ZfEvent],
    *,
    event_idx: int,
    task_id: str,
    target_role: str,
) -> bool:
    terminal_types = _ROLE_TERMINAL_EVENTS.get(target_role, frozenset())
    for event in events[event_idx + 1:]:
        if _event_task_id(event) != task_id:
            continue
        if event.type in {"task.assigned", "task.dispatched"} and (
            _event_matches_role(event, target_role)
        ):
            return True
        if event.type in terminal_types:
            return True
        if event.type in {
            "task.contract.update",
            "task.rework.requested",
            "task.requeued",
        } or _is_terminal_done(event):
            return True
        if event.type in {
            "task.rework.requested",
            "task.requeued",
            "orchestrator.dispatch_failed",
        }:
            return True
    return False


def _downstream_progress_recovered(
    events: list[ZfEvent],
    *,
    event_idx: int,
    task_id: str,
    trigger_type: str,
) -> bool:
    """Return true when the task made later stage progress after a handoff event."""

    for event in events[event_idx + 1:]:
        if _event_task_id(event) != task_id:
            continue
        if event.type == trigger_type:
            continue
        if event.type in _DOWNSTREAM_PROGRESS_TYPES:
            return True
        if event.type in _TASK_PROGRESS_RECOVERY_TYPES and event.type != trigger_type:
            return True
        if _is_terminal_done(event):
            return True
    return False


def _event_age(event: ZfEvent, *, now: datetime) -> timedelta | None:
    raw = str(event.ts or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return now - parsed.astimezone(timezone.utc)


def _handoff_still_in_grace(event: ZfEvent, *, now: datetime) -> bool:
    age = _event_age(event, now=now)
    return age is not None and timedelta(0) <= age < _HANDOFF_STALL_GRACE


def detect_success_handoff_stalls(
    events: list[ZfEvent],
    *,
    state_dir: Path,
    now: datetime | None = None,
) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    current = now or datetime.now(timezone.utc)
    # In a candidate-integration flow, a writer's per-task static_gate.passed
    # hands off to review via the candidate aggregate (all slices integrate →
    # candidate.ready → review reviews the CANDIDATE, never per-task). The
    # per-task static_gate→review handoff expectation is then inapplicable, so it
    # must not fire (R18: 5× false handoff_stall during the integration window).
    candidate_flow = any(
        e.type in {"task_map.ready", "candidate.ready"} for e in events
    )
    for idx, event in enumerate(events):
        task_id = _event_task_id(event)
        default_target_role = _SUCCESS_HANDOFF_EXPECTATIONS.get(event.type)
        if not task_id or not default_target_role:
            continue
        if candidate_flow:
            continue
        if not _passed_equivalent_handoff(event):
            continue
        if _handoff_still_in_grace(event, now=current):
            continue
        target_role = (
            _observed_handoff_target_role(events, event_idx=idx, task_id=task_id)
            or default_target_role
        )
        if _downstream_progress_recovered(
            events,
            event_idx=idx,
            task_id=task_id,
            trigger_type=event.type,
        ):
            continue
        if _handoff_recovered(
            events,
            event_idx=idx,
            task_id=task_id,
            target_role=target_role,
        ):
            continue
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="event_log",
            fingerprint=f"handoff_stall:{task_id}:{event.type}:{target_role}",
            category="handoff_stall",
            severity="high",
            summary=(
                f"{event.type} did not hand off task {task_id} to "
                f"{target_role}"
            ),
            expected=(
                f"{event.type} is followed by task.assigned/task.dispatched "
                f"to {target_role} or by {target_role} terminal evidence"
            ),
            actual=f"no later {target_role} assignment/dispatch/evidence event",
            event_ids=[event.id],
            repro_command=f"zf events --task {task_id} --last 80",
            metric_impacts={
                "runtime_reliability": -0.35,
                "loop_progress": -0.3,
            },
        ))
    return signals


def _observed_handoff_target_role(
    events: list[ZfEvent],
    *,
    event_idx: int,
    task_id: str,
) -> str:
    """Return the actual downstream role if the event stream shows one.

    Static gate success is not universally followed by `review`: mini profiles
    can hand off to `qa`, and custom zf.yaml files can choose other gate roles.
    The failure signal extractor has no config object, so it should prefer the
    observed kernel handoff before falling back to the historical strict
    `review` expectation.
    """
    for later in events[event_idx + 1:]:
        if _event_task_id(later) != task_id:
            continue
        if later.type not in {"task.assigned", "task.dispatched"}:
            continue
        payload = later.payload if isinstance(later.payload, dict) else {}
        role = str(
            payload.get("assignee")
            or payload.get("role")
            or payload.get("target_role")
            or ""
        ).strip()
        if role:
            return role
    return ""


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _missing_tool_from_stderr(stderr: str) -> str:
    text = str(stderr or "")
    patterns = (
        r":\s*(?P<tool>[A-Za-z0-9_.+-]+):\s*not found\b",
        r"(?P<tool>[A-Za-z0-9_.+-]+):\s*command not found\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group("tool")
    return "unknown"


def _contract_verifier_missing_tool(
    event: ZfEvent,
) -> tuple[str, str, str] | None:
    if event.type != "discriminator.failed":
        return None
    payload = _payload(event)
    for detail in payload.get("details") or []:
        if not isinstance(detail, dict):
            continue
        if str(detail.get("d") or "") != "ContractD":
            continue
        evidence = detail.get("evidence")
        if not isinstance(evidence, dict):
            continue
        returncode = _int_or_none(evidence.get("verification_returncode"))
        stderr = str(evidence.get("verification_stderr_tail") or "")
        stderr_lower = stderr.lower()
        if returncode != 127:
            continue
        if not any(marker in stderr_lower for marker in _TOOL_NOT_FOUND_MARKERS):
            continue
        command = str(
            evidence.get("verification_shell_command")
            or evidence.get("verification_command")
            or ""
        ).strip()
        return _missing_tool_from_stderr(stderr), command, stderr.strip()
    return None


def _contract_verifier_recovered(
    events: list[ZfEvent],
    *,
    failed_idx: int,
    failed_event: ZfEvent,
) -> bool:
    task_id = _event_task_id(failed_event)
    if not task_id:
        return False
    for event in events[failed_idx + 1:]:
        if _event_task_id(event) != task_id:
            continue
        if event.type == "discriminator.passed":
            return True
        if _is_terminal_done(event):
            return True
    return False


def detect_contract_verifier_missing_tools(
    events: list[ZfEvent],
    *,
    state_dir: Path,
) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    for idx, event in enumerate(events):
        found = _contract_verifier_missing_tool(event)
        if found is None:
            continue
        if _contract_verifier_recovered(
            events,
            failed_idx=idx,
            failed_event=event,
        ):
            continue
        task_id = _event_task_id(event) or "unknown"
        tool, command, stderr = found
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="event_log",
            fingerprint=f"contract_verifier_missing_tool:{task_id}:{tool}",
            category="verification_environment_missing_tool",
            severity="high",
            summary=(
                f"ContractD verification for {task_id} failed because "
                f"tool {tool!r} was not available"
            ),
            expected=(
                "ContractD runs declared verification commands in an "
                "environment containing required harness tools or reports "
                "an infrastructure repair signal"
            ),
            actual=stderr or "verification command returned 127",
            event_ids=[event.id],
            repro_command=command or f"zf events --task {task_id} --last 80",
            metric_impacts={
                "runtime_reliability": -0.35,
                "eval_strength": -0.25,
            },
        ))
    return signals


def detect_completion_without_gate(
    events: list[ZfEvent],
    *,
    state_dir: Path,
) -> list[FailureSignal]:
    types = {event.type for event in events}
    if types & _GATE_EVIDENCE:
        return []
    terminal = [event for event in events if _is_terminal_done(event)]
    if not terminal:
        return []
    ids = [event.id for event in terminal]
    return [_signal(
        state_dir=state_dir,
        source_kind="event_log",
        fingerprint="completion_without_gate:" + ",".join(sorted(ids)[:5]),
        category="self_declared_completion",
        severity="high",
        summary="Terminal completion appeared without gate evidence",
        expected="done requires task evidence and gate/verification support",
        actual="terminal done event exists but no gate evidence event was found",
        event_ids=ids,
        metric_impacts={"eval_strength": -0.35},
    )]


def detect_state_dir_violations(
    events: list[ZfEvent],
    *,
    state_dir: Path,
) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    for event in events:
        if event.type == "state_dir.violation":
            p = _payload(event)
            signals.append(_signal(
                state_dir=state_dir,
                source_kind="event_log",
                fingerprint=f"state_dir_violation:{p.get('path') or event.id}",
                category="control_plane_violation",
                severity="high",
                summary="Runtime ignored project.state_dir or hard-coded .zf",
                expected="all runtime state honors project.state_dir",
                actual=str(p.get("path") or p.get("reason") or "state_dir.violation"),
                event_ids=[event.id],
                metric_impacts={"control_plane_safety": -0.4},
            ))
    return signals


def detect_web_bind_logs(
    *,
    state_dir: Path,
    log_paths: Iterable[Path],
) -> list[FailureSignal]:
    signals: list[FailureSignal] = []
    for path in log_paths:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "127.0.0.1" not in text and "localhost" not in text:
            continue
        if "0.0.0.0" in text:
            continue
        signals.append(_signal(
            state_dir=state_dir,
            source_kind="web_log",
            source_path=str(path),
            fingerprint=f"web_bind_localhost:{path}",
            category="operator_access_bug",
            severity="high",
            summary="Web service appears bound to localhost-only",
            expected="operator can explicitly bind Web/API to 0.0.0.0 when needed",
            actual="log contains localhost/127.0.0.1 without 0.0.0.0",
            evidence_paths=[str(path)],
            metric_impacts={"web_observability": -0.3, "operator_ergonomics": -0.2},
        ))
    return signals


def detect_codex_realism_archive(
    *,
    state_dir: Path,
    run_dir: Path,
) -> list[FailureSignal]:
    report = run_dir / "report.md"
    runner_log = run_dir / "inner-runner.log"
    if not report.exists():
        return []
    try:
        text = report.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return []
    claims_real = any(token in text for token in (
        "real codex",
        "真实 codex",
        "真实 provider",
        "real provider",
    ))
    has_evidence = runner_log.exists() and runner_log.stat().st_size > 0
    if not claims_real or has_evidence:
        return []
    return [_signal(
        state_dir=state_dir,
        source_kind="run_archive",
        source_path=str(report),
        fingerprint=f"missing_real_provider_evidence:{run_dir.name}",
        category="missing_real_provider_evidence",
        severity="high",
        summary="Run archive claims real Codex/provider evidence but lacks runner log",
        expected="real-provider claims include command/session evidence",
        actual=f"missing or empty {runner_log}",
        evidence_paths=[str(report)],
        metric_impacts={"instrument_score": -0.4, "realism": -0.5},
    )]


def collect_failure_signals(
    state_dir: Path,
    *,
    run_dir: Path | None = None,
    web_log_paths: Iterable[Path] = (),
) -> list[FailureSignal]:
    state_dir = Path(state_dir)
    events = _read_events(state_dir)
    if completed_run_quiesced(events):
        return []
    signals: list[FailureSignal] = []
    signals.extend(detect_fatal_events(events, state_dir=state_dir))
    signals.extend(detect_semantic_flow_failures(events, state_dir=state_dir))
    signals.extend(detect_fanout_failures(events, state_dir=state_dir))
    signals.extend(detect_replan_followthrough_gaps(events, state_dir=state_dir))
    signals.extend(detect_task_ref_handoff_deadends(events, state_dir=state_dir))
    signals.extend(detect_worker_stuck(events, state_dir=state_dir))
    signals.extend(detect_dispatch_preflight_blockers(events, state_dir=state_dir))
    signals.extend(detect_success_handoff_stalls(events, state_dir=state_dir))
    signals.extend(detect_contract_verifier_missing_tools(events, state_dir=state_dir))
    signals.extend(detect_readonly_gate_mutations(events, state_dir=state_dir))
    signals.extend(detect_completion_without_gate(events, state_dir=state_dir))
    signals.extend(detect_state_dir_violations(events, state_dir=state_dir))
    signals.extend(detect_web_bind_logs(state_dir=state_dir, log_paths=web_log_paths))
    if run_dir is not None:
        signals.extend(detect_codex_realism_archive(state_dir=state_dir, run_dir=run_dir))
    return sorted(
        _dedupe_signals(signals),
        key=lambda signal: (-severity_rank(signal.severity), signal.fingerprint),
    )


def completed_run_quiesced(events: list[ZfEvent]) -> bool:
    completed_idx, completed = _latest_completed_run(events)
    if completed is None:
        return False
    return not _run_reopened_after_completion(events, completed_idx, completed)


def _latest_completed_run(events: list[ZfEvent]) -> tuple[int, ZfEvent | None]:
    for idx in range(len(events) - 1, -1, -1):
        event = events[idx]
        if event.type != "run.completed":
            continue
        payload = _payload(event)
        status = str(
            payload.get("status")
            or payload.get("completion_status")
            or ""
        ).strip()
        if status in {"", "passed", "complete", "completed"}:
            return idx, event
    return -1, None


def _run_reopened_after_completion(
    events: list[ZfEvent],
    completed_idx: int,
    completed: ZfEvent,
) -> bool:
    completed_payload = _payload(completed)
    completed_head = str(
        completed_payload.get("candidate_head_commit")
        or completed_payload.get("head")
        or ""
    ).strip()
    completed_candidate = str(
        completed_payload.get("candidate_ref")
        or completed_payload.get("target_ref")
        or completed_payload.get("candidate_branch")
        or ""
    ).strip()
    for event in events[completed_idx + 1:]:
        payload = _payload(event)
        if event.type in _RUN_COMPLETED_REOPEN_EVENTS:
            return True
        if event.type == "run.goal.updated":
            if str(payload.get("status") or "").strip() not in {
                "",
                "complete",
                "completed",
                "passed",
            }:
                return True
        if event.type == "candidate.ready":
            head = str(
                payload.get("candidate_head_commit")
                or payload.get("head")
                or ""
            ).strip()
            candidate = str(
                payload.get("candidate_ref")
                or payload.get("target_ref")
                or payload.get("candidate_branch")
                or ""
            ).strip()
            if completed_head:
                if head and head != completed_head:
                    return True
            elif completed_candidate:
                if candidate and candidate != completed_candidate:
                    return True
            elif head or candidate:
                return True
    return False


def _dedupe_signals(signals: Iterable[FailureSignal]) -> list[FailureSignal]:
    by_key: dict[str, FailureSignal] = {}
    for signal in signals:
        key = signal.fingerprint or signal.signal_id
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = signal
            continue
        event_ids = sorted(set(existing.event_ids) | set(signal.event_ids))
        evidence = sorted(set(existing.evidence_paths) | set(signal.evidence_paths))
        severity = (
            signal.severity
            if severity_rank(signal.severity) > severity_rank(existing.severity)
            else existing.severity
        )
        by_key[key] = FailureSignal(
            **{
                **existing.to_dict(),
                "severity": severity,
                "event_ids": event_ids,
                "evidence_paths": evidence,
            },
        )
    return list(by_key.values())


def write_failure_signals_jsonl(path: Path, signals: Iterable[FailureSignal]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for signal in signals:
            fh.write(json.dumps(signal.to_dict(), ensure_ascii=False) + "\n")


__all__ = [
    "FailureSignal",
    "severity_rank",
    "collect_failure_signals",
    "detect_readonly_gate_mutations",
    "detect_semantic_flow_failures",
    "detect_worker_stuck",
    "detect_fatal_events",
    "detect_fanout_failures",
    "detect_replan_followthrough_gaps",
    "detect_dispatch_preflight_blockers",
    "detect_success_handoff_stalls",
    "detect_contract_verifier_missing_tools",
    "detect_completion_without_gate",
    "detect_state_dir_violations",
    "detect_web_bind_logs",
    "detect_codex_realism_archive",
    "completed_run_quiesced",
    "write_failure_signals_jsonl",
]
