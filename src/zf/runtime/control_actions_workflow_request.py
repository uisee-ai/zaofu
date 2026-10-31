"""Controlled Project Request creation and explicit workflow ignition."""

from __future__ import annotations

from pathlib import Path

from zf.core.events import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.control_actions_helpers import _required_text, _string_list
from zf.runtime.sidecar_refs import SidecarRefError
from zf.runtime.workflow_delivery import (
    apply_flow_submit,
    build_flow_submit_preview,
)
from zf.runtime.workflow_intake import build_flow_intake
from zf.runtime.workflow_request_routing import request_revision_needed as _request_revision_needed
from zf.runtime.control_actions_workflow_replay import active_request_mutation_failure, replay_active_workflow_start
from zf.runtime.workflow_origin import (
    WorkflowOriginError,
    assert_same_workflow_origin,
    build_workflow_origin_binding,
    workflow_origin_from_request,
)
from zf.runtime.workflow_start import WorkflowStartService
from zf.runtime.workflow_request_acceptance import (
    TaskWorkflowInputCoverageError,
)
from zf.runtime.workflow_start_inputs import (
    ensure_workflow_managed_task,
    prepare_approved_workflow_start,
)
from zf.runtime.channel_execution_approval import channel_start_authorization_error
from zf.runtime.workflow_task_request_rotation import (
    WorkflowTaskRequestRotationError,
    apply_task_request_binding,
    fresh_task_request_origin_binding,
)
from zf.runtime.workflow_request_identity import request_source_ref, stable_request_id
from zf.runtime.workflow_runtime_start import ensure_submitted_workflow_runtime


WORKFLOW_CONTROL_ACTIONS = frozenset({
    "run-cancel",
    "run-pause",
    "run-resume",
    "task-workflow-start",
    "workflow-cancel",
    "workflow-invoke",
    "workflow-reject",
    "workflow-request",
    "workflow-start",
    "workflow-submit",
})


class WorkflowRequestActionsMixin:
    def _dispatch_workflow_control_action(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        if action in {"run-pause", "run-resume", "run-cancel"}:
            handler = self._run_control_action
        elif action in {"workflow-start", "task-workflow-start"}:
            handler = self._workflow_start
            action = "workflow-start"
        else:
            handler = {
                "workflow-cancel": self._workflow_cancel,
                "workflow-invoke": self._workflow_invoke,
                "workflow-reject": self._workflow_reject,
                "workflow-request": self._workflow_request,
                "workflow-submit": self._workflow_submit,
            }[action]
        from zf.core.state.locks import locked_path
        with locked_path(self.state_dir / "locks" / "workflow-preview-mutation"):
            return handler(
                requested=requested, action=action,
                requested_action=requested_action, payload=payload,
            )

    def _workflow_start(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        preview = WorkflowStartService(
            self.state_dir,
            self.config,
            project_root=self.project_root,
        ).preview(
            payload,
            require_bindings=True,
            origin=self.surface,
        )
        if not preview.get("ok"):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=str(preview.get("task_id") or "") or None,
                reason=str(preview.get("reason") or "workflow preflight blocked"),
                status_code=int(preview.get("_status_code") or 409),
                status=str(preview.get("status") or "preflight_blocked"),
            )
        normalized = dict(preview.get("payload") or {})
        task_id = str(normalized.get("task_id") or "")
        route_id = str(normalized.get("route_id") or "")
        objective = str(normalized.get("objective") or "")
        route = dict(preview.get("route") or {})
        workflow_task = TaskStore(self.state_dir / "kanban.json").get(task_id)
        readiness_error = channel_start_authorization_error(
            state_dir=self.state_dir, task=workflow_task, actor=self.actor,
            payload=payload, events=self.writer.event_log.read_all(),
        )
        if readiness_error:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=readiness_error,
                status_code=409,
                status="channel_prd_execution_blocked",
            )
        try:
            prepared = prepare_approved_workflow_start(
                state_dir=self.state_dir,
                normalized=normalized,
                route=route,
                actor=self.actor,
                reason=_required_text(payload, "reason"),
            )
        except ValueError as exc:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=str(exc),
                status_code=409,
                status="workflow_task_stale",
            )
        parameters = prepared.parameters
        workflow_task = prepared.workflow_task
        common_payload = prepared.common_payload
        ensure_workflow_managed_task(
            state_dir=self.state_dir,
            workflow_task=workflow_task,
            writer=self.writer,
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
        )
        adapter = str(route.get("start_adapter") or "")
        if normalized.get("active_request_replay"):
            result = replay_active_workflow_start(self, requested, action, requested_action, common_payload, route_id, route)
        elif adapter in {"adaptive_research", "fixed_research"}:
            result = self._research_start(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload={
                    **common_payload,
                    "template_id": str(route.get("template_id") or ""),
                    "topic": str(
                        parameters.get("topic")
                        or objective
                    ),
                },
            )
        elif adapter in {
            "delivery_request_submit",
            "light_delivery_request_submit",
            "registered_general",
        }:
            request_result = self._workflow_request(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload={
                    **common_payload,
                    "kind": str(route.get("kind") or ""),
                },
            )
            if not request_result.get("ok"):
                return {
                    **request_result,
                    "route_id": route_id,
                    "route": route,
                }
            result = self._workflow_submit(
                requested=requested,
                action=action,
                requested_action=requested_action,
                payload={
                    **common_payload,
                    "kind": str(route.get("kind") or ""),
                    "intake_ref": str(
                        request_result.get("intake_ref") or ""
                    ),
                    "request_id": str(
                        request_result.get("request_id") or ""
                    ),
                    "proposal_ref": (
                        dict(request_result.get("proposal_ref") or {})
                        if isinstance(
                            request_result.get("proposal_ref"),
                            dict,
                        )
                        else {}
                    ),
                    "proposal_digest": str(
                        request_result.get("proposal_digest") or ""
                    ),
                },
            )
            result["workflow_request"] = {
                key: request_result.get(key)
                for key in (
                    "request_id",
                    "intake_ref",
                    "workflow_input_manifest_ref",
                    "submit_preview_ref",
                    "proposal_ref",
                    "proposal_digest",
                )
            }
        else:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=f"workflow route {route_id!r} has no start adapter",
                status_code=409,
                status="workflow_route_unavailable",
            )
        result.update({
            "route_id": route_id,
            "route": route,
        })
        return ensure_submitted_workflow_runtime(result, config_path=self.project_root / "zf.yaml", config=self.config)

    def _workflow_request(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        if self.project_root is None or self.config is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="initialized project context is required",
                status_code=409,
                status="project_initialization_required",
            )
        config_ref = Path(
            _required_text(payload, "config_ref") or self.project_root / "zf.yaml"
        ).expanduser()
        if not config_ref.is_absolute():
            config_ref = self.project_root / config_ref
        if not config_ref.exists():
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=f"workflow config does not exist: {config_ref}",
                status_code=409,
                status="project_initialization_required",
            )

        from zf.runtime.workflow_prepare import task_preparation_payload, prepare_request_revision
        if (payload.get("task_id") and not payload.get("task_input_binding")
                and (payload.get("prepare_workflow") or payload.get("kind", "auto") == "auto")):
            try:
                payload = task_preparation_payload(self.state_dir, payload)
            except ValueError as exc:
                return self._failed(requested=requested, action=action,
                    requested_action=requested_action, task_id=str(payload["task_id"]),
                    reason=str(exc), status_code=409, status="workflow_task_stale")

        objective = (
            _required_text(payload, "objective")
            or _required_text(payload, "message")
            or _required_text(payload, "title")
        )
        request_id = _required_text(payload, "request_id") or stable_request_id(
            self.project_root,
            requested.id,
            objective,
        )
        source_ref = request_source_ref(self.project_root, self.state_dir, payload)
        from zf.runtime.workflow_requests import (
            WorkflowRequestError,
            load_workflow_request,
            revise_workflow_request,
        )
        existing_projection = load_workflow_request(
            self.state_dir,
            request_id,
        )
        if existing_projection:
            try:
                origin_binding = workflow_origin_from_request(
                    existing_projection
                )
                supplied_origin = payload.get("origin_binding")
                if isinstance(supplied_origin, dict):
                    assert_same_workflow_origin(
                        origin_binding,
                        supplied_origin,
                    )
                for key in (
                    "project_id",
                    "channel_id",
                    "thread_id",
                    "conversation_id",
                    "thread_key",
                ):
                    supplied = _required_text(payload, key)
                    if supplied and supplied != str(
                        origin_binding.get(key) or ""
                    ):
                        raise WorkflowOriginError(
                            "workflow origin binding does not match the "
                            "canonical request origin"
                        )
            except WorkflowOriginError as exc:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=None,
                    reason=str(exc),
                    status_code=409,
                    status="origin_binding_mismatch",
                )
            active_failure = active_request_mutation_failure(self, requested, action, requested_action, payload, existing_projection)
            if active_failure is not None:
                return active_failure
        elif bool(payload.get("fresh_request")):
            task_id = _required_text(payload, "task_id")
            task = TaskStore(self.state_dir / "kanban.json").get(task_id)
            try:
                origin_binding = fresh_task_request_origin_binding(
                    self.state_dir,
                    task,
                    payload,
                )
            except WorkflowTaskRequestRotationError as exc:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task_id or None,
                    reason=str(exc),
                    status_code=409,
                    status="workflow_task_stale",
                )
        else:
            origin_binding = build_workflow_origin_binding(
                source=self.source,
                project_id=(
                    _required_text(payload, "project_id")
                    or self.config.project.name
                ),
                channel_id=_required_text(payload, "channel_id"),
                thread_id=_required_text(payload, "thread_id"),
                conversation_id=_required_text(payload, "conversation_id"),
                thread_key=(
                    _required_text(payload, "thread_key")
                    or _required_text(payload, "thread_id")
                ),
            )
        try:
            intake = build_flow_intake(
                kind=_required_text(payload, "kind") or "auto",
                source_ref=source_ref,
                objective=objective,
                source_root=_required_text(payload, "source_root"),
                target_root=(
                    _required_text(payload, "target_root")
                    or _required_text(payload, "target")
                ),
                backend=_required_text(payload, "backend"),
                lanes=int(
                    payload.get("lanes")
                    or payload.get("requested_lanes")
                    or 0
                ),
                project_id=str(
                    origin_binding.get("project_id")
                    or self.config.project.name
                ),
                project_name=self.config.project.name,
                strictness=(
                    _required_text(payload, "strictness") or "standard"
                ),
                scope=tuple(_string_list(payload.get("scope"))),
                parity_scope=tuple(
                    _string_list(payload.get("parity_scope"))
                ),
                acceptance=tuple(
                    _string_list(payload.get("acceptance"))
                ),
                constraints=tuple(
                    _string_list(payload.get("constraints"))
                ),
                open_questions=tuple(
                    _string_list(payload.get("open_questions"))
                ),
                request_id=request_id,
                source=self.surface,
                created_by=self.actor,
                channel_id=str(origin_binding.get("channel_id") or ""),
                thread_id=str(origin_binding.get("thread_id") or ""),
                conversation_id=str(
                    origin_binding.get("conversation_id") or ""
                ),
                thread_key=str(origin_binding.get("thread_key") or ""),
                origin_binding=origin_binding,
                artifact_refs=tuple(
                    payload.get("artifact_refs")
                    if isinstance(payload.get("artifact_refs"), list)
                    else []
                ),
                source_refs=(
                    {
                        str(key): str(value)
                        for key, value in payload.get(
                            "source_refs",
                            {},
                        ).items()
                        if str(key).strip() and str(value).strip()
                    }
                    if isinstance(payload.get("source_refs"), dict)
                    else {}
                ),
                task_input_binding=(
                    dict(payload.get("task_input_binding") or {})
                    if isinstance(payload.get("task_input_binding"), dict)
                    else {}
                ),
                task_input_contract=(
                    dict(payload.get("task_input_contract") or {})
                    if isinstance(payload.get("task_input_contract"), dict)
                    else {}
                ),
                output=(
                    self.project_root
                    / "docs"
                    / "intake"
                    / f"{request_id}.md"
                ),
            )
        except (TaskWorkflowInputCoverageError, SidecarRefError) as exc:
            invalid_source = isinstance(exc, SidecarRefError)
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_required_text(payload, "task_id") or None,
                reason=f"{exc.code}: {exc}" if invalid_source else str(exc),
                status_code=422,
                status="workflow_input_invalid" if invalid_source else "workflow_input_incomplete",
            )
        except WorkflowRequestError as exc:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=str(exc),
                status_code=409,
                status="origin_binding_mismatch",
            )

        request_projection = load_workflow_request(self.state_dir, request_id)
        if existing_projection and _request_revision_needed(self.state_dir, existing_projection, payload):
            request_projection = revise_workflow_request(
                self.state_dir,
                Path(str(intake["workflow_input_manifest_ref"])),
                actor=self.actor,
                kind=(
                    _required_text(payload, "kind")
                    if _required_text(payload, "kind") not in {"", "auto"} else None
                ),
                objective=objective or None,
                source_root=(
                    _required_text(payload, "source_root") or None
                ),
                target_root=(
                    _required_text(payload, "target_root")
                    or _required_text(payload, "target")
                    or None
                ),
                acceptance=(
                    _string_list(payload.get("acceptance"))
                    if "acceptance" in payload
                    else None
                ),
                constraints=(
                    _string_list(payload.get("constraints"))
                    if "constraints" in payload
                    else None
                ),
                open_questions=(
                    _string_list(payload.get("open_questions"))
                    if "open_questions" in payload
                    else None
                ),
                confirm=True,
                revision_reason=(
                    "clarification"
                    if str(existing_projection.get("status") or "")
                    == "clarifying"
                    else "requirement_update"
                ),
                source_event_id=requested.id,
                writer=self.writer,
            )
        elif not bool(request_projection.get("confirmed")):
            request_projection = revise_workflow_request(
                self.state_dir,
                Path(str(intake["workflow_input_manifest_ref"])),
                actor=self.actor,
                confirm=True,
                writer=self.writer,
            )
        from zf.runtime.workflow_synthesis import WorkflowSynthesisError
        try:
            prepared = prepare_request_revision(
                state_dir=self.state_dir, project_root=self.project_root, config=self.config,
                writer=self.writer, request=request_projection, actor=self.actor,
                payload={**payload, "intake_ref": str(intake["intake_ref"])},
            )
        except WorkflowSynthesisError as exc:
            return self._failed(requested=requested, action=action,
                requested_action=requested_action, task_id=payload.get("task_id"),
                reason=str(exc), status_code=409, status="synthesis_enqueue_failed")
        if prepared is not None:
            return {"_status_code": 202 if prepared.get("ok") else 409, "action": action, "requested_action": requested_action,
                    "origin_binding": dict(request_projection.get("origin_binding") or {}),
                    "workflow_input_manifest_ref": str(intake["workflow_input_manifest_ref"]),
                    **prepared}
        flow_kind = _required_text(payload, "kind")
        preview = build_flow_submit_preview(
            config_path=config_ref,
            intake_path=Path(str(intake["intake_ref"])),
            flow_kind=flow_kind,
            task_id=_required_text(payload, "task_id"),
            pattern_id=(
                _required_text(payload, "pattern_id")
            ),
            requested_by=self.actor,
            reason=_required_text(payload, "reason") or "workflow request proposal",
            allow_missing_env=bool(payload.get("allow_missing_env")),
        )
        ready = preview.get("status") != "STOP"
        return {
            "_status_code": 202 if ready else 409,
            "ok": ready,
            "status": "proposal_ready" if ready else "clarification_required",
            "action": action,
            "requested_action": requested_action,
            "request_id": request_id,
            "request_revision": int(
                request_projection.get("revision") or 0
            ),
            "origin_binding": dict(
                request_projection.get("origin_binding") or {}
            ),
            "intake_ref": str(intake["intake_ref"]),
            "workflow_input_manifest_ref": str(intake["workflow_input_manifest_ref"]),
            "request_projection_ref": str(intake.get("request_projection_ref") or ""),
            "submit_preview_ref": str(preview.get("submit_preview_ref") or ""),
            "proposal_ref": (
                dict(preview.get("proposal_ref") or {})
                if isinstance(preview.get("proposal_ref"), dict)
                else {}
            ),
            "proposal_digest": str(
                (preview.get("proposal") or {}).get("proposal_digest") or ""
            ),
            "proposal": (
                dict(preview.get("proposal") or {})
                if isinstance(preview.get("proposal"), dict)
                else {}
            ),
            "synthesis_operation_id": "",
            "synthesis_ref": {},
            "blockers": list(preview.get("blockers") or []),
        }
    def _workflow_submit(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        if self.project_root is None or self.config is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="initialized project context is required",
                status_code=409,
                status="project_initialization_required",
            )
        intake_ref = _required_text(payload, "intake_ref") or _required_text(payload, "intake")
        if not intake_ref or intake_ref == "<created-intake-ref>":
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="intake_ref is required",
                status_code=422,
                status="invalid_payload",
            )
        request_id = _required_text(payload, "request_id")
        proposal_ref = payload.get("proposal_ref")
        proposal_digest = _required_text(payload, "proposal_digest")
        if (
            not request_id
            or not isinstance(proposal_ref, dict)
            or not proposal_digest
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=(
                    "request_id, proposal_ref, and proposal_digest are "
                    "required for exact Workflow Proposal approval"
                ),
                status_code=422,
                status="invalid_payload",
            )
        from zf.runtime.workflow_requests import (
            WorkflowRequestError,
            validate_current_workflow_proposal,
        )

        try:
            request_projection, proposal = validate_current_workflow_proposal(
                self.state_dir,
                request_id=request_id,
                proposal_ref=proposal_ref,
                proposal_digest=proposal_digest,
            )
        except WorkflowRequestError as exc:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=str(exc),
                status_code=409,
                status="stale_proposal",
            )
        from zf.runtime.workflow_prepare import prepared_task_error
        if error := prepared_task_error(self.state_dir, request_projection, actor=self.actor):
            return self._failed(requested=requested, action=action, requested_action=requested_action,
                task_id=payload.get("task_id"), reason=error, status_code=409, status="workflow_task_stale")
        if (
            str(proposal.get("change_mode") or "") == "config_change"
            and not any(
                event.type == "workflow.config.change.applied"
                and str(event.payload.get("proposal_digest") or "")
                == proposal_digest
                for event in self.writer.event_log.read_all()
            )
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=(
                    "config-changing Workflow Proposal must reach "
                    "workflow.config.change.applied before submit"
                ),
                status_code=409,
                status="config_apply_required",
            )
        import json
        manifest = json.loads(Path(request_projection["workflow_input_manifest_ref"]).read_text())
        bound_task_id = str((manifest.get("task_input_binding") or {}).get("task_id") or "")
        task_id = _required_text(payload, "task_id") or bound_task_id
        if bound_task_id and task_id != bound_task_id:
            return self._failed(requested=requested, action=action, requested_action=requested_action,
                task_id=task_id, reason="Task does not match approved Workflow Request", status_code=409,
                status="workflow_task_stale")
        if task_id:
            task_store = TaskStore(self.state_dir / "kanban.json")
            workflow_task = task_store.get(task_id)
            if workflow_task is not None:
                try:
                    apply_task_request_binding(
                        self.state_dir,
                        task_store=task_store,
                        event_writer=self.writer,
                        task=workflow_task,
                        request_projection=request_projection,
                        requested_event=requested,
                        actor=self.actor,
                    )
                except WorkflowTaskRequestRotationError as exc:
                    return self._failed(
                        requested=requested,
                        action=action,
                        requested_action=requested_action,
                        task_id=task_id,
                        reason=str(exc),
                        status_code=409,
                        status="workflow_task_stale",
                    )
        intake_path = Path(intake_ref).expanduser()
        if not intake_path.is_absolute():
            intake_path = self.project_root / intake_path
        config_ref = Path(
            _required_text(payload, "config_ref") or self.project_root / "zf.yaml"
        ).expanduser()
        if not config_ref.is_absolute():
            config_ref = self.project_root / config_ref
        result = apply_flow_submit(
            config_path=config_ref,
            intake_path=intake_path,
            flow_kind=_required_text(payload, "kind"),
            task_id=task_id,
            pattern_id=_required_text(payload, "pattern_id"),
            requested_by=self.actor,
            reason=_required_text(payload, "reason") or "workflow request proposal",
            allow_missing_env=bool(payload.get("allow_missing_env")),
        )
        accepted = result.get("status") != "STOP"
        return {
            "_status_code": 202 if accepted else result.get("_status_code", 409),
            "ok": accepted,
            "status": str(result.get("status") or "STOP"),
            "action": action,
            "requested_action": requested_action,
            "request_id": str((result.get("payload") or {}).get("request_id") or ""),
            "workflow_invoke_event_id": str(result.get("workflow_invoke_event_id") or ""),
            "workflow_invoke_status": str(result.get("workflow_invoke_status") or ""),
            "next_action": str(result.get("next_action") or ""),
            "runtime_start": result.get("runtime_start", {}),
            "reason": str(result.get("reason") or ""),
            "idempotent_replay": bool(result.get("idempotent_replay")),
            "event_ids": list(result.get("event_ids") or []),
            "blockers": list(result.get("blockers") or []),
        }

    def _workflow_cancel(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        request_id = _required_text(payload, "request_id")
        operation_id = _required_text(payload, "operation_id")
        request_hash = _required_text(payload, "request_hash")
        if not request_id or not operation_id or not request_hash:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=(
                    "request_id, operation_id, and request_hash are required"
                ),
                status_code=422,
                status="invalid_payload",
            )
        from zf.runtime.workflow_operation import (
            TERMINAL_OPERATION_STATUSES,
            WorkflowOperationService,
            load_workflow_operation,
        )
        from zf.runtime.workflow_requests import load_workflow_request

        request_projection = load_workflow_request(
            self.state_dir,
            request_id,
        )
        if (
            str(request_projection.get("synthesis_operation_id") or "")
            != operation_id
            or str(request_projection.get("synthesis_request_hash") or "")
            != request_hash
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="workflow synthesis cancellation identity is stale",
                status_code=409,
                status="stale_operation",
            )
        operation = load_workflow_operation(
            self.writer.event_log,
            operation_id,
        ) or {}
        status = str(operation.get("status") or "")
        if (
            str(operation.get("operation_type") or "")
            != "workflow_synthesis"
            or str(operation.get("request_hash") or "") != request_hash
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="workflow synthesis operation is unavailable",
                status_code=409,
                status="stale_operation",
            )
        if status == "cancelled":
            return {
                "ok": True,
                "status": "cancelled",
                "action": action,
                "requested_action": requested_action,
                "request_id": request_id,
                "operation_id": operation_id,
                "replayed": True,
            }
        if status in TERMINAL_OPERATION_STATUSES:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=(
                    "workflow synthesis operation is already terminal: "
                    f"{status}"
                ),
                status_code=409,
                status="operation_terminal",
            )
        reason = (
            _required_text(payload, "reason")
            or "cancelled by operator"
        )
        WorkflowOperationService(
            state_dir=self.state_dir,
            event_log=self.writer.event_log,
            event_writer=self.writer,
        ).cancel(
            operation_id=operation_id,
            request_hash=request_hash,
            workflow_run_id=str(operation.get("workflow_run_id") or ""),
            reason=reason,
            causation_id=requested.id,
            correlation_id=request_id,
        )
        self.writer.append(ZfEvent(
            type="workflow.synthesis.cancelled",
            actor=self.actor,
            causation_id=requested.id,
            correlation_id=request_id,
            payload={
                "request_id": request_id,
                "operation_id": operation_id,
                "request_hash": request_hash,
                "reason": reason,
            },
        ))
        return {
            "ok": True,
            "status": "cancelled",
            "action": action,
            "requested_action": requested_action,
            "request_id": request_id,
            "operation_id": operation_id,
            "replayed": False,
        }

    def _workflow_reject(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        request_id = _required_text(payload, "request_id")
        proposal_ref = payload.get("proposal_ref")
        proposal_digest = _required_text(payload, "proposal_digest")
        if (
            not request_id
            or not isinstance(proposal_ref, dict)
            or not proposal_digest
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=(
                    "request_id, proposal_ref, and proposal_digest are "
                    "required for Workflow Proposal rejection"
                ),
                status_code=422,
                status="invalid_payload",
            )
        from zf.runtime.workflow_requests import (
            WorkflowRequestError,
            reject_workflow_proposal,
        )

        try:
            projection = reject_workflow_proposal(
                self.state_dir,
                request_id=request_id,
                proposal_ref=proposal_ref,
                proposal_digest=proposal_digest,
                reason=_required_text(payload, "reason"),
                actor=self.actor,
                writer=self.writer,
            )
        except WorkflowRequestError as exc:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=str(exc),
                status_code=409,
                status="stale_proposal",
            )
        return {
            "_status_code": 200,
            "ok": True,
            "status": "rejected",
            "action": action,
            "requested_action": requested_action,
            "request_id": request_id,
            "proposal_digest": proposal_digest,
            "request": projection,
        }
