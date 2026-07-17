"""Durable call/return wiring for selected nested workflow invocations."""

from __future__ import annotations

from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.orchestrator_types import OrchestratorDecision
from zf.runtime.workstream_scope_guard import check_workstream_scope


_WORKFLOW_IDENTITY_KEYS = (
    "request_id",
    "run_id",
    "workflow_run_id",
    "flow_kind",
    "request_kind",
    "workflow_request_ref",
    "requirement_spec_ref",
    "requirement_spec_digest",
    "request_revision",
)


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


class DurableCallWorkflowMixin:
    """Host methods for stable nested-workflow operations."""

    def _on_workflow_invoke_requested(
        self,
        event: ZfEvent,
    ) -> OrchestratorDecision | None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        task_id = event.task_id or str(payload.get("task_id") or "")
        pattern_id = str(payload.get("pattern_id") or payload.get("stage_id") or "")
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
            return OrchestratorDecision(
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
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="workflow invoke rejected: open questions",
            )
        stage = self._workflow_stage_by_id(pattern_id)
        if stage is None:
            self._emit_workflow_invoke_rejected(
                event,
                "pattern not declared",
                task_id=task_id,
                pattern_id=pattern_id,
            )
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="workflow invoke rejected: unknown pattern",
            )
        topology = str(getattr(stage, "topology", "") or "")
        if not topology.startswith("fanout_"):
            self._emit_workflow_invoke_rejected(
                event,
                "pattern is not a fanout topology",
                task_id=task_id,
                pattern_id=pattern_id,
            )
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="workflow invoke rejected: unsupported topology",
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
            return OrchestratorDecision(
                action="block",
                task_id=task_id,
                reason="workflow invoke rejected: stale dispatch",
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
            return OrchestratorDecision(
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
            if invoke_operation.status in {"divergent", "failed", "blocked"}:
                reason = invoke_operation.reason or invoke_operation.status
                self._emit_workflow_invoke_rejected(
                    event,
                    f"workflow operation {reason}",
                    task_id=task_id,
                    pattern_id=pattern_id,
                )
                return OrchestratorDecision(
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
                if invoke_operation.status == "requested" and existing_accept is not None:
                    from zf.runtime.call_result_runtime import workflow_operation_service

                    workflow_operation_service(self).mark_started(
                        operation_id=invoke_operation.operation_id,
                        request_hash=invoke_operation.request_hash,
                        workflow_run_id=str(payload.get("workflow_run_id") or ""),
                        task_id=task_id,
                        dispatch_id=str(
                            (existing_accept.payload or {}).get("fanout_request_event_id")
                            or existing_accept.id
                        ),
                        causation_id=existing_accept.id,
                        correlation_id=event.correlation_id or "",
                    )
                return OrchestratorDecision(
                    action="observe",
                    task_id=task_id,
                    reason=(
                        "workflow invoke operation replayed without redispatch: "
                        f"{invoke_operation.operation_id}"
                    ),
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
        accepted_event.payload["fanout_request_event_id"] = fanout_request.id
        self.event_writer.append(accepted_event)
        self.event_writer.append(fanout_request)
        if invoke_operation is not None:
            from zf.runtime.call_result_runtime import workflow_operation_service

            workflow_operation_service(self).mark_started(
                operation_id=invoke_operation.operation_id,
                request_hash=invoke_operation.request_hash,
                workflow_run_id=str(payload.get("workflow_run_id") or ""),
                task_id=task_id,
                dispatch_id=fanout_request.id,
                causation_id=accepted_event.id,
                correlation_id=event.correlation_id or "",
            )
        return OrchestratorDecision(
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
        from zf.runtime.workflow_operation import stable_operation_id

        workflow_run_id = str(
            payload.get("workflow_run_id")
            or event.correlation_id
            or payload.get("request_id")
            or f"legacy-{task_id or pattern_id}"
        )
        operation_id = str(payload.get("workflow_operation_id") or "") or stable_operation_id(
            workflow_run_id=workflow_run_id,
            parent_stage_id=pattern_id,
            operation_key=task_id or pattern_id,
            operation_type="workflow",
        )
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
                "artifact_refs": payload.get("artifact_refs")
                if isinstance(payload.get("artifact_refs"), list)
                else [],
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
    ) -> OrchestratorDecision | None:
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
        return OrchestratorDecision(
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
