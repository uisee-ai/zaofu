"""Reactor handlers for admitted Orchestrator Agent lifecycle events."""

from __future__ import annotations

from zf.core.events.model import ZfEvent
from zf.runtime.channel_prd_evaluation_reactor import react_channel_prd_evaluation_requested
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision


class OrchestratorAgentReactorMixin:
    def _on_channel_prd_evaluation_requested(
        self, event: ZfEvent,
    ) -> WorkflowRuntimeDecision | None:
        react_channel_prd_evaluation_requested(self, event)
        return None

    def _on_orchestrator_stage_barrier_admitted(
        self,
        event: ZfEvent,
    ) -> WorkflowRuntimeDecision:
        source = self._orchestrator_checkpoint_source(event)
        if source is None:
            return WorkflowRuntimeDecision(
                action="block",
                role="orchestrator",
                reason="admitted stage barrier source event is missing",
            )
        reader_started = self._maybe_start_reader_fanout(source)
        self._maybe_start_writer_fanout(source)
        return WorkflowRuntimeDecision(
            action="dispatch",
            role="orchestrator",
            reason=(
                "admitted stage barrier redrove the original aggregate edge"
                if reader_started
                else "admitted stage barrier redrove the original writer edge"
            ),
        )

    def _on_orchestrator_pre_closeout_admitted(
        self,
        event: ZfEvent,
    ) -> WorkflowRuntimeDecision:
        source = self._orchestrator_checkpoint_source(event)
        if source is None:
            return WorkflowRuntimeDecision(
                action="block",
                role="orchestrator",
                reason="admitted pre-closeout source event is missing",
            )
        self._maybe_complete_run_goal(source)
        return WorkflowRuntimeDecision(
            action="gate",
            role="orchestrator",
            reason="admitted pre-closeout redrove the Kernel Goal gate",
        )

    def _orchestrator_checkpoint_source(self, event: ZfEvent) -> ZfEvent | None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        source_event_id = str(payload.get("source_event_id") or "")
        return next(
            (
                item for item in self.event_log.read_all()
                if item.id == source_event_id
            ),
            None,
        )

    def _on_orchestrator_run_plan_admitted(
        self,
        event: ZfEvent,
    ) -> WorkflowRuntimeDecision:
        source = self._orchestrator_checkpoint_source(event)
        if source is None:
            return WorkflowRuntimeDecision(
                action="block",
                role="orchestrator",
                reason="admitted run plan source event is missing",
            )
        self._maybe_start_writer_fanout(source)
        return WorkflowRuntimeDecision(
            action="dispatch",
            role="orchestrator",
            reason="admitted run plan redrove the fenced business graph",
        )

    def _on_orchestrator_semantic_decision(
        self,
        event: ZfEvent,
    ) -> WorkflowRuntimeDecision:
        from zf.runtime.orchestrator_agent_decision_apply import (
            apply_orchestrator_agent_decision,
        )

        payload = event.payload if isinstance(event.payload, dict) else {}
        outcome = apply_orchestrator_agent_decision(self, event)
        self.event_writer.append(ZfEvent(
            type="orchestrator.semantic.decision.observed",
            actor="zf-cli",
            origin="kernel",
            payload={
                "schema_version": "orchestrator-semantic-decision-observation.v1",
                "workflow_run_id": str(payload.get("workflow_run_id") or ""),
                "operation_id": str(payload.get("operation_id") or ""),
                "checkpoint": str(payload.get("checkpoint") or ""),
                "source_event_id": event.id,
                "status": (
                    "submitted" if event.type.endswith(".submitted") else "failed"
                ),
                "admission_status": str(outcome.get("status") or ""),
                "decision": str(outcome.get("decision") or ""),
                "reason_codes": list(outcome.get("reason_codes") or []),
                "summary": str(outcome.get("summary") or ""),
                "explanation_status": str(
                    outcome.get("explanation_status") or "degraded"
                ),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        ))
        applied = bool(outcome.get("applied"))
        if applied and outcome.get("owner_approval_required"):
            source = next((item for item in self.event_log.read_all()
                           if item.id == outcome.get("source_event_id")), None)
            if source is not None:
                self._maybe_start_writer_fanout(source)
        return WorkflowRuntimeDecision(
            action="apply" if applied else "observe",
            role="orchestrator",
            reason=(
                "typed OA decision applied through checkpoint policy"
                if applied
                else "typed OA decision observed without state mutation"
            ),
        )

    def _on_orchestrator_semantic_failure_requested(
        self,
        event: ZfEvent,
    ) -> WorkflowRuntimeDecision:
        from zf.runtime.orchestrator_agent_semantic_failure import (
            SemanticFailureCheckpointError,
            request_semantic_failure_checkpoint,
        )

        try:
            prepared = request_semantic_failure_checkpoint(self, event)
        except SemanticFailureCheckpointError as exc:
            self.event_writer.append(ZfEvent(
                type="orchestrator.semantic.checkpoint.rejected",
                actor="zf-cli",
                origin="kernel",
                task_id=event.task_id,
                payload={
                    "checkpoint": "semantic_failure",
                    "source_event_id": event.id,
                    "reason": str(exc),
                },
                causation_id=event.id,
                correlation_id=event.correlation_id,
            ))
            return WorkflowRuntimeDecision(
                action="block",
                task_id=event.task_id,
                reason=f"semantic failure checkpoint rejected: {exc}",
            )
        return WorkflowRuntimeDecision(
            action="checkpoint",
            task_id=event.task_id,
            role="orchestrator",
            reason=(
                "semantic failure compiled into durable OA operation "
                f"{prepared.operation_id}"
            ),
        )

    def _on_owner_delivery_narrative(
        self,
        event: ZfEvent,
    ) -> WorkflowRuntimeDecision:
        from zf.runtime.owner_delivery_narrative import (
            apply_owner_delivery_narrative,
        )

        outcome = apply_owner_delivery_narrative(self, event)
        return WorkflowRuntimeDecision(
            action="project",
            role="orchestrator",
            reason=(
                "owner narrative admitted into factual composite"
                if outcome.get("status") == "admitted"
                else "factual owner delivery retained with degraded narrative"
            ),
        )

    def _on_orchestrator_semantic_rework_requested(
        self,
        event: ZfEvent,
    ) -> WorkflowRuntimeDecision:
        task = self.task_store.get(str(event.task_id or ""))
        if task is None:
            return WorkflowRuntimeDecision(
                action="block",
                task_id=event.task_id,
                reason="semantic rework target task is missing",
            )
        from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
        contexts = task_pipeline_generation_contexts(self.event_log.read_all())
        if task.id in contexts:
            from zf.runtime.task_pipeline_rework import derive_impl_rework_requests
            from zf.runtime.workflow_operation import reduce_workflow_operations
            events = self.event_log.read_all()
            requests = derive_impl_rework_requests(
                events=events, generation_contexts=contexts,
                operation_rows=reduce_workflow_operations(events).values(),
            )
            return WorkflowRuntimeDecision(
                action="gate" if task.id in requests else "block",
                task_id=task.id,
                reason=("OA rework admitted to Task Pipeline ready-set" if task.id in requests
                        else "OA rework is stale, active, or requires a plan revision"),
            )
        role = self._dispatch_rework(task, event)
        return WorkflowRuntimeDecision(
            action="dispatch" if role else "block",
            task_id=event.task_id,
            role=role,
            reason=(
                "admitted OA directive dispatched to exact target"
                if role
                else "admitted OA directive failed deterministic rework gates"
            ),
        )


__all__ = ["OrchestratorAgentReactorMixin"]
