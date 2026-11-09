"""Durable call/return wiring for selected nested workflow invocations."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.generic_workflow_fanout import GENERIC_WORKFLOW_HANDOFF_KEYS
from zf.runtime.workflow_runtime_types import WorkflowRuntimeDecision
from zf.runtime.workstream_scope_guard import check_workstream_scope
from zf.runtime.workflow_task_lifecycle import activate_workflow_managed_task
from zf.runtime.flow_role_activation_fence import live_run_acceptance, reclaim_cancelled_activation_roles


_WORKFLOW_IDENTITY_KEYS = tuple(dict.fromkeys((
    "request_id",
    "run_id",
    "workflow_run_id",
    "flow_kind",
    "request_kind",
    "workflow_request_ref",
    "requirement_spec_ref",
    "requirement_spec_digest",
    "request_revision",
    "origin_binding",
    "workflow_proposal_ref",
    "workflow_proposal_digest",
    "effective_config_ref",
    "effective_config_digest",
    "run_contract_ref",
    "run_contract_digest",
    "route_id",
    "research_template_id",
    "research_route_digest",
    "research_template_digest",
    "research_role_activation_digest",
    "research_prompt_contract_digest",
    "research_generation_contract_ref",
    "task_contract_digest",
    "restart_boundary",
    "safe_resume_action",
    "continuation_key",
    "expected_generation",
    "fragment_id",
    "fragment_digest",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
    "provider_idempotency_key",
    "reservation_id",
    *GENERIC_WORKFLOW_HANDOFF_KEYS,
    "input_result_refs",
)))


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list | tuple | set):
        return [text for item in value if (text := str(item).strip())]
    text = str(value).strip()
    return [text] if text else []


def _target_ref(payload: dict[str, Any], stage_target_ref: str) -> str:
    explicit = str(payload.get("target_ref") or "").strip()
    if explicit:
        return explicit
    kind = str(payload.get("prompt_kind") or payload.get("kind") or "").strip().lower()
    source_refs = (
        payload.get("source_refs")
        if isinstance(payload.get("source_refs"), dict)
        else {}
    )
    source_ref = str(source_refs.get("source_ref") or "").strip()
    if kind in {"prd", "issue"} and source_ref:
        return source_ref
    return str(stage_target_ref or "").strip()


def _workflow_proposal_binding_error(
    state_dir: Path,
    payload: dict[str, Any],
) -> str:
    proposal_ref = payload.get("workflow_proposal_ref")
    proposal_digest = str(
        payload.get("workflow_proposal_digest") or ""
    )
    if not isinstance(proposal_ref, dict) and not proposal_digest:
        return ""
    effective_ref = payload.get("effective_config_ref")
    effective_digest = str(payload.get("effective_config_digest") or "")
    run_contract_ref = str(payload.get("run_contract_ref") or "")
    run_contract_digest = str(payload.get("run_contract_digest") or "")
    request_id = str(payload.get("request_id") or "")
    if (
        not isinstance(proposal_ref, dict)
        or not proposal_digest
        or not isinstance(effective_ref, dict)
        or not effective_digest
        or not run_contract_ref
        or not run_contract_digest
        or not request_id
    ):
        return "approved proposal binding is incomplete"
    try:
        from zf.runtime.run_contract import load_run_contract_snapshot
        from zf.runtime.sidecar_refs import sidecar_path
        from zf.runtime.workflow_proposal import load_workflow_proposal
        from zf.runtime.workflow_requests import load_workflow_request

        request = load_workflow_request(state_dir, request_id)
        if (
            str(request.get("status") or "")
            not in {"submitted", "running"}
            or str(request.get("proposal_digest") or "")
            != proposal_digest
            or not _same_ref(
                request.get("proposal_ref"),
                proposal_ref,
            )
        ):
            return "workflow proposal is not approved for the current request"
        proposal = load_workflow_proposal(state_dir, proposal_ref)
        if (
            str(proposal.get("proposal_digest") or "")
            != proposal_digest
            or not _same_ref(
                proposal.get("effective_config_ref"),
                effective_ref,
            )
            or str(effective_ref.get("sha256") or "")
            != effective_digest
        ):
            return "workflow proposal effective config binding is invalid"
        snapshot_path = sidecar_path(state_dir, run_contract_ref)
        if snapshot_path.is_symlink():
            return "run contract snapshot is unsafe"
        raw = snapshot_path.read_bytes()
        snapshot = load_run_contract_snapshot(
            state_dir,
            {
                "ref": run_contract_ref,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "byte_count": len(raw),
            },
        )
        contract = snapshot["contract"]
        workflow = (
            contract.get("workflow")
            if isinstance(contract.get("workflow"), dict)
            else {}
        )
        config = (
            contract.get("config")
            if isinstance(contract.get("config"), dict)
            else {}
        )
        if (
            str(snapshot.get("contract_digest") or "")
            != run_contract_digest
            or str(workflow.get("proposal_digest") or "")
            != proposal_digest
            or not _same_ref(workflow.get("proposal_ref"), proposal_ref)
            or str(config.get("effective_snapshot_digest") or "")
            != effective_digest
            or not _same_ref(
                config.get("effective_snapshot_ref"),
                effective_ref,
            )
        ):
            return "run contract does not pin the approved proposal"
    except Exception:
        return "approved proposal binding cannot be verified"
    return ""


def _same_ref(left: Any, right: Any) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return (
        str(left.get("ref") or "") == str(right.get("ref") or "")
        and str(left.get("sha256") or "") == str(right.get("sha256") or "")
    )


class DurableCallWorkflowMixin:
    """Host methods for stable nested-workflow operations."""

    def _activate_workflow_task(self, accepted_event: ZfEvent) -> None:
        updated = activate_workflow_managed_task(
            task_store=self.task_store,
            event_writer=self.event_writer,
            accepted_event=accepted_event,
        )
        if updated is not None:
            self._refresh_task_doc_projection(
                updated,
                source_event=accepted_event.type,
            )

    def _activate_replayed_workflow_task(self, event: ZfEvent, accepted: ZfEvent) -> bool:
        from zf.runtime.flow_roles import FlowRoleBindingError

        try:
            with live_run_acceptance(self, accepted.payload or {}):
                self._activate_workflow_task(accepted)
        except FlowRoleBindingError as exc:
            self._emit_workflow_invoke_rejected(
                event, str(exc), task_id=str(event.task_id or ""),
                pattern_id=str((event.payload or {}).get("pattern_id") or ""),
            )
            return False
        return True

    def _on_workflow_invoke_requested(
        self,
        event: ZfEvent,
    ) -> WorkflowRuntimeDecision | None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = event.task_id or str(payload.get("task_id") or "")
        pattern_id = str(payload.get("pattern_id") or payload.get("stage_id") or "")
        if str(payload.get("request_kind") or "") == "research":
            from zf.runtime.research_generation import (
                research_generation_binding_error,
            )

            research_task = self.task_store.get(task_id) if task_id else None
            generation_error = (
                "research_generation_task_missing"
                if research_task is None
                else research_generation_binding_error(
                    self.state_dir,
                    config=self.config,
                    task=research_task,
                    payload=payload,
                )
            )
            if generation_error:
                self._emit_workflow_invoke_rejected(
                    event,
                    generation_error,
                    task_id=task_id,
                    pattern_id=pattern_id,
                )
                return WorkflowRuntimeDecision(
                    action="block",
                    task_id=task_id,
                    reason=(
                        "workflow invoke rejected: "
                        f"{generation_error}"
                    ),
                )
        proposal_binding_error = _workflow_proposal_binding_error(
            self.state_dir,
            payload,
        )
        if not proposal_binding_error:
            from zf.runtime.workflow_config_consumer import approved_config_consumer_error

            proposal_binding_error = approved_config_consumer_error(
                self.state_dir, self.config, payload,
            )
        if proposal_binding_error:
            self._emit_workflow_invoke_rejected(
                event,
                proposal_binding_error,
                task_id=task_id,
                pattern_id=pattern_id,
            )
            return WorkflowRuntimeDecision(
                action="block",
                task_id=task_id,
                reason=(
                    "workflow invoke rejected: "
                    f"{proposal_binding_error}"
                ),
            )
        light_entry_trigger = str(payload.get("light_entry_trigger") or "").strip()
        if light_entry_trigger:
            return self._admit_light_workflow_invoke(
                event,
                payload=payload,
                task_id=task_id,
                pattern_id=pattern_id,
                entry_trigger=light_entry_trigger,
            )
        task = self.task_store.get(task_id) if task_id else None
        if task is None:
            task = self._bootstrap_invoke_task(event, payload, task_id)
        if task is None:
            self._emit_workflow_invoke_rejected(
                event,
                "task missing",
                task_id=task_id,
                pattern_id=pattern_id,
            )
            return WorkflowRuntimeDecision(
                action="block",
                task_id=task_id,
                reason="workflow invoke rejected: task missing",
            )
        if _strings(payload.get("open_questions")):
            self._emit_workflow_invoke_rejected(
                event,
                "blocking open questions",
                task_id=task_id,
                pattern_id=pattern_id,
            )
            return WorkflowRuntimeDecision(
                action="block",
                task_id=task_id,
                reason="workflow invoke rejected: open questions",
            )
        from zf.runtime.stage_definition_binding import bound_stage

        try:
            stage = bound_stage(self, payload) or self._workflow_stage_by_id(pattern_id)
        except ValueError as exc:
            self._emit_workflow_invoke_rejected(event, str(exc), task_id=task_id, pattern_id=pattern_id)
            return WorkflowRuntimeDecision(action="block", task_id=task_id, reason=str(exc))
        if stage is None:
            self._emit_workflow_invoke_rejected(
                event,
                "pattern not declared",
                task_id=task_id,
                pattern_id=pattern_id,
            )
            return WorkflowRuntimeDecision(
                action="block",
                task_id=task_id,
                reason="workflow invoke rejected: unknown pattern",
            )
        topology = str(getattr(stage, "topology", "") or "")
        if topology != "fanout_reader":
            self._emit_workflow_invoke_rejected(
                event,
                "pattern is not a supported external reader entry",
                task_id=task_id,
                pattern_id=pattern_id,
            )
            return WorkflowRuntimeDecision(
                action="block",
                task_id=task_id,
                reason="workflow invoke rejected: unsupported entry topology",
            )
        dispatch_id = str(
            payload.get("dispatch_id")
            or getattr(task, "active_dispatch_id", "")
            or ""
        )
        active_dispatch = getattr(task, "active_dispatch_id", "") or ""
        if active_dispatch and dispatch_id != active_dispatch:
            self._emit_workflow_invoke_rejected(
                event,
                "dispatch_id mismatch",
                task_id=task_id,
                pattern_id=pattern_id,
            )
            return WorkflowRuntimeDecision(
                action="block",
                task_id=task_id,
                reason="workflow invoke rejected: stale dispatch",
            )
        from zf.runtime.run_admission import admit_workflow_invoke

        admission = admit_workflow_invoke(self, event)
        if admission.status != "admitted":
            action = "observe" if admission.status in {"queued", "paused"} else "block"
            return WorkflowRuntimeDecision(
                action=action,
                task_id=task_id,
                reason=(
                    f"workflow invoke {admission.status}: "
                    f"{admission.reason or admission.run_id}"
                ),
            )
        prior_accept = next(
            (
                candidate
                for candidate in reversed(self.event_log.read_all())
                if candidate.type == "workflow.invoke.accepted"
                and str((candidate.payload or {}).get("source_event_id") or "")
                == event.id
            ),
            None,
        )
        if prior_accept is not None:
            if not self._activate_replayed_workflow_task(event, prior_accept):
                return WorkflowRuntimeDecision(action="block", task_id=task_id, reason="Run terminal on invoke replay")
        if (
            prior_accept is not None
            and not str(
                (prior_accept.payload or {}).get("workflow_operation_id")
                or ""
            )
        ):
            return WorkflowRuntimeDecision(
                action="observe",
                task_id=task_id,
                reason="workflow invoke replayed without duplicate dispatch",
            )
        proposed_paths = _strings(payload.get("paths")) + _strings(payload.get("scope"))
        scope_check = check_workstream_scope(
            self.state_dir,
            proposed_paths,
            proposed_task_id=task_id,
        )
        if not scope_check.allowed:
            self._emit_workflow_invoke_rejected(
                event,
                f"workstream_scope_overlap: {scope_check.reason}",
                task_id=task_id,
                pattern_id=pattern_id,
            )
            channel_id = str(payload.get("channel_id") or "")
            if channel_id:
                self.event_writer.append(ZfEvent(
                    type="channel.workflow.rejected",
                    actor="zf-cli",
                    task_id=task_id,
                    payload={
                        "channel_id": channel_id,
                        "thread_id": str(payload.get("thread_id") or ""),
                        "task_id": task_id,
                        "pattern_id": pattern_id,
                        "reason": "workstream_scope_overlap",
                        "overlaps": [
                            {"task_id": item.task_id, "paths": list(item.paths)}
                            for item in scope_check.overlaps
                        ],
                        "source_event_id": event.id,
                    },
                    causation_id=event.id,
                    correlation_id=event.correlation_id,
                ))
            return WorkflowRuntimeDecision(
                action="block",
                task_id=task_id,
                reason="workflow invoke rejected: workstream scope overlap",
            )
        roles = list(getattr(stage, "roles", []) or [])
        target_ref = _target_ref(
            payload,
            str(getattr(stage, "target_ref", "") or ""),
        )
        invoke_operation = self._prepare_workflow_invoke_operation(
            event=event,
            payload=payload,
            task_id=task_id,
            pattern_id=pattern_id,
            topology=topology,
            target_ref=target_ref,
            roles=roles,
        )
        if invoke_operation is not None:
            if invoke_operation.status in {
                "divergent",
                "failed",
                "blocked",
                "superseded",
            }:
                reason = invoke_operation.reason or invoke_operation.status
                self._emit_workflow_invoke_rejected(
                    event,
                    f"workflow operation {reason}",
                    task_id=task_id,
                    pattern_id=pattern_id,
                )
                return WorkflowRuntimeDecision(
                    action="block",
                    task_id=task_id,
                    reason=f"workflow invoke operation rejected: {reason}",
                )
            existing_accept = next((
                existing
                for existing in reversed(self.event_log.read_all())
                if existing.type == "workflow.invoke.accepted"
                and str((existing.payload or {}).get("workflow_operation_id") or "")
                == invoke_operation.operation_id
            ), None)
            if invoke_operation.replay_hit and (
                invoke_operation.status in {"running", "settled"}
                or existing_accept is not None
            ):
                if existing_accept is not None:
                    if not self._activate_replayed_workflow_task(event, existing_accept):
                        return WorkflowRuntimeDecision(action="block", task_id=task_id, reason="Run terminal on invoke replay")
                if invoke_operation.status in {"requested", "reserved"} and existing_accept is not None:
                    from zf.runtime.call_result_runtime import workflow_operation_service
                    from zf.runtime.workflow_operation import load_workflow_operation

                    existing_operation = load_workflow_operation(
                        self.event_log,
                        invoke_operation.operation_id,
                    ) or {}

                    workflow_operation_service(self).mark_started(
                        operation_id=invoke_operation.operation_id,
                        request_hash=invoke_operation.request_hash,
                        workflow_run_id=str(payload.get("workflow_run_id") or ""),
                        task_id=task_id,
                        dispatch_id=str(
                            (existing_accept.payload or {}).get("fanout_request_event_id")
                            or existing_accept.id
                        ),
                        reservation_id=str(
                            existing_operation.get("reservation_id") or ""
                        ),
                        idempotency_key=str(
                            existing_operation.get("idempotency_key") or ""
                        ),
                        causation_id=existing_accept.id,
                        correlation_id=event.correlation_id or "",
                    )
                return WorkflowRuntimeDecision(
                    action="observe",
                    task_id=task_id,
                    reason=(
                        "workflow invoke operation replayed without redispatch: "
                        f"{invoke_operation.operation_id}"
                    ),
                )
        from zf.runtime.flow_role_activation import activate_flow_roles
        from zf.runtime.flow_roles import FlowRoleBindingError

        try:
            activation = activate_flow_roles(
                self,
                payload=payload,
                source_event_id=event.id,
                correlation_id=event.correlation_id or "",
            )
        except FlowRoleBindingError as exc:
            self._emit_workflow_invoke_rejected(
                event,
                str(exc),
                task_id=task_id,
                pattern_id=pattern_id,
            )
            return WorkflowRuntimeDecision(
                action="block",
                task_id=task_id,
                reason=f"workflow invoke role activation rejected: {exc}",
            )
        accepted_event = ZfEvent(
            type="workflow.invoke.accepted",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "pattern_id": pattern_id,
                "channel_id": str(payload.get("channel_id") or ""),
                "thread_id": str(payload.get("thread_id") or ""),
                "source_event_id": event.id,
                "topology": topology,
                "source_refs": dict(payload.get("source_refs") or {})
                if isinstance(payload.get("source_refs"), dict)
                else {},
                "workflow_run_id": str(payload.get("workflow_run_id") or ""),
                "workflow_input_manifest_ref": str(
                    payload.get("workflow_input_manifest_ref") or ""
                ),
                "workflow_prompt_ref": str(payload.get("workflow_prompt_ref") or ""),
                "prompt_kind": str(
                    payload.get("prompt_kind") or payload.get("kind") or ""
                ),
                "artifact_refs": payload.get("artifact_refs")
                if isinstance(payload.get("artifact_refs"), list)
                else [],
                "workflow_operation_id": str(
                    payload.get("workflow_operation_id") or ""
                ),
                "workflow_operation_request_hash": str(
                    payload.get("workflow_operation_request_hash") or ""
                ),
                "flow_role_activation_id": activation.activation_id,
                "flow_role_activation_manifest_ref": (
                    activation.manifest_ref or {}
                ),
            },
            causation_id=event.id,
            correlation_id=event.correlation_id,
        )
        for key in _WORKFLOW_IDENTITY_KEYS:
            value = payload.get(key)
            if value not in (None, ""):
                accepted_event.payload[key] = value
        fanout_request = ZfEvent(
            type="task.fanout.requested",
            actor="zf-cli",
            task_id=task_id,
            payload={
                "task_id": task_id,
                "dispatch_id": dispatch_id,
                "requested_by": str(
                    payload.get("requested_by") or event.actor or "channel"
                ),
                "reason": str(payload.get("reason") or "workflow invoke accepted"),
                "scope": _strings(payload.get("scope")),
                "requested_specialists": _strings(payload.get("requested_specialists"))
                or [str(role) for role in roles],
                "expected_output": str(
                    payload.get("expected_output")
                    or f"run execution pattern {pattern_id}"
                ),
                "risk": str(payload.get("risk") or ""),
                "target_ref": target_ref,
                "source_event_id": event.id,
                "source_intent_event_id": accepted_event.id,
                "pattern_id": pattern_id,
                "channel_id": str(payload.get("channel_id") or ""),
                "thread_id": str(payload.get("thread_id") or ""),
                "source_refs": dict(payload.get("source_refs") or {})
                if isinstance(payload.get("source_refs"), dict)
                else {},
                "workflow_run_id": str(payload.get("workflow_run_id") or ""),
                "workflow_input_manifest_ref": str(
                    payload.get("workflow_input_manifest_ref") or ""
                ),
                "workflow_prompt_ref": str(payload.get("workflow_prompt_ref") or ""),
                "prompt_kind": str(
                    payload.get("prompt_kind") or payload.get("kind") or ""
                ),
                "artifact_refs": payload.get("artifact_refs")
                if isinstance(payload.get("artifact_refs"), list)
                else [],
                "parent_operation_id": str(payload.get("workflow_operation_id") or ""),
                "workflow_operation_id": str(payload.get("workflow_operation_id") or ""),
                "workflow_operation_request_hash": str(
                    payload.get("workflow_operation_request_hash") or ""
                ),
            },
            causation_id=accepted_event.id,
            correlation_id=event.correlation_id,
        )
        for key in _WORKFLOW_IDENTITY_KEYS:
            value = payload.get(key)
            if value not in (None, ""):
                fanout_request.payload[key] = value
        fanout_request.payload["workflow_invoke_admitted"] = True
        accepted_event.payload["fanout_request_event_id"] = fanout_request.id
        try:
            with live_run_acceptance(self, payload):
                self.event_writer.append(accepted_event)
                self._activate_workflow_task(accepted_event)
                self._mark_workflow_request_running(payload, accepted_event=accepted_event)
                self.event_writer.append(fanout_request)
        except FlowRoleBindingError as exc:
            from zf.runtime.flow_roles import role_configs_for_flow

            reclaim_cancelled_activation_roles(
                self, payload, list(activation.spawned_instance_ids),
                role_configs_for_flow(self.config, str(payload.get("flow_kind") or "")),
            )
            self._emit_workflow_invoke_rejected(
                event, str(exc), task_id=task_id, pattern_id=pattern_id,
            )
            return WorkflowRuntimeDecision(action="block", task_id=task_id, reason=str(exc))
        if str(getattr(stage, "trigger", "") or "") == event.type:
            self._try_start_declared_workflow_fanout(
                fanout_request,
                fanout_request.payload,
                task_id=task_id,
            )
        if invoke_operation is not None:
            from zf.runtime.call_result_runtime import workflow_operation_service

            workflow_operation_service(self).mark_started(
                operation_id=invoke_operation.operation_id,
                request_hash=invoke_operation.request_hash,
                workflow_run_id=str(payload.get("workflow_run_id") or ""),
                task_id=task_id,
                dispatch_id=fanout_request.id,
                reservation_id=str(payload.get("reservation_id") or ""),
                idempotency_key=str(
                    payload.get("provider_idempotency_key") or ""
                ),
                causation_id=accepted_event.id,
                correlation_id=event.correlation_id or "",
            )
        return WorkflowRuntimeDecision(
            action="workflow_invoke",
            task_id=task_id,
            reason=f"workflow invoke accepted: {pattern_id}",
        )

    def _prepare_workflow_invoke_operation(
        self,
        *,
        event: ZfEvent,
        payload: dict[str, Any],
        task_id: str,
        pattern_id: str,
        topology: str,
        target_ref: str,
        roles: list[str],
    ):
        from zf.runtime.call_result_admission import result_protocol_mode

        mode = result_protocol_mode(self.config, payload)
        if mode == "shadow" and not bool(payload.get("durable_operation")):
            return None
        from zf.runtime.call_result_runtime import workflow_operation_service
        from zf.runtime.workflow_operation import (
            EnsureOperationResult,
            load_workflow_operation,
            stable_operation_id,
        )

        workflow_run_id = str(
            payload.get("workflow_run_id")
            or event.correlation_id
            or payload.get("request_id")
            or f"legacy-{task_id or pattern_id}"
        )
        operation_key = task_id or pattern_id
        workflow_generation = str(
            payload.get("workflow_generation")
            or payload.get("workflow_proposal_digest")
            or ""
        )
        if workflow_generation:
            operation_key = f"{operation_key}:generation:{workflow_generation}"
        rework_attempt = int(payload.get("rework_attempt") or 0)
        if rework_attempt > 0:
            operation_key = f"{operation_key}:rework:{rework_attempt}"
        supplied_operation_id = (
            ""
            if rework_attempt > 0
            else str(payload.get("workflow_operation_id") or "")
        )
        operation_id = supplied_operation_id or stable_operation_id(
            workflow_run_id=workflow_run_id,
            parent_stage_id=pattern_id,
            operation_key=operation_key,
            operation_type="workflow",
        )
        supplied_request_hash = (
            ""
            if rework_attempt > 0
            else str(payload.get("workflow_operation_request_hash") or "")
        )
        existing_operation = (
            load_workflow_operation(self.event_log, operation_id)
            if supplied_request_hash
            else None
        )
        if supplied_request_hash:
            if existing_operation is None:
                return EnsureOperationResult(
                    status="divergent",
                    operation_id=operation_id,
                    request_hash=supplied_request_hash,
                    reason="prepared_workflow_operation_missing",
                )
            existing_hash = str(existing_operation.get("request_hash") or "")
            if existing_hash != supplied_request_hash:
                return EnsureOperationResult(
                    status="divergent",
                    operation_id=operation_id,
                    request_hash=supplied_request_hash,
                    reason="prepared_workflow_operation_hash_mismatch",
                )
            ensured = EnsureOperationResult(
                status=str(existing_operation.get("status") or "requested"),
                operation_id=operation_id,
                request_hash=existing_hash,
                replay_hit=True,
                admitted_call_result_ref=str(
                    (
                        existing_operation.get("admitted_call_result_ref")
                        if isinstance(
                            existing_operation.get("admitted_call_result_ref"),
                            dict,
                        )
                        else {}
                    ).get("ref")
                    or ""
                ),
                admitted_call_result_digest=str(
                    (
                        existing_operation.get("admitted_call_result_ref")
                        if isinstance(
                            existing_operation.get("admitted_call_result_ref"),
                            dict,
                        )
                        else {}
                    ).get("sha256")
                    or ""
                ),
            )
        else:
            ensured = workflow_operation_service(self).ensure_operation(
                workflow_run_id=workflow_run_id,
                operation_id=operation_id,
                operation_type="workflow",
                request={
                    "task_id": task_id,
                    "pattern_id": pattern_id,
                    "topology": topology,
                    "target_ref": target_ref,
                    "roles": list(roles),
                    "scope": _strings(payload.get("scope")),
                    "requested_specialists": _strings(
                        payload.get("requested_specialists")
                    ),
                    "expected_output": str(payload.get("expected_output") or ""),
                    "workflow_input_manifest_ref": str(
                        payload.get("workflow_input_manifest_ref") or ""
                    ),
                    "workflow_proposal_ref": (
                        dict(payload["workflow_proposal_ref"])
                        if isinstance(
                            payload.get("workflow_proposal_ref"),
                            dict,
                        )
                        else {}
                    ),
                    "workflow_proposal_digest": str(
                        payload.get("workflow_proposal_digest") or ""
                    ),
                    "effective_config_ref": (
                        dict(payload["effective_config_ref"])
                        if isinstance(
                            payload.get("effective_config_ref"),
                            dict,
                        )
                        else {}
                    ),
                    "effective_config_digest": str(
                        payload.get("effective_config_digest") or ""
                    ),
                    "artifact_refs": payload.get("artifact_refs")
                    if isinstance(payload.get("artifact_refs"), list)
                    else [],
                    "rework_of": str(payload.get("rework_of") or ""),
                    "rework_attempt": rework_attempt,
                    "rework_feedback": (
                        list(payload.get("rework_feedback") or [])
                        if isinstance(payload.get("rework_feedback"), list)
                        else []
                    ),
                },
                parent_operation_id=str(payload.get("parent_operation_id") or ""),
                parent_stage_id=pattern_id,
                task_id=task_id,
                child_task_ids=[task_id] if task_id else [],
                causation_id=event.id,
                correlation_id=event.correlation_id or workflow_run_id,
            )
        payload.update({
            "workflow_run_id": workflow_run_id,
            "workflow_operation_id": operation_id,
            "workflow_operation_request_hash": ensured.request_hash,
            "result_protocol_mode": mode,
        })
        return ensured

    def _on_durable_fanout_aggregate_completed(
        self,
        event: ZfEvent,
    ) -> WorkflowRuntimeDecision | None:
        """Settle a durable parent from synchronous fanout/recovery paths.

        This is intentionally not a reactor-table handler: ordinary fanout
        aggregates are kernel-emitted and must not wake the orchestrator just
        to consume their own event. Aggregate writers call this synchronously,
        while the fanout recovery sweep replays an append-before-settle crash.
        """
        payload = event.payload if isinstance(event.payload, dict) else {}
        fanout_id = str(payload.get("fanout_id") or "").strip()
        if not fanout_id:
            return None
        manifest = self._fanout_manifest(fanout_id)
        if not manifest:
            return None
        trigger_payload = (
            manifest.get("trigger_payload")
            if isinstance(manifest.get("trigger_payload"), dict)
            else {}
        )
        operation_id = str(
            trigger_payload.get("workflow_operation_id")
            or trigger_payload.get("parent_operation_id")
            or ""
        ).strip()
        if not operation_id:
            return None

        from zf.runtime.call_result_admission import result_protocol_mode
        from zf.runtime.call_result_runtime import admit_runtime_call_result
        from zf.runtime.workflow_operation import load_workflow_operation

        mode = result_protocol_mode(self.config, trigger_payload)
        if mode == "shadow" and not bool(trigger_payload.get("durable_operation")):
            return None
        operation = load_workflow_operation(self.event_log, operation_id)
        if operation is None:
            return None
        request_hash = str(
            trigger_payload.get("workflow_operation_request_hash")
            or operation.get("request_hash")
            or ""
        ).strip()
        if not request_hash:
            return None

        child_refs: list[dict[str, Any]] = []
        seen_refs: set[tuple[str, str]] = set()
        candidates = list(manifest.get("children", []) or [])
        if not candidates:
            candidates = []
        for child in candidates:
            if not isinstance(child, dict):
                continue
            descriptor = child.get("admitted_call_result_ref")
            if not isinstance(descriptor, dict):
                child_payload = (
                    child.get("payload")
                    if isinstance(child.get("payload"), dict)
                    else {}
                )
                descriptor = child_payload.get("admitted_call_result_ref")
            self._append_admitted_child_ref(child_refs, seen_refs, descriptor)
        if not child_refs:
            for candidate in self.event_log.read_all():
                if candidate.type not in {
                    "fanout.child.completed",
                    "fanout.child.failed",
                }:
                    continue
                body = candidate.payload if isinstance(candidate.payload, dict) else {}
                if str(body.get("fanout_id") or "") != fanout_id:
                    continue
                self._append_admitted_child_ref(
                    child_refs,
                    seen_refs,
                    body.get("admitted_call_result_ref"),
                )

        workflow_run_id = str(
            trigger_payload.get("workflow_run_id")
            or operation.get("workflow_run_id")
            or event.correlation_id
            or ""
        )
        outcome = admit_runtime_call_result(
            self,
            event,
            merged_payload={
                **payload,
                "workflow_run_id": workflow_run_id,
                "workflow_operation_id": operation_id,
                "workflow_operation_request_hash": request_hash,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "attempt_id": fanout_id,
                "producer_role": "fanout-aggregate",
                "child_call_result_refs": child_refs,
                "result_protocol_mode": mode,
            },
            mode=mode,
            dispatch_correction=False,
        )
        return WorkflowRuntimeDecision(
            action="observe",
            task_id=str(event.task_id or operation.get("task_id") or ""),
            reason=f"nested workflow aggregate {outcome.status}: {operation_id}",
        )

    @staticmethod
    def _append_admitted_child_ref(
        child_refs: list[dict[str, Any]],
        seen_refs: set[tuple[str, str]],
        descriptor: Any,
    ) -> None:
        if not isinstance(descriptor, dict):
            return
        key = (
            str(descriptor.get("ref") or ""),
            str(descriptor.get("sha256") or ""),
        )
        if not all(key) or key in seen_refs:
            return
        seen_refs.add(key)
        child_refs.append(dict(descriptor))
