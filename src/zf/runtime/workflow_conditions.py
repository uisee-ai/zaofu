"""Workflow graph condition evaluation.

This module is intentionally read-only. It returns structured reasons but does
not mutate EventLog, TaskStore, or projections.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.workflow.graph import WorkflowNode


TERMINAL_PHASE_EVENTS: frozenset[str] = frozenset({
    "static_gate.passed",
    "static_gate.failed",
    "static_gate.skipped",
    "review.approved",
    "review.rejected",
    "verify.passed",
    "verify.failed",
    "test.passed",
    "test.failed",
    "judge.passed",
    "judge.failed",
})


@dataclass(frozen=True)
class UpstreamStageStates:
    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    omitted: int = 0
    done: int = 0
    running: int = 0
    blocked: int = 0
    latest_dispatch_matched: bool = True

    @classmethod
    def from_events(
        cls,
        events: list[ZfEvent],
        *,
        task: Task | None = None,
        trigger_event: ZfEvent | None = None,
    ) -> "UpstreamStageStates":
        success = failed = skipped = omitted = 0
        for event in events:
            if event.type.endswith((".passed", ".approved", ".done", ".completed", ".ready")):
                success += 1
            elif event.type.endswith((".failed", ".rejected", ".blocked")):
                failed += 1
            elif event.type.endswith(".skipped"):
                skipped += 1
            elif event.type.endswith(".omitted"):
                omitted += 1
        done = success + failed + skipped + omitted
        latest_dispatch_matched = _latest_dispatch_matches(
            events,
            task=task,
            trigger_event=trigger_event,
        )
        return cls(
            total=max(done, len([e for e in events if e.type in TERMINAL_PHASE_EVENTS])),
            success=success,
            failed=failed,
            skipped=skipped,
            omitted=omitted,
            done=done,
            running=0,
            blocked=failed,
            latest_dispatch_matched=latest_dispatch_matched,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConditionResult:
    type: str
    passed: bool
    reason: str = ""
    source_event_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_event_ids"] = list(self.source_event_ids)
        return data


@dataclass(frozen=True)
class StageEvaluation:
    node_id: str
    stage_id: str
    ready: bool
    conditions: tuple[ConditionResult, ...]
    upstream: UpstreamStageStates
    shadow_matches: bool | None = None
    diagnostics: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "stage_id": self.stage_id,
            "ready": self.ready,
            "conditions": [condition.to_dict() for condition in self.conditions],
            "upstream": self.upstream.to_dict(),
            "shadow_matches": self.shadow_matches,
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class WorkflowEvaluationContext:
    events: list[ZfEvent] = field(default_factory=list)
    task: Task | None = None
    trigger_event: ZfEvent | None = None
    active_role_tasks: dict[str, int] = field(default_factory=dict)
    shadow_expected_ready: bool | None = None


class WorkflowConditionEvaluator:
    def evaluate_node(
        self,
        node: WorkflowNode,
        context: WorkflowEvaluationContext,
        conditions: list[str] | tuple[str, ...] | None = None,
    ) -> StageEvaluation:
        condition_names = tuple(conditions or node.conditions or ("event_seen",))
        relevant = _events_for_task(context.events, context.task)
        upstream = UpstreamStageStates.from_events(
            relevant,
            task=context.task,
            trigger_event=context.trigger_event,
        )
        results = tuple(
            self._evaluate_condition(name, node, context, relevant, upstream)
            for name in condition_names
        )
        ready = all(result.passed for result in results)
        shadow_matches = (
            None if context.shadow_expected_ready is None
            else ready == context.shadow_expected_ready
        )
        return StageEvaluation(
            node_id=node.node_id,
            stage_id=node.stage_id,
            ready=ready,
            conditions=results,
            upstream=upstream,
            shadow_matches=shadow_matches,
        )

    def _evaluate_condition(
        self,
        name: str,
        node: WorkflowNode,
        context: WorkflowEvaluationContext,
        events: list[ZfEvent],
        upstream: UpstreamStageStates,
    ) -> ConditionResult:
        if name == "event_seen":
            event_type = (
                context.trigger_event.type if context.trigger_event is not None
                else node.trigger
            )
            matches = _events_of_type(events, event_type)
            if matches:
                return _result(name, True, matches, f"event {event_type!r} seen")
            return _result(
                name,
                False,
                matches,
                f"missing upstream event {event_type!r}",
            )
        if name == "latest_dispatch_matches":
            latest_match = _latest_dispatch_match_details(
                events,
                task=context.task,
                trigger_event=context.trigger_event,
            )
            return ConditionResult(
                type=name,
                passed=latest_match["passed"],
                reason=str(latest_match["reason"]),
                source_event_ids=tuple(
                    event.id for event in events if event.type == "task.dispatched"
                )[-1:],
            )
        if name == "task_status_in":
            allowed = set(_metadata_list(node.metadata.get("task_status_in"))) or {"in_progress"}
            status = str(getattr(context.task, "status", "") or "")
            return ConditionResult(name, status in allowed, f"task status={status!r}, allowed={sorted(allowed)!r}")
        if name == "role_available":
            busy = [
                role for role in node.roles
                if int(context.active_role_tasks.get(role, 0)) > 0
            ]
            return ConditionResult(name, not busy, f"busy roles={busy!r}")
        if name == "evidence_present":
            event_type = str(node.success_event or node.trigger or "")
            matches = _events_of_type(events, event_type)
            if matches:
                return _result(name, True, matches, f"evidence event {event_type!r} present")
            return _result(
                name,
                False,
                matches,
                f"missing evidence event {event_type!r}",
            )
        if name == "stage_contract_passed":
            if not node.metadata.get("criteria"):
                return ConditionResult(name, True, "no explicit criteria")
            matches = _events_of_type(events, "stage.contract.passed")
            return _result(name, bool(matches), matches, "stage contract passed")
        if name == "terminal_evidence_accepted":
            matches = _events_of_type(events, node.success_event)
            return _result(name, bool(matches), matches, "terminal evidence accepted")
        if name == "fanout_barrier_satisfied":
            success = _events_of_type(events, node.success_event)
            failure = _events_of_type(events, node.failure_event)
            return _result(name, bool(success or failure), success + failure, "fanout barrier has aggregate result")
        if name == "all_success":
            return ConditionResult(name, upstream.total == 0 or upstream.success >= upstream.total, "all upstreams succeeded")
        if name == "one_success":
            return ConditionResult(name, upstream.success > 0, "at least one upstream succeeded")
        if name == "all_done":
            return ConditionResult(name, upstream.total == 0 or upstream.done >= upstream.total, "all upstreams done")
        if name == "none_failed":
            return ConditionResult(name, upstream.failed == 0, "no upstream failed")
        if name in {"scope_available", "budget_available", "context_safe", "gate_policy_allows"}:
            return ConditionResult(name, True, f"{name} default allow in shadow mode")
        return ConditionResult(name, False, f"unknown condition {name!r}")


def _events_for_task(events: list[ZfEvent], task: Task | None) -> list[ZfEvent]:
    if task is None:
        return list(events)
    return [
        event for event in events
        if not event.task_id or event.task_id == task.id
    ]


def _events_of_type(events: list[ZfEvent], event_type: str) -> list[ZfEvent]:
    if not event_type:
        return []
    candidates = {event_type}
    if "," in event_type:
        candidates = {item.strip() for item in event_type.split(",") if item.strip()}
    return [event for event in events if event.type in candidates]


def _metadata_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return []


def _result(name: str, passed: bool, events: list[ZfEvent], reason: str) -> ConditionResult:
    return ConditionResult(
        type=name,
        passed=passed,
        reason=reason,
        source_event_ids=tuple(event.id for event in events),
    )


def _latest_dispatch_matches(
    events: list[ZfEvent],
    *,
    task: Task | None,
    trigger_event: ZfEvent | None,
) -> bool:
    return bool(_latest_dispatch_match_details(
        events,
        task=task,
        trigger_event=trigger_event,
    )["passed"])


def _latest_dispatch_match_details(
    events: list[ZfEvent],
    *,
    task: Task | None,
    trigger_event: ZfEvent | None,
) -> dict[str, object]:
    if trigger_event is None:
        return {"passed": True, "reason": "no trigger event"}
    payload = trigger_event.payload if isinstance(trigger_event.payload, dict) else {}
    trigger_dispatch = str(payload.get("dispatch_id") or "")
    if not trigger_dispatch:
        return {"passed": True, "reason": "trigger has no dispatch_id"}
    task_id = trigger_event.task_id or (task.id if task is not None else "")
    dispatches = [
        event for event in events
        if event.type == "task.dispatched"
        and (not task_id or event.task_id == task_id)
    ]
    if not dispatches:
        active = str(getattr(task, "active_dispatch_id", "") or "")
        passed = not active or active == trigger_dispatch
        return {
            "passed": passed,
            "reason": (
                "active task dispatch matches trigger"
                if passed
                else f"trigger dispatch_id {trigger_dispatch!r} != active {active!r}"
            ),
        }
    latest = dispatches[-1]
    latest_payload = latest.payload if isinstance(latest.payload, dict) else {}
    latest_dispatch = str(latest_payload.get("dispatch_id") or "")
    passed = latest_dispatch == trigger_dispatch
    return {
        "passed": passed,
        "reason": (
            "latest dispatch matches trigger event"
            if passed
            else f"trigger dispatch_id {trigger_dispatch!r} != latest {latest_dispatch!r}"
        ),
    }
