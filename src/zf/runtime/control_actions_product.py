"""ProductActionsMixin — controlled-action handlers (moved verbatim from control_actions.py)."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any
from zf.core.events import ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions_helpers import _approval_ref
from zf.runtime.control_actions_helpers import _optional_str
from zf.runtime.control_actions_helpers import _proposal_id
from zf.runtime.control_actions_helpers import _requested_fanout_id
from zf.runtime.control_actions_helpers import _required_text
from zf.runtime.control_actions_helpers import _string_list
from zf.runtime.control_actions_helpers import _task_contract_from_payload
from zf.runtime.control_actions_helpers import _task_id_from_payload
from zf.runtime.control_actions_helpers import _task_metadata_payload
from zf.runtime.control_actions_helpers import _task_priority
from zf.runtime.control_actions_helpers import _task_updates_from_payload
from zf.runtime.control_actions_helpers import _workflow_stage
from zf.runtime.operator_intent import infer_operator_intent
from zf.runtime.operator_intent import validate_operator_intent_payload
from zf.runtime.workflow_inputs import infer_workflow_prompt_kind
from zf.runtime.workflow_inputs import normalize_artifact_refs
from zf.runtime.workflow_inputs import normalize_source_refs
from zf.runtime.workflow_inputs import workflow_input_manifest_ref
from zf.runtime.workflow_inputs import workflow_run_id_for
from zf.runtime.workflow_inputs import write_workflow_input_manifest
from zf.runtime.workflow_inputs import write_workflow_prompt_package
from zf.runtime.kanban_plan_requests import (
    PLAN_REQUESTED_EVENT,
    PLAN_REQUEST_SCHEMA_VERSION,
    normalize_plan_request_revision,
)
from zf.runtime.kanban_proposals import (
    LEGACY_PROPOSAL_EVENT,
    LEGACY_PROPOSAL_RESOLVED_EVENT,
    PROPOSAL_EVENT_TYPES,
    PROPOSAL_RESOLVED_EVENT,
)
from zf.runtime.channel_prd_task_contract import (
    ChannelPrdTaskContractError,
    compile_channel_prd_task_payload,
)
from zf.runtime.channel_workflow_authority import (
    channel_authority_context_from_submit_payload,
)
from zf.runtime.task_workflow_plans import (
    build_task_workflow_plan_request,
)
from zf.runtime.workflow_anchor import (
    bind_workflow_request_to_task,
    mark_workflow_managed_task,
    workflow_task_request_binding,
)
from zf.runtime.workflow_origin import workflow_origin_digest
from zf.runtime.workflow_requests import (
    WorkflowRequestError,
    require_current_workflow_request,
)
from zf.runtime.research_generation import (
    ResearchGenerationError,
    materialize_research_generation,
    supersede_active_research_generations,
)
from zf.runtime.research_templates import resolve_research_template


class ProductActionsMixin:
    def _create_task(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        title = str(payload.get("title") or "").strip()
        if not title:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="title is required",
                status_code=422,
                status="invalid_payload",
            )

        store = TaskStore(self.state_dir / "kanban.json")
        task_id = str(payload.get("task_id") or payload.get("id") or "").strip()
        if task_id and store.get(task_id) is not None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=f"task {task_id!r} already exists",
                status_code=409,
                status="conflict",
            )

        execution_mode = str(payload.get("execution_mode") or "").strip()
        if not execution_mode and self.source == "kanban-agent":
            payload = dict(payload)
            payload["execution_mode"] = "workflow"
            execution_mode = "workflow"
        if execution_mode and execution_mode not in {"direct", "workflow"}:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id or None,
                reason="execution_mode must be direct or workflow",
                status_code=422,
                status="invalid_payload",
            )
        request_id = _required_text(payload, "request_id")
        if execution_mode == "direct" and (
            request_id or payload.get("workflow_plan") is not None
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id or None,
                reason=(
                    "execution_mode direct cannot include a workflow Plan "
                    "or Workflow Request binding"
                ),
                status_code=422,
                status="invalid_payload",
            )
        try:
            request_revision = int(payload.get("request_revision") or 0)
        except (TypeError, ValueError):
            request_revision = 0
        request_projection: dict = {}
        if request_id:
            try:
                request_projection = require_current_workflow_request(
                    self.state_dir,
                    request_id,
                    request_revision,
                )
            except WorkflowRequestError as exc:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task_id or None,
                    reason=str(exc),
                    status_code=409,
                    status="stale_or_missing_request",
                )

        channel_authority = channel_authority_context_from_submit_payload(
            payload
        )
        if channel_authority:
            try:
                payload = compile_channel_prd_task_payload(
                    self.state_dir,
                    payload,
                )
            except ChannelPrdTaskContractError as exc:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task_id or None,
                    reason=str(exc),
                    status_code=409,
                    status="channel_prd_not_ready",
                )
            execution_mode = str(payload.get("execution_mode") or "").strip()

        task = Task(
            id=task_id or Task().id,
            title=title,
            key=str(payload.get("key") or payload.get("feature_id") or ""),
            priority=_task_priority(payload.get("priority")),
            assigned_to=_optional_str(payload.get("assigned_to") or payload.get("owner")),
            skills_required=_string_list(payload.get("skills_required") or payload.get("skills")),
            blocked_by=_string_list(payload.get("blocked_by")),
            contract=_task_contract_from_payload(payload.get("contract")),
        )
        raw_workflow_plan = payload.get("workflow_plan")
        workflow_plan_warning = ""
        if (
            execution_mode == "workflow"
            or raw_workflow_plan is not None
            or request_id
        ):
            workflow_candidate = mark_workflow_managed_task(deepcopy(task))
            if request_id:
                workflow_candidate = bind_workflow_request_to_task(
                    workflow_candidate,
                    request_id=request_id,
                    request_revision=request_revision,
                    origin_binding_digest=workflow_origin_digest(
                        request_projection["origin_binding"]
                    ),
                )
            task = workflow_candidate
        if (
            channel_authority
            and self.config is not None
            and self.project_root is not None
        ):
            from zf.core.task.contract_validation import validate_task_contract

            contract_errors = validate_task_contract(
                task,
                config=self.config,
                project_root=self.project_root,
            )
            if contract_errors:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task.id,
                    reason="; ".join(contract_errors),
                    status_code=422,
                    status="task_contract_invalid",
                )
        if raw_workflow_plan is not None:
            _workflow_preview, workflow_plan_warning = (
                build_task_workflow_plan_request(
                    raw_workflow_plan,
                    task=workflow_candidate,
                    task_event_id="",
                    config=self.config,
                )
            )
            if workflow_plan_warning and channel_authority:
                # A canonical Channel PRD handoff promises a second, Task-bound
                # Workflow Plan. Refuse a partial handoff when the active route
                # catalog or parameters can no longer produce that Plan.
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task.id,
                    reason=(
                        "Channel PRD workflow handoff preflight failed: "
                        + workflow_plan_warning
                    ),
                    status_code=422,
                    status="channel_prd_handoff_invalid",
                )
        try:
            created = store.add(task)
        except Exception as exc:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task.id,
                reason=str(exc),
                status_code=409,
            )

        event = self.writer.emit(
            "task.created",
            actor=self.actor,
            task_id=created.id,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "source": self.source,
                "task": redact_obj(asdict(created)),
                "request": redact_obj(payload),
                "proposal_event_id": str(
                    payload.get("proposal_event_id") or ""
                ),
            },
        )
        workflow_plan_event_id = ""
        if raw_workflow_plan is not None:
            workflow_plan, workflow_plan_warning = (
                build_task_workflow_plan_request(
                    raw_workflow_plan,
                    task=created,
                    task_event_id=event.id,
                    config=self.config,
                    context={
                        "project_id": str(payload.get("project_id") or ""),
                        "conversation_id": str(
                            payload.get("conversation_id") or ""
                        ),
                        "thread_id": str(payload.get("thread_id") or ""),
                        "turn_id": str(payload.get("run_id") or ""),
                        "backend": str(payload.get("backend") or ""),
                        "originating_message_event_id": str(
                            payload.get("causation_id")
                            or payload.get(
                                "originating_message_event_id"
                            )
                            or event.id
                        ),
                    },
                )
            )
            if workflow_plan is not None:
                workflow_plan = normalize_plan_request_revision(
                    self.writer.event_log.read_all(),
                    workflow_plan,
                )
                plan_event = ZfEvent(
                    type=PLAN_REQUESTED_EVENT,
                    actor=self.actor,
                    task_id=created.id,
                    causation_id=event.id,
                    correlation_id=requested.correlation_id,
                )
                workflow_plan["request_event_id"] = plan_event.id
                plan_event.payload = {
                    "schema_version": PLAN_REQUEST_SCHEMA_VERSION,
                    "source": self.source,
                    "project_id": str(payload.get("project_id") or ""),
                    "conversation_id": str(
                        payload.get("conversation_id") or ""
                    ),
                    "thread_key": str(payload.get("thread_id") or ""),
                    "plan_request": redact_obj(workflow_plan),
                    "request": redact_obj(workflow_plan),
                }
                self.writer.append(plan_event)
                workflow_plan_event_id = plan_event.id
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="completed",
            task_id=created.id,
            extra={
                "task_id": created.id,
                "workflow_plan_event_id": workflow_plan_event_id,
                "workflow_plan_warning": workflow_plan_warning,
            },
        )
        return {
            "_status_code": 201,
            "ok": True,
            "status": "completed",
            "action": action,
            "requested_action": requested_action,
            "reason": f"task {created.id} created through controlled action",
            "event_id": event.id,
            "task_id": created.id,
            "workflow_plan_event_id": workflow_plan_event_id,
            "workflow_plan_warning": workflow_plan_warning,
            "result": {"task": redact_obj(asdict(created))},
        }
    def _kanban_proposal_dismiss(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        """chat-e2e F2: operator dismisses a pending kanban-agent proposal —
        the resolved event collapses it out of the durable pending list."""
        proposal_event_id = str(payload.get("proposal_event_id") or "").strip()
        if not proposal_event_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="proposal_event_id is required",
                status_code=422,
                status="invalid_payload",
            )
        source_event = next(
            (
                event
                for event in self.writer.event_log.read_all()
                if event.id == proposal_event_id
                and event.type in PROPOSAL_EVENT_TYPES
            ),
            None,
        )
        source_payload = (
            source_event.payload
            if source_event is not None
            and isinstance(source_event.payload, dict)
            else {}
        )
        proposal = (
            source_payload.get("proposal")
            if isinstance(source_payload.get("proposal"), dict)
            else {}
        )
        resolution_type = (
            LEGACY_PROPOSAL_RESOLVED_EVENT
            if source_event is not None
            and source_event.type == LEGACY_PROPOSAL_EVENT
            else PROPOSAL_RESOLVED_EVENT
        )
        proposal_payload = (
            proposal.get("payload")
            if isinstance(proposal.get("payload"), dict)
            else {}
        )
        proposal_task_id = str(
            (source_event.task_id if source_event is not None else "")
            or proposal_payload.get("task_id")
            or ""
        ).strip()
        event = self.writer.emit(
            resolution_type,
            actor=self.actor,
            task_id=proposal_task_id or None,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                **{
                    key: source_payload[key]
                    for key in (
                        "conversation_id",
                        "project_id",
                        "thread_key",
                        "turn_id",
                    )
                    if source_payload.get(key) not in (None, "")
                },
                "proposal_event_id": proposal_event_id,
                "proposal_id": str(proposal.get("proposal_id") or ""),
                "proposal_digest": str(
                    proposal.get("proposal_digest") or ""
                ),
                "revision": int(proposal.get("revision") or 1),
                "resolution": "dismissed",
                "reason": str(payload.get("reason") or ""),
                "source": self.source,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="completed",
            task_id=proposal_task_id or None,
            extra={"proposal_event_id": proposal_event_id},
        )
        return {
            "_status_code": 200,
            "ok": True,
            "status": "completed",
            "action": action,
            "requested_action": requested_action,
            "reason": f"proposal {proposal_event_id} dismissed",
            "event_id": event.id,
            "proposal_event_id": proposal_event_id,
            "task_id": proposal_task_id,
        }

    def _capture_regression_case(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        """design 101 §8 C — capture a failed task as a deterministic
        regression eval case (artifact + event). No LLM judge."""
        from zf.runtime.regression_case import (
            REGRESSION_CASE_CAPTURED,
            capture_regression_case,
        )

        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="task_id is required",
                status_code=422,
                status="invalid_payload",
            )
        case_id = str(payload.get("case_id") or f"rc-{task_id}-{requested.id[-8:]}")
        case = capture_regression_case(
            self.state_dir,
            case_id=case_id,
            source_task_id=task_id,
            feature_id=str(payload.get("feature_id") or ""),
            source_event_ids=tuple(str(e) for e in (payload.get("source_event_ids") or [])),
            command=str(payload.get("command") or ""),
            assertions=tuple(str(a) for a in (payload.get("assertions") or [])),
            captured_at=str(payload.get("captured_at") or ""),
            provenance={"source": self.source, "actor": self.actor},
        )
        event = self.writer.emit(
            REGRESSION_CASE_CAPTURED,
            actor=self.actor,
            task_id=task_id,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "source": self.source,
                "case_id": case.case_id,
                "assertions": list(case.assertions),
                "source_event_ids": list(case.source_event_ids),
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="completed",
            task_id=task_id,
            extra={"case_id": case.case_id},
        )
        return {
            "_status_code": 201,
            "ok": True,
            "status": "completed",
            "action": action,
            "requested_action": requested_action,
            "reason": f"regression case {case.case_id} captured",
            "event_id": event.id,
            "task_id": task_id,
            "result": {"case_id": case.case_id, "assertions": list(case.assertions)},
        }

    def _replay_regression_case(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        """design 101 §8 E — replay a captured regression case: evaluate its
        deterministic assertions against current facts (recomputed from
        events for the source task). No LLM, no agent re-run."""
        from zf.core.events.log import EventLog
        from zf.runtime.regression_case import (
            list_regression_cases,
            replay_regression_case,
        )

        case_id = str(payload.get("case_id") or "").strip()
        if not case_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="case_id is required",
                status_code=422,
                status="invalid_payload",
            )
        case = next(
            (c for c in list_regression_cases(self.state_dir) if c.case_id == case_id),
            None,
        )
        if case is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason=f"regression case {case_id!r} not found",
                status_code=404,
                status="not_found",
            )
        # assemble deterministic facts for the source task from events
        rework = 0
        scope_violation = 0
        try:
            for event in EventLog(self.state_dir / "events.jsonl").read_all():
                if str(getattr(event, "task_id", "") or "") != case.source_task_id:
                    continue
                etype = str(getattr(event, "type", "") or "")
                if etype == "task.rework.requested":
                    rework += 1
                elif etype == "scope.violation":
                    scope_violation += 1
        except Exception:
            pass
        verdict = replay_regression_case(
            case,
            facts={"rework": rework, "scope_violation": scope_violation},
            run_command=bool(payload.get("run_command")),
        )
        event = self.writer.emit(
            "regression.case.replayed",
            actor=self.actor,
            task_id=case.source_task_id,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "source": self.source,
                "case_id": case_id,
                "passed": verdict["passed"],
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="completed",
            task_id=case.source_task_id,
            extra={"case_id": case_id, "passed": verdict["passed"]},
        )
        return {
            "_status_code": 200,
            "ok": True,
            "status": "completed",
            "action": action,
            "requested_action": requested_action,
            "reason": f"regression case {case_id} replayed: {'PASS' if verdict['passed'] else 'FAIL'}",
            "event_id": event.id,
            "result": verdict,
        }

    def _update_task(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=None,
                reason="task_id is required",
                status_code=422,
                status="invalid_payload",
            )

        store = TaskStore(self.state_dir / "kanban.json")
        task = store.get(task_id)
        if task is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=f"task {task_id!r} not found",
                status_code=404,
                status="not_found",
            )

        updates = _task_updates_from_payload(task, payload)
        if not updates:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason="no supported task fields to update",
                status_code=422,
                status="invalid_payload",
            )

        updated = store.update(task_id, **updates)
        if updated is None:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=task_id,
                reason=f"task {task_id!r} not found",
                status_code=404,
                status="not_found",
            )

        event = self.writer.emit(
            "task.updated",
            actor=self.actor,
            task_id=task_id,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "source": self.source,
                "updates": redact_obj(updates),
                "unsupported_metadata": redact_obj(_task_metadata_payload(payload)),
                "task": redact_obj(asdict(updated)),
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="completed",
            task_id=task_id,
            extra={"task_id": task_id},
        )
        return {
            "ok": True,
            "status": "completed",
            "action": action,
            "requested_action": requested_action,
            "reason": f"task {task_id} updated through controlled action",
            "event_id": event.id,
            "task_id": task_id,
            "result": {"task": redact_obj(asdict(updated))},
        }
    def _request_fanout(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        stage_id = str(payload.get("stage_id") or "")
        stage = _workflow_stage(self.config, stage_id)
        topology = str(getattr(stage, "topology", "") or "")
        target_ref = str(payload.get("target_ref") or getattr(stage, "target_ref", "") or "")
        fanout_id = str(payload.get("fanout_id") or "") or _requested_fanout_id(stage_id, payload)
        task_id = _task_id_from_payload(payload)
        event = self.writer.emit(
            "fanout.requested",
            actor=self.actor,
            task_id=task_id,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "fanout_id": fanout_id,
                "stage_id": stage_id,
                "topology": topology,
                "target_ref": target_ref,
                "pdd_id": str(payload.get("pdd_id") or ""),
                "trace_id": str(payload.get("trace_id") or requested.correlation_id or ""),
                "requested_by": str(
                    payload.get("requested_by")
                    or ("kanban" if self.surface == "web" else self.source)
                ),
                "reason": str(payload.get("reason") or ""),
                "channel_id": str(payload.get("channel_id") or ""),
                "thread_id": str(payload.get("thread_id") or ""),
                "source_refs": (
                    dict(payload.get("source_refs"))
                    if isinstance(payload.get("source_refs"), dict)
                    else {}
                ),
                "artifact_refs": normalize_artifact_refs(payload),
                "runtime_delivery": "queued_no_runtime",
                "request": redact_obj(payload),
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="requested",
            task_id=task_id,
            extra={"fanout_id": fanout_id, "stage_id": stage_id},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "requested",
            "action": action,
            "requested_action": requested_action,
            "reason": "fanout request recorded; orchestrator runtime owns child dispatch",
            "fanout_id": fanout_id,
            "event_id": event.id,
        }
    def _workflow_invoke(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        task_id = _required_text(payload, "task_id")
        pattern_id = _required_text(payload, "pattern_id")
        task = TaskStore(self.state_dir / "kanban.json").get(task_id)
        request_id = _required_text(payload, "request_id")
        try:
            request_revision = int(payload.get("request_revision") or 0)
        except (TypeError, ValueError):
            request_revision = 0
        request_projection: dict = {}
        if request_id:
            try:
                request_projection = require_current_workflow_request(
                    self.state_dir,
                    request_id,
                    request_revision,
                )
            except WorkflowRequestError as exc:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task_id or None,
                    reason=str(exc),
                    status_code=409,
                    status="stale_or_missing_request",
                )
            task_request = (
                workflow_task_request_binding(task)
                if task is not None
                else {}
            )
            if (
                not task_request
                or task_request["request_id"] != request_id
                or int(task_request["request_revision"])
                != request_revision
                or task_request.get("origin_binding_digest")
                != workflow_origin_digest(
                    request_projection["origin_binding"]
                )
            ):
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task_id or None,
                    reason=(
                        "workflow Task is not bound to the current "
                        "Workflow Request"
                    ),
                    status_code=409,
                    status="workflow_task_stale",
                )
            payload = dict(payload)
            payload["request_id"] = request_id
            payload["request_revision"] = request_revision
            payload["origin_binding"] = dict(
                request_projection["origin_binding"]
            )
            origin = request_projection["origin_binding"]
            supplied_channel = _required_text(payload, "channel_id")
            supplied_thread = _required_text(payload, "thread_id")
            canonical_channel = str(origin.get("channel_id") or "")
            canonical_thread = str(origin.get("thread_id") or "")
            if (
                supplied_channel and supplied_channel != canonical_channel
            ) or (
                supplied_thread and supplied_thread != canonical_thread
            ):
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task_id or None,
                    reason=(
                        "workflow return target does not match the "
                        "Workflow Request origin"
                    ),
                    status_code=409,
                    status="origin_binding_mismatch",
                )
            payload["channel_id"] = canonical_channel
            payload["thread_id"] = canonical_thread
        origin_binding = (
            request_projection.get("origin_binding")
            if isinstance(request_projection.get("origin_binding"), dict)
            else {}
        )
        event = ZfEvent(
            type="workflow.invoke.requested",
            actor=self.actor,
            task_id=task_id,
            causation_id=requested.id,
            correlation_id=str(
                origin_binding.get("channel_id")
                or origin_binding.get("conversation_id")
                or payload.get("channel_id")
                or requested.correlation_id
                or ""
            ),
        )
        workflow_run_id = workflow_run_id_for(
            event_id=event.id,
            task_id=task_id,
            pattern_id=pattern_id,
        )
        if task is not None:
            from dataclasses import replace

            from zf.runtime.task_contract_authority import (
                TaskContractAuthorityService,
                task_execution_binding,
            )

            binding = task_execution_binding(task)
            if binding.workflow_run_id != workflow_run_id:
                mutation = TaskContractAuthorityService(
                    task_store=TaskStore(self.state_dir / "kanban.json"),
                    event_writer=self.writer,
                    state_dir=self.state_dir,
                ).replace(
                    task,
                    contract=task.contract,
                    execution_binding=replace(
                        binding,
                        workflow_run_id=workflow_run_id,
                    ),
                    source="workflow_invoke_run_binding",
                    actor=self.actor,
                    causation_id=requested.id,
                    correlation_id=workflow_run_id,
                    audit_payload={
                        "request_id": request_id,
                        "request_revision": request_revision,
                        "workflow_run_id": workflow_run_id,
                    },
                )
                task = mutation.task
        manifest_ref = workflow_input_manifest_ref(workflow_run_id)
        artifact_refs = normalize_artifact_refs(payload)
        source_refs = normalize_source_refs(
            payload,
            task_id=task_id,
            pattern_id=pattern_id,
            workflow_run_id=workflow_run_id,
            workflow_input_manifest_ref=manifest_ref,
            workflow_invoke_event_id=event.id,
            requested_event_id=requested.id,
            source=self.surface,
        )
        event.payload = {
            "task_id": task_id,
            "pattern_id": pattern_id,
            "dispatch_id": str(payload.get("dispatch_id") or ""),
            "requested_by": str(payload.get("requested_by") or self.source),
            "reason": str(payload.get("reason") or ""),
            "source": self.surface,
            "source_refs": source_refs,
            "workflow_run_id": workflow_run_id,
            "workflow_input_manifest_ref": manifest_ref,
            "flow_kind": str(payload.get("flow_kind") or ""),
            "request_kind": str(payload.get("request_kind") or ""),
            "route_id": str(payload.get("route_id") or ""),
            "research_template_id": str(
                payload.get("research_template_id") or ""
            ),
            "task_contract_digest": str(
                payload.get("task_contract_digest") or ""
            ),
            "artifact_refs": artifact_refs,
            "channel_id": str(
                origin_binding.get("channel_id")
                or payload.get("channel_id")
                or ""
            ),
            "thread_id": str(
                origin_binding.get("thread_id")
                or payload.get("thread_id")
                or ""
            ),
            "scope": _string_list(payload.get("scope")),
            "target_ref": str(payload.get("target_ref") or ""),
            "expected_output": str(payload.get("expected_output") or ""),
            "risk": str(payload.get("risk") or ""),
            "synthesis_event_id": str(payload.get("synthesis_event_id") or ""),
            "open_questions": _string_list(payload.get("open_questions")),
            "request_id": request_id,
            "request_revision": request_revision,
            "origin_binding": dict(origin_binding),
        }
        prompt_kind = infer_workflow_prompt_kind(pattern_id, payload)
        workflow_prompt_ref = ""
        prompt_artifact: dict[str, Any] = {}
        if prompt_kind:
            prompt_artifact = write_workflow_prompt_package(
                self.state_dir,
                workflow_run_id=workflow_run_id,
                task_id=task_id,
                pattern_id=pattern_id,
                prompt_kind=prompt_kind,
                source_refs=source_refs,
                artifact_refs=artifact_refs,
                request_payload=event.payload,
            )
            workflow_prompt_ref = str(prompt_artifact.get("ref") or "")
            source_refs["workflow_prompt_ref"] = workflow_prompt_ref
            source_refs["prompt_kind"] = prompt_kind
            artifact_refs.append(prompt_artifact)
            event.payload["prompt_kind"] = prompt_kind
            event.payload["workflow_prompt_ref"] = workflow_prompt_ref
            event.payload["source_refs"] = source_refs
            event.payload["artifact_refs"] = artifact_refs
        research_identity: dict[str, Any] = {}
        if str(event.payload.get("request_kind") or "") == "research":
            template = resolve_research_template(
                str(event.payload.get("research_template_id") or "")
            )
            if task is None or template is None or not prompt_artifact:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task_id or None,
                    reason="research generation requires Task, template, and prompt bindings",
                    status_code=409,
                    status="preflight_blocked",
                )
            try:
                research_identity = materialize_research_generation(
                    self.state_dir,
                    config=self.config,
                    project_root=self.project_root or self.state_dir.parent,
                    task=task,
                    template=template,
                    workflow_run_id=workflow_run_id,
                    request_payload=event.payload,
                    prompt_ref=prompt_artifact,
                    source_event_id=event.id,
                )
            except ResearchGenerationError as exc:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=task_id or None,
                    reason=str(exc),
                    status_code=409,
                    status="preflight_blocked",
                )
            event.payload.update(research_identity)
            source_refs.update({
                "workflow_generation": str(
                    research_identity["workflow_generation"]
                ),
                "research_generation_contract_ref": str(
                    research_identity["run_contract_ref"]
                ),
            })
            generation_ref = dict(
                research_identity["research_generation_contract_ref"]
            )
            generation_ref.update({
                "kind": "research_generation_contract",
                "source_field": "generated_research_contract",
            })
            artifact_refs.append(generation_ref)
            event.payload["source_refs"] = source_refs
            event.payload["artifact_refs"] = artifact_refs
        write_workflow_input_manifest(
            self.state_dir,
            workflow_run_id=workflow_run_id,
            workflow_invoke_event_id=event.id,
            task_id=task_id,
            pattern_id=pattern_id,
            source_refs=source_refs,
            artifact_refs=artifact_refs,
            request_payload=event.payload,
        )
        if research_identity:
            supersede_active_research_generations(
                self.writer,
                task_id=task_id,
                new_generation=str(research_identity["workflow_generation"]),
                new_run_id=workflow_run_id,
                source_event_id=requested.id,
            )
        event = self.writer.append(event)
        channel_id = str(
            origin_binding.get("channel_id")
            or payload.get("channel_id")
            or ""
        )
        if channel_id:
            self.writer.emit(
                "channel.state_update.posted",
                actor=self.actor,
                task_id=task_id,
                causation_id=event.id,
                correlation_id=channel_id,
                payload={
                    "channel_id": channel_id,
                    "thread_id": str(
                        origin_binding.get("thread_id")
                        or payload.get("thread_id")
                        or "main"
                    ),
                    "status": "workflow_requested",
                    "summary": f"workflow {pattern_id} requested for {task_id}",
                    "task_id": task_id,
                    "refs": {
                        "workflow_invoke_event_id": event.id,
                        "workflow_input_manifest_ref": manifest_ref,
                        "workflow_prompt_ref": workflow_prompt_ref,
                        "prompt_kind": prompt_kind,
                        "workflow_generation": str(
                            research_identity.get("workflow_generation") or ""
                        ),
                    },
                    "source": self.surface,
                },
            )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="requested",
            task_id=task_id,
            extra={"pattern_id": pattern_id, "workflow_invoke_event_id": event.id},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "requested",
            "action": action,
            "requested_action": requested_action,
            "task_id": task_id,
            "pattern_id": pattern_id,
            "event_id": event.id,
            "workflow_run_id": workflow_run_id,
            "workflow_input_manifest_ref": manifest_ref,
            "workflow_prompt_ref": workflow_prompt_ref,
            "prompt_kind": prompt_kind,
            "workflow_generation": str(
                research_identity.get("workflow_generation") or ""
            ),
            "effective_config_digest": str(
                research_identity.get("effective_config_digest") or ""
            ),
            "run_contract_digest": str(
                research_identity.get("run_contract_digest") or ""
            ),
        }
    def _operator_intent_create(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        validation_error = validate_operator_intent_payload(payload)
        if validation_error:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason=validation_error,
                status_code=422,
                status="invalid_payload",
            )
        intent = infer_operator_intent(
            str(payload.get("message") or payload.get("objective") or payload.get("text") or ""),
            payload=payload,
            project_id=_required_text(payload, "project_id"),
            source=self.source,
        )
        if _required_text(payload, "intent_type"):
            intent["intent_type"] = _required_text(payload, "intent_type")
        if _required_text(payload, "risk"):
            intent["risk"] = _required_text(payload, "risk")
        event = self.writer.emit(
            "operator.intent.created",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                **intent,
                "request": redact_obj(payload),
                "surface": self.surface,
            },
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="proposed",
            task_id=_task_id_from_payload(payload),
            extra={
                "intent_id": str(intent.get("intent_id") or ""),
                "intent_type": str(intent.get("intent_type") or ""),
                "event_type": event.type,
                "event_id": event.id,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "proposed",
            "action": action,
            "requested_action": requested_action,
            "intent_id": str(intent.get("intent_id") or ""),
            "intent_type": str(intent.get("intent_type") or ""),
            "risk": str(intent.get("risk") or ""),
            "requires_owner_approval": bool(intent.get("requires_owner_approval")),
            "proposed_actions": list(intent.get("proposed_actions") or []),
            "event_id": event.id,
        }
    def _operator_intent_lifecycle(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        intent_id = _required_text(payload, "intent_id")
        if not intent_id:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="intent_id is required",
                status_code=422,
                status="invalid_payload",
            )
        approved = action == "operator-intent-approve"
        event_type = "operator.intent.approved" if approved else "operator.intent.rejected"
        event = self.writer.emit(
            event_type,
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload=redact_obj({
                "schema_version": "operator.intent.lifecycle.v0",
                "intent_id": intent_id,
                "reason": _required_text(payload, "reason"),
                "approved_by": _required_text(payload, "approved_by") or _required_text(payload, "owner"),
                "source": self.source,
                "surface": self.surface,
                "request": payload,
            }),
        )
        executed: dict | None = None
        if approved and bool(payload.get("execute_proposals")):
            # doc 122 §9 last mile: "approve" alone only records a decision and
            # left the owner hand-running create-task/workflow-invoke. With the
            # explicit flag the same call executes the proposal chain inline,
            # so one click takes a clarified requirement into the workflow.
            executed = self._execute_intent_proposals(
                requested=requested,
                intent_id=intent_id,
                approved_event=event,
            )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="approved" if approved else "rejected",
            task_id=_task_id_from_payload(payload),
            extra={"intent_id": intent_id, "event_type": event_type, "event_id": event.id},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "approved" if approved else "rejected",
            "action": action,
            "requested_action": requested_action,
            "intent_id": intent_id,
            "event_id": event.id,
            **({"executed": executed} if executed is not None else {}),
        }

    def _execute_intent_proposals(
        self,
        *,
        requested: ZfEvent,
        intent_id: str,
        approved_event: ZfEvent,
    ) -> dict:
        from zf.core.events.log import EventLog

        events = EventLog(self.state_dir / "events.jsonl").read_all()
        proposal = None
        for event in events:
            if event.type != "operator.action.proposed":
                continue
            if str((event.payload or {}).get("intent_id") or "") == intent_id:
                proposal = event
        if proposal is None:
            return {"ok": False, "reason": "proposal_not_found", "intent_id": intent_id}
        proposal_id = str((proposal.payload or {}).get("proposal_id") or "")
        for event in events:
            if event.type == "operator.action.executed" and (
                str((event.payload or {}).get("proposal_id") or "") == proposal_id
            ):
                return {"ok": True, "reason": "already_executed", "proposal_id": proposal_id}
        results: list[dict] = []
        created_task_id = ""
        created_intake_ref = ""
        for item in (proposal.payload or {}).get("proposals") or []:
            if not isinstance(item, dict):
                continue
            step_action = str(item.get("action") or "")
            step_payload = dict(item.get("payload") or {})
            if step_action == "workflow-invoke":
                placeholder = str(step_payload.get("task_id") or "")
                if created_task_id and placeholder in {"", "<created-task-id>"}:
                    step_payload["task_id"] = created_task_id
            if step_action == "workflow-submit":
                placeholder = str(step_payload.get("intake_ref") or "")
                if created_intake_ref and placeholder in {"", "<created-intake-ref>"}:
                    step_payload["intake_ref"] = created_intake_ref
            result = self._execute_action(
                action=step_action,
                requested_action=step_action,
                payload=step_payload,
                requested=approved_event,
            )
            if step_action == "create-task" and result.get("ok"):
                created_task_id = str(result.get("task_id") or result.get("id") or "")
            if step_action == "workflow-request" and result.get("ok"):
                created_intake_ref = str(result.get("intake_ref") or "")
            results.append({
                "action": step_action,
                "ok": bool(result.get("ok")),
                "status": str(result.get("status") or ""),
                "task_id": str(result.get("task_id") or ""),
                "request_id": str(result.get("request_id") or ""),
            })
            if not result.get("ok"):
                break
        self.writer.emit(
            "operator.action.executed",
            actor=self.actor,
            causation_id=approved_event.id,
            correlation_id=requested.correlation_id,
            payload=redact_obj({
                "schema_version": "operator.action.executed.v0",
                "proposal_id": proposal_id,
                "intent_id": intent_id,
                "results": results,
                "created_task_id": created_task_id,
                "created_intake_ref": created_intake_ref,
                "source": self.source,
                "surface": self.surface,
            }),
        )
        return {
            "ok": all(r["ok"] for r in results) if results else False,
            "proposal_id": proposal_id,
            "created_task_id": created_task_id,
            "created_intake_ref": created_intake_ref,
            "results": results,
        }
    def _replan_owner_decision(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        proposal_ref = _required_text(payload, "proposal_ref") or _required_text(payload, "artifact_ref")
        eval_ref = _required_text(payload, "eval_ref") or _required_text(payload, "eval_id")
        if not proposal_ref:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="proposal_ref is required",
                status_code=422,
                status="invalid_payload",
            )
        if not eval_ref:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="eval_ref is required",
                status_code=422,
                status="invalid_payload",
            )
        decision = {
            "replan-approve": "approved",
            "replan-defer": "deferred",
            "replan-reject": "rejected",
        }[action]
        decision_payload = redact_obj({
            "schema_version": "replan-owner-decision.v0",
            "decision": decision,
            "proposal_ref": proposal_ref,
            "eval_ref": eval_ref,
            "candidate_task_map_ref": _required_text(payload, "candidate_task_map_ref"),
            "reason": _required_text(payload, "reason"),
            "decided_by": _required_text(payload, "decided_by") or _required_text(payload, "owner"),
            "approval_ref": _required_text(payload, "approval_ref"),
            "checkpoint_id": _required_text(payload, "checkpoint_id"),
            "workflow_run_id": _required_text(payload, "workflow_run_id"),
            "fanout_id": _required_text(payload, "fanout_id"),
            "source_event_id": _required_text(payload, "source_event_id"),
            "source": self.source,
            "surface": self.surface,
            "apply_policy": "decision_only",
            "direct_adoption": False,
            "request": payload,
        })
        event = self.writer.emit(
            f"replan.owner_decision.{decision}",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload=decision_payload,
        )
        approval_ref = str(decision_payload.get("approval_ref") or "")
        if approval_ref:
            self.writer.emit(
                "approval.resolved",
                actor=self.actor,
                task_id=_task_id_from_payload(payload),
                causation_id=event.id,
                correlation_id=requested.correlation_id,
                payload=redact_obj({
                    "schema_version": "approval.resolved.v1",
                    "approval_ref": approval_ref,
                    "checkpoint_id": decision_payload.get("checkpoint_id", ""),
                    "status": decision,
                    "resolution": decision,
                    "decision_event_id": event.id,
                    "source": self.source,
                    "surface": self.surface,
                }),
            )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status=decision,
            task_id=_task_id_from_payload(payload),
            extra={
                "decision": decision,
                "proposal_ref": proposal_ref,
                "eval_ref": eval_ref,
                "event_id": event.id,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": decision,
            "action": action,
            "requested_action": requested_action,
            "proposal_ref": proposal_ref,
            "eval_ref": eval_ref,
            "event_id": event.id,
        }
    def _idea_to_product(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        objective = (
            _required_text(payload, "objective")
            or _required_text(payload, "message")
            or _required_text(payload, "title")
        )
        if not objective:
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="objective or message is required",
                status_code=422,
                status="invalid_payload",
            )
        intent = infer_operator_intent(
            objective,
            payload={**payload, "objective": objective},
            project_id=_required_text(payload, "project_id"),
            source=self.source,
        )
        intent_event = self.writer.emit(
            "operator.intent.created",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                **intent,
                "intent_type": "idea_to_product",
                "source": self.source,
                "surface": self.surface,
            },
        )
        proposal_id = _proposal_id("idea-to-product", payload, requested.id)
        initialized_project = bool(
            self.project_root is not None
            and self.config is not None
            and (self.project_root / "zf.yaml").exists()
        )
        if initialized_project:
            proposals = [
                {
                    "action": "workflow-request",
                    "payload": {
                        "objective": objective,
                        "kind": str(payload.get("kind") or "auto"),
                        "source_ref": str(payload.get("artifact_ref") or ""),
                        "source_root": str(payload.get("source_root") or ""),
                        "target_root": str(payload.get("target_root") or payload.get("target") or ""),
                        "backend": str(payload.get("backend") or ""),
                        "lanes": int(payload.get("lanes") or 0),
                        "acceptance": _string_list(payload.get("acceptance")),
                        "constraints": _string_list(payload.get("constraints")),
                        "open_questions": _string_list(payload.get("open_questions")),
                        "channel_id": str(payload.get("channel_id") or ""),
                        "thread_id": str(payload.get("thread_id") or ""),
                        "pattern_id": str(payload.get("pattern_id") or ""),
                        "allow_missing_env": bool(payload.get("allow_missing_env")),
                    },
                },
                {
                    "action": "workflow-submit",
                    "payload": {
                        "intake_ref": "<created-intake-ref>",
                        "kind": str(payload.get("kind") or ""),
                        "task_id": str(payload.get("task_id") or ""),
                        "pattern_id": str(payload.get("pattern_id") or ""),
                        "reason": str(payload.get("reason") or "approved clarified requirement"),
                        "allow_missing_env": bool(payload.get("allow_missing_env")),
                    },
                },
            ]
        else:
            proposals = [
                {
                    "action": "create-task",
                    "payload": {
                        "title": objective[:160],
                        "priority": str(payload.get("priority") or "P1"),
                        "contract": payload.get("contract")
                        if isinstance(payload.get("contract"), dict) else {},
                    },
                },
                {
                    "action": "workflow-invoke",
                    "payload": {
                        "task_id": str(payload.get("task_id") or "<created-task-id>"),
                        "pattern_id": str(payload.get("pattern_id") or "dag"),
                        "expected_output": str(payload.get("expected_output") or "plan-to-ship delivery"),
                        **({"artifact_refs": [str(payload.get("artifact_ref"))]}
                           if payload.get("artifact_ref") else {}),
                    },
                },
            ]
        proposal_event = self.writer.emit(
            "operator.action.proposed",
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=intent_event.id,
            correlation_id=requested.correlation_id,
            payload=redact_obj({
                "schema_version": "operator.action.proposal.v0",
                "proposal_id": proposal_id,
                "intent_id": str(intent.get("intent_id") or ""),
                "action": action,
                "status": "proposed",
                "objective": objective,
                "proposals": proposals,
                "requires_owner_confirmation": True,
                "mutates_truth_directly": False,
            }),
        )
        self._completed(
            requested=requested,
            event=proposal_event,
            action=action,
            requested_action=requested_action,
            status="proposed",
            task_id=_task_id_from_payload(payload),
            extra={
                "proposal_id": proposal_id,
                "intent_id": str(intent.get("intent_id") or ""),
                "proposal_event_id": proposal_event.id,
            },
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "proposed",
            "action": action,
            "requested_action": requested_action,
            "reason": (
                "idea-to-product intent recorded; workflow request and submit require explicit approval"
                if initialized_project
                else "idea-to-product intent recorded; task creation and workflow invoke require explicit approval"
            ),
            "intent_id": str(intent.get("intent_id") or ""),
            "proposal_id": proposal_id,
            "event_id": proposal_event.id,
            "proposals": proposals,
        }
    def _provider_dev_chat(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        event_type = {
            "provider-dev-chat-start": "provider.dev_chat.start.requested",
            "provider-dev-chat-send": "provider.dev_chat.message.requested",
            "provider-dev-chat-stop": "provider.dev_chat.stop.requested",
        }[action]
        proposal_id = _proposal_id(action, payload, requested.id)
        event = self.writer.emit(
            event_type,
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload=redact_obj({
                "schema_version": "provider.dev_chat.request.v0",
                "proposal_id": proposal_id,
                "provider": _required_text(payload, "provider") or _required_text(payload, "backend"),
                "role": _required_text(payload, "role"),
                "thread_id": _required_text(payload, "thread_id"),
                "message": _required_text(payload, "message") or _required_text(payload, "objective"),
                "permission_profile": _required_text(payload, "permission_profile") or "proposal_only",
                "requires_owner_approval": True,
                "source": self.source,
                "surface": self.surface,
                "request": payload,
            }),
        )
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status="requested",
            task_id=_task_id_from_payload(payload),
            extra={"proposal_id": proposal_id, "event_type": event_type, "event_id": event.id},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": "requested",
            "action": action,
            "requested_action": requested_action,
            "reason": "provider dev chat request recorded; executor wiring remains provider-gated",
            "proposal_id": proposal_id,
            "event_type": event_type,
            "event_id": event.id,
        }
    def _workflow_config_action(
        self,
        *,
        requested: ZfEvent,
        action: str,
        requested_action: str,
        payload: dict,
    ) -> dict:
        if action == "workflow-config-apply" and not _approval_ref(payload):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="owner approval is required before applying workflow config changes",
                status_code=403,
                status="approval_required",
            )
        if action == "workflow-config-apply" and not (
            _required_text(payload, "validation_result_ref")
            or isinstance(payload.get("validation_result_ref"), dict)
        ):
            return self._failed(
                requested=requested,
                action=action,
                requested_action=requested_action,
                task_id=_task_id_from_payload(payload),
                reason="validation_result_ref is required before applying workflow config changes",
                status_code=422,
                status="invalid_payload",
            )
        event_type = {
            "workflow-config-propose": "workflow.config.change.proposed",
            "workflow-config-validate": "workflow.config.validation.requested",
            "workflow-config-apply": "workflow.config.change.apply.requested",
        }[action]
        proposal_id = _required_text(payload, "proposal_id") or _proposal_id(action, payload, requested.id)
        event = self.writer.emit(
            event_type,
            actor=self.actor,
            task_id=_task_id_from_payload(payload),
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload=redact_obj({
                "schema_version": "workflow.config.action.v0",
                "proposal_id": proposal_id,
                "objective": _required_text(payload, "objective") or _required_text(payload, "message"),
                "patch_ref": _required_text(payload, "patch_ref"),
                "validation_result_ref": (
                    payload.get("validation_result_ref")
                    if isinstance(payload.get("validation_result_ref"), dict)
                    else _required_text(payload, "validation_result_ref")
                ),
                "approval_ref": _approval_ref(payload),
                "proposal_ref": payload.get("proposal_ref")
                if isinstance(payload.get("proposal_ref"), dict) else {},
                "proposal_digest": _required_text(payload, "proposal_digest"),
                "config_ref": _required_text(payload, "config_ref"),
                "idempotency_key": _required_text(payload, "idempotency_key"),
                "source": self.source,
                "surface": self.surface,
                "request": payload,
            }),
        )
        if action == "workflow-config-apply":
            if self.project_root is None:
                return self._failed(
                    requested=requested,
                    action=action,
                    requested_action=requested_action,
                    task_id=_task_id_from_payload(payload),
                    reason="initialized project context is required",
                    status_code=409,
                    status="project_initialization_required",
                )
            from zf.runtime.workflow_config_apply import (
                WorkflowConfigApplyError,
                WorkflowConfigApplyService,
            )

            try:
                outcome = WorkflowConfigApplyService(
                    state_dir=self.state_dir,
                    project_root=self.project_root,
                ).apply(
                    payload,
                    source_event_id=event.id,
                    actor=self.actor,
                )
            except WorkflowConfigApplyError as exc:
                terminal = self.writer.emit(
                    "workflow.config.change.rejected",
                    actor=self.actor,
                    task_id=_task_id_from_payload(payload),
                    causation_id=event.id,
                    correlation_id=requested.correlation_id,
                    payload={
                        "proposal_id": proposal_id,
                        "proposal_digest": _required_text(
                            payload, "proposal_digest"
                        ),
                        "reason": str(exc),
                        "code": exc.code,
                        "source_event_id": event.id,
                    },
                )
                self._completed(
                    requested=requested,
                    event=terminal,
                    action=action,
                    requested_action=requested_action,
                    status="rejected",
                    task_id=_task_id_from_payload(payload),
                    extra={
                        "proposal_id": proposal_id,
                        "event_type": terminal.type,
                        "event_id": terminal.id,
                    },
                )
                return {
                    "_status_code": 409,
                    "ok": False,
                    "status": "rejected",
                    "action": action,
                    "requested_action": requested_action,
                    "proposal_id": proposal_id,
                    "event_type": terminal.type,
                    "event_id": terminal.id,
                    "reason": str(exc),
                    "code": exc.code,
                }
            except Exception as exc:
                terminal = self.writer.emit(
                    "workflow.config.change.failed",
                    actor=self.actor,
                    task_id=_task_id_from_payload(payload),
                    causation_id=event.id,
                    correlation_id=requested.correlation_id,
                    payload={
                        "proposal_id": proposal_id,
                        "proposal_digest": _required_text(
                            payload, "proposal_digest"
                        ),
                        "reason": str(exc)[:512],
                        "code": "config_apply_internal_failure",
                        "source_event_id": event.id,
                    },
                )
                self._completed(
                    requested=requested,
                    event=terminal,
                    action=action,
                    requested_action=requested_action,
                    status="failed",
                    task_id=_task_id_from_payload(payload),
                    extra={
                        "proposal_id": proposal_id,
                        "event_type": terminal.type,
                        "event_id": terminal.id,
                    },
                )
                return {
                    "_status_code": 500,
                    "ok": False,
                    "status": "failed",
                    "action": action,
                    "requested_action": requested_action,
                    "proposal_id": proposal_id,
                    "event_type": terminal.type,
                    "event_id": terminal.id,
                    "reason": str(exc),
                    "code": "config_apply_internal_failure",
                }
            terminal = self.writer.emit(
                "workflow.config.change.applied",
                actor=self.actor,
                task_id=_task_id_from_payload(payload),
                causation_id=event.id,
                correlation_id=requested.correlation_id,
                payload={
                    "proposal_id": outcome.proposal_id,
                    "proposal_digest": outcome.proposal_digest,
                    "base_config_digest": outcome.base_config_digest,
                    "target_config_digest": outcome.target_config_digest,
                    "config_ref": outcome.config_ref,
                    "receipt_ref": outcome.receipt_ref,
                    "replayed": outcome.replayed,
                    "source_event_id": event.id,
                },
            )
            self._completed(
                requested=requested,
                event=terminal,
                action=action,
                requested_action=requested_action,
                status="completed",
                task_id=_task_id_from_payload(payload),
                extra={
                    "proposal_id": proposal_id,
                    "event_type": terminal.type,
                    "event_id": terminal.id,
                },
            )
            return {
                "_status_code": 200,
                "ok": True,
                "status": "completed",
                "action": action,
                "requested_action": requested_action,
                "proposal_id": proposal_id,
                "event_type": terminal.type,
                "event_id": terminal.id,
                "receipt_ref": outcome.receipt_ref,
                "replayed": outcome.replayed,
            }
        status = "requested" if action != "workflow-config-propose" else "proposed"
        self._completed(
            requested=requested,
            event=event,
            action=action,
            requested_action=requested_action,
            status=status,
            task_id=_task_id_from_payload(payload),
            extra={"proposal_id": proposal_id, "event_type": event_type, "event_id": event.id},
        )
        return {
            "_status_code": 202,
            "ok": True,
            "status": status,
            "action": action,
            "requested_action": requested_action,
            "proposal_id": proposal_id,
            "event_type": event_type,
            "event_id": event.id,
        }
