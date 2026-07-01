"""Workflow graph reconcile helpers.

This module is safe to use in shadow mode: ``plan`` returns evaluations and
action plans; callers must explicitly call ``commit`` on StageActionRunner to
mutate state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.workflow.graph import WorkflowGraph, WorkflowNode
from zf.runtime.stage_actions import StageActionPlan, StageActionRunner
from zf.runtime.stage_actions import StageActionContext, StageActionResult
from zf.runtime.workflow_conditions import (
    StageEvaluation,
    WorkflowConditionEvaluator,
    WorkflowEvaluationContext,
)


@dataclass(frozen=True)
class WorkflowReconcileDecision:
    node_id: str
    stage_id: str
    ready: bool
    reason: str
    evaluation: StageEvaluation
    action_plan: StageActionPlan | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "stage_id": self.stage_id,
            "ready": self.ready,
            "reason": self.reason,
            "evaluation": self.evaluation.to_dict(),
            "action_plan": self.action_plan.to_dict() if self.action_plan else None,
        }


class WorkflowGraphReconciler:
    def __init__(
        self,
        *,
        evaluator: WorkflowConditionEvaluator | None = None,
        action_runner: StageActionRunner | None = None,
    ) -> None:
        self.evaluator = evaluator or WorkflowConditionEvaluator()
        self.action_runner = action_runner or StageActionRunner()

    def plan(
        self,
        *,
        graph: WorkflowGraph,
        events: list[ZfEvent],
        task: Task | None = None,
        trigger_event: ZfEvent | None = None,
    ) -> list[WorkflowReconcileDecision]:
        affected = self._affected_nodes(graph, trigger_event)
        decisions: list[WorkflowReconcileDecision] = []
        for node in affected:
            evaluation = self.evaluator.evaluate_node(
                node,
                WorkflowEvaluationContext(
                    events=events,
                    task=task,
                    trigger_event=trigger_event,
                ),
            )
            action_plan = None
            if evaluation.ready and node.action:
                action_plan = self.action_runner.plan(
                    node=node,
                    task_id=task.id if task is not None else (trigger_event.task_id if trigger_event else ""),
                    source_events=[trigger_event] if trigger_event is not None else [],
                    payload=_action_payload(node, trigger_event),
                )
            reason = "ready" if evaluation.ready else _blocked_reason(evaluation)
            decisions.append(WorkflowReconcileDecision(
                node_id=node.node_id,
                stage_id=node.stage_id,
                ready=evaluation.ready,
                reason=reason,
                evaluation=evaluation,
                action_plan=action_plan,
            ))
        return decisions

    def commit(
        self,
        decisions: list[WorkflowReconcileDecision],
        context: StageActionContext,
    ) -> list[StageActionResult]:
        results: list[StageActionResult] = []
        for decision in decisions:
            if decision.action_plan is None:
                continue
            results.append(self.action_runner.commit(decision.action_plan, context))
        return results

    def resync(
        self,
        *,
        graph: WorkflowGraph,
        events: list[ZfEvent],
        tasks: list[Task],
    ) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            if task.status in {"done", "cancelled"}:
                continue
            task_events = [
                event for event in events
                if not event.task_id or event.task_id == task.id
            ]
            out[task.id] = [
                decision.to_dict()
                for decision in self.plan(graph=graph, events=task_events, task=task)
                if decision.ready
            ]
        return out

    def _affected_nodes(
        self,
        graph: WorkflowGraph,
        trigger_event: ZfEvent | None,
    ) -> list[WorkflowNode]:
        if trigger_event is None:
            return list(graph.nodes)
        event_type = trigger_event.type
        affected_ids = {
            edge.to_node for edge in graph.edges
            if event_type in _split_events(edge.event)
        }
        affected = [node for node in graph.nodes if node.node_id in affected_ids]
        if affected:
            return affected
        return [
            node for node in graph.nodes
            if event_type in _split_events(node.trigger)
        ]


def _blocked_reason(evaluation: StageEvaluation) -> str:
    failed = [condition for condition in evaluation.conditions if not condition.passed]
    if not failed:
        return "not ready"
    return "; ".join(condition.reason for condition in failed[:3])


def _action_payload(
    node: WorkflowNode,
    trigger_event: ZfEvent | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    target_role = str(node.metadata.get("target_role") or "")
    if target_role:
        payload["target_role"] = target_role
    gate = str(node.metadata.get("gate") or "")
    if gate:
        payload["gate"] = gate
    if trigger_event is not None:
        payload["reason"] = f"{trigger_event.type} -> {node.stage_id}"
        payload["trigger_event_type"] = trigger_event.type
        payload["trigger_event_id"] = trigger_event.id
    if node.action == "emit" and node.success_event:
        payload["event"] = node.success_event
    return payload


def _split_events(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}
