"""Deterministic stage action runner contract.

The runner separates planning from mutation. ``plan`` is pure; ``commit`` is
the only path that may write through EventWriter / TaskStore.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.store import TaskStore
from zf.core.workflow.graph import WorkflowNode


SUPPORTED_ACTIONS: frozenset[str] = frozenset({
    "emit",
    "dispatch_role",
    "run_gate",
    "start_fanout",
    "aggregate_fanout",
    "route_rework",
    "complete_task",
    "block_with_reason",
})


@dataclass(frozen=True)
class StageActionPlan:
    action_type: str
    stage_id: str
    task_id: str = ""
    decision: str = "noop"
    reason: str = ""
    source_event_ids: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_event_ids"] = list(self.source_event_ids)
        return data


@dataclass(frozen=True)
class StageActionResult:
    action_type: str
    stage_id: str
    task_id: str = ""
    decision: str = "noop"
    reason: str = ""
    source_event_ids: tuple[str, ...] = ()
    emitted_event_ids: tuple[str, ...] = ()
    state_changes: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_event_ids"] = list(self.source_event_ids)
        data["emitted_event_ids"] = list(self.emitted_event_ids)
        data["state_changes"] = list(self.state_changes)
        return data


@dataclass
class StageActionContext:
    event_writer: EventWriter | None = None
    task_store: TaskStore | None = None
    source_event: ZfEvent | None = None
    project_root: str = ""
    config: object | None = None


class StageActionRunner:
    def __init__(self) -> None:
        self._committed_keys: set[tuple[str, str, str, tuple[str, ...]]] = set()

    def plan(
        self,
        *,
        node: WorkflowNode,
        action_type: str | None = None,
        task_id: str = "",
        source_events: list[ZfEvent] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> StageActionPlan:
        action = action_type or node.action or "emit"
        source_ids = tuple(event.id for event in (source_events or []))
        if action not in SUPPORTED_ACTIONS:
            return StageActionPlan(
                action_type=action,
                stage_id=node.stage_id,
                task_id=task_id,
                decision="blocked",
                reason=f"unsupported stage action {action!r}",
                source_event_ids=source_ids,
                payload=dict(payload or {}),
            )
        return StageActionPlan(
            action_type=action,
            stage_id=node.stage_id,
            task_id=task_id,
            decision="planned",
            reason=f"{action} planned",
            source_event_ids=source_ids,
            payload={
                "target_role": str(node.metadata.get("target_role") or ""),
                "gate": str(node.metadata.get("gate") or ""),
                **dict(payload or {}),
            },
        )

    def commit(
        self,
        plan: StageActionPlan,
        context: StageActionContext,
    ) -> StageActionResult:
        key = (
            plan.action_type,
            plan.stage_id,
            plan.task_id,
            plan.source_event_ids,
        )
        if key in self._committed_keys:
            return StageActionResult(
                action_type=plan.action_type,
                stage_id=plan.stage_id,
                task_id=plan.task_id,
                decision="deduped",
                reason="stage action already committed in this runner",
                source_event_ids=plan.source_event_ids,
            )
        replayed = self._already_committed(plan, context)
        if replayed is not None:
            self._committed_keys.add(key)
            return replayed
        if plan.decision == "blocked":
            return StageActionResult(
                action_type=plan.action_type,
                stage_id=plan.stage_id,
                task_id=plan.task_id,
                decision="blocked",
                reason=plan.reason,
                source_event_ids=plan.source_event_ids,
            )
        result = self._commit_once(plan, context)
        self._committed_keys.add(key)
        return result

    def _commit_once(
        self,
        plan: StageActionPlan,
        context: StageActionContext,
    ) -> StageActionResult:
        if plan.action_type == "complete_task":
            return self._complete_task(plan, context)
        if plan.action_type == "route_rework":
            return self._route_rework(plan, context)
        if plan.action_type == "block_with_reason":
            return self._block_task(plan, context)
        if plan.action_type == "run_gate":
            return self._run_gate(plan, context)
        if plan.action_type in {
            "emit",
            "dispatch_role",
            "start_fanout",
            "aggregate_fanout",
        }:
            return self._emit_event(plan, context)
        return StageActionResult(
            action_type=plan.action_type,
            stage_id=plan.stage_id,
            task_id=plan.task_id,
            decision="blocked",
            reason=f"unsupported stage action {plan.action_type!r}",
            source_event_ids=plan.source_event_ids,
        )

    def _emit_event(
        self,
        plan: StageActionPlan,
        context: StageActionContext,
    ) -> StageActionResult:
        writer = context.event_writer
        event_type = str(plan.payload.get("event") or _default_event_type(plan))
        if writer is None:
            return StageActionResult(
                action_type=plan.action_type,
                stage_id=plan.stage_id,
                task_id=plan.task_id,
                decision="planned_only",
                reason="no EventWriter supplied",
                source_event_ids=plan.source_event_ids,
            )
        event = ZfEvent(
            type=event_type,
            actor="workflow_graph",
            task_id=plan.task_id or None,
            payload={
                "stage_id": plan.stage_id,
                "action_type": plan.action_type,
                "decision": "committed",
                **{
                    key: value for key, value in plan.payload.items()
                    if key not in {"event", "payload"}
                },
                **dict(plan.payload.get("payload") or {}),
            },
            causation_id=(
                context.source_event.id if context.source_event is not None else None
            ),
            correlation_id=(
                context.source_event.correlation_id if context.source_event is not None else None
            ),
        )
        writer.append(event)
        return StageActionResult(
            action_type=plan.action_type,
            stage_id=plan.stage_id,
            task_id=plan.task_id,
            decision="committed",
            reason=f"emitted {event_type}",
            source_event_ids=plan.source_event_ids,
            emitted_event_ids=(event.id,),
        )

    def _complete_task(
        self,
        plan: StageActionPlan,
        context: StageActionContext,
    ) -> StageActionResult:
        if context.task_store is None or not plan.task_id:
            return StageActionResult(
                plan.action_type,
                plan.stage_id,
                plan.task_id,
                "planned_only",
                "no TaskStore or task_id supplied",
                plan.source_event_ids,
            )
        existing = context.task_store.get(plan.task_id)
        if existing is not None and existing.status == "done":
            return StageActionResult(
                action_type=plan.action_type,
                stage_id=plan.stage_id,
                task_id=plan.task_id,
                decision="deduped",
                reason="task already done",
                source_event_ids=plan.source_event_ids,
            )
        updated = context.task_store.update(plan.task_id, status="done")
        change = {"task_id": plan.task_id, "status": "done"} if updated else {}
        return StageActionResult(
            action_type=plan.action_type,
            stage_id=plan.stage_id,
            task_id=plan.task_id,
            decision="committed" if updated else "blocked",
            reason="task completed" if updated else "task not found",
            source_event_ids=plan.source_event_ids,
            state_changes=tuple([change] if change else []),
        )

    def _route_rework(
        self,
        plan: StageActionPlan,
        context: StageActionContext,
    ) -> StageActionResult:
        payload = {
            "event": "task.rework.requested",
            "payload": {
                "target_role": str(plan.payload.get("target_role") or "dev"),
                "reason": str(plan.payload.get("reason") or plan.reason),
                "trigger_event_id": (
                    context.source_event.id if context.source_event is not None else ""
                ),
            },
        }
        return self._emit_event(
            StageActionPlan(
                action_type=plan.action_type,
                stage_id=plan.stage_id,
                task_id=plan.task_id,
                decision=plan.decision,
                reason=plan.reason,
                source_event_ids=plan.source_event_ids,
                payload=payload,
            ),
            context,
        )

    def _block_task(
        self,
        plan: StageActionPlan,
        context: StageActionContext,
    ) -> StageActionResult:
        if context.task_store is None or not plan.task_id:
            return StageActionResult(
                plan.action_type,
                plan.stage_id,
                plan.task_id,
                "planned_only",
                "no TaskStore or task_id supplied",
                plan.source_event_ids,
            )
        reason = str(plan.payload.get("reason") or plan.reason or "workflow blocked")
        updated = context.task_store.update(
            plan.task_id,
            status="blocked",
            blocked_reason=reason,
        )
        change = {"task_id": plan.task_id, "status": "blocked"} if updated else {}
        return StageActionResult(
            action_type=plan.action_type,
            stage_id=plan.stage_id,
            task_id=plan.task_id,
            decision="committed" if updated else "blocked",
            reason=reason if updated else "task not found",
            source_event_ids=plan.source_event_ids,
            state_changes=tuple([change] if change else []),
        )

    def _run_gate(
        self,
        plan: StageActionPlan,
        context: StageActionContext,
    ) -> StageActionResult:
        writer = context.event_writer
        if writer is None or context.config is None or context.source_event is None:
            return StageActionResult(
                action_type=plan.action_type,
                stage_id=plan.stage_id,
                task_id=plan.task_id,
                decision="planned_only",
                reason="run_gate requires EventWriter, config, and source_event",
                source_event_ids=plan.source_event_ids,
            )
        gate = str(plan.payload.get("gate") or "static")
        if gate != "static":
            return StageActionResult(
                action_type=plan.action_type,
                stage_id=plan.stage_id,
                task_id=plan.task_id,
                decision="blocked",
                reason=f"unsupported gate {gate!r}",
                source_event_ids=plan.source_event_ids,
            )
        from zf.runtime.static_gate import build_static_gate_event, run_static_gate

        project_root = Path(context.project_root or ".")
        result = _static_gate_override_result(plan, context)
        if result is None:
            result = run_static_gate(
                config=context.config,
                project_root=project_root,
            )
        gate_event = build_static_gate_event(
            result,
            trigger_event=context.source_event,
            actor="workflow_graph",
        )
        gate_event.payload["stage_id"] = plan.stage_id
        gate_event.payload["action_type"] = plan.action_type
        gate_event.payload["decision"] = "committed"
        gate_event.payload["workdir"] = str(project_root)
        emitted = writer.append(gate_event)
        return StageActionResult(
            action_type=plan.action_type,
            stage_id=plan.stage_id,
            task_id=plan.task_id,
            decision="committed",
            reason=f"emitted {emitted.type}",
            source_event_ids=plan.source_event_ids,
            emitted_event_ids=(emitted.id,),
        )

    def _already_committed(
        self,
        plan: StageActionPlan,
        context: StageActionContext,
    ) -> StageActionResult | None:
        writer = context.event_writer
        if writer is None:
            return None
        try:
            events = writer.event_log.read_all()
        except Exception:
            return None
        source_ids = set(plan.source_event_ids)
        if not source_ids:
            return None
        for event in reversed(events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            trigger_event_id = str(payload.get("trigger_event_id") or "")
            causation_id = str(event.causation_id or "")
            if source_ids and trigger_event_id not in source_ids and causation_id not in source_ids:
                continue
            if plan.action_type == "run_gate" and event.type in {
                "static_gate.passed",
                "static_gate.failed",
                "static_gate.skipped",
            }:
                return _deduped_result(plan, f"gate already emitted {event.type}", event.id)
            if plan.action_type == "route_rework" and event.type == "task.rework.requested":
                return _deduped_result(plan, "rework already requested", event.id)
            if plan.action_type == "dispatch_role" and event.type == "workflow.dispatch.requested":
                return _deduped_result(plan, "dispatch already requested", event.id)
        return None


def _default_event_type(plan: StageActionPlan) -> str:
    if plan.action_type == "dispatch_role":
        return "workflow.dispatch.requested"
    if plan.action_type == "run_gate":
        return "workflow.gate.requested"
    if plan.action_type == "start_fanout":
        return "workflow.fanout.start_requested"
    if plan.action_type == "aggregate_fanout":
        return "workflow.fanout.aggregate_requested"
    return "workflow.action.emitted"


def _deduped_result(
    plan: StageActionPlan,
    reason: str,
    event_id: str,
) -> StageActionResult:
    return StageActionResult(
        action_type=plan.action_type,
        stage_id=plan.stage_id,
        task_id=plan.task_id,
        decision="deduped",
        reason=reason,
        source_event_ids=plan.source_event_ids,
        emitted_event_ids=(event_id,),
    )


def _static_gate_override_result(
    plan: StageActionPlan,
    context: StageActionContext,
):
    task_store = context.task_store
    if task_store is None or not plan.task_id:
        return None
    try:
        task = task_store.get(plan.task_id)
    except Exception:
        return None
    if task is None or task.contract is None:
        return None
    override = getattr(task.contract, "quality_gates_override", {}) or {}
    static_override = override.get("static") or {}
    if not isinstance(static_override, dict):
        return None
    if static_override.get("enabled") is not False:
        return None
    from zf.runtime.static_gate import StaticGateResult

    return StaticGateResult(
        passed=True,
        skipped=True,
        skip_reason=(
            "per-task contract.quality_gates_override.static.enabled=False "
            "(#E cangjie 2026-05-21 doc-type task opt-out)"
        ),
    )
