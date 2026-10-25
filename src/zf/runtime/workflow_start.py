"""Surface-neutral workflow route preview and exact proposal service."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter, ZfEvent
from zf.core.security.redaction import redact_obj
from zf.core.task.store import TaskStore
from zf.core.task.contract_validation import validate_task_contract
from zf.runtime.kanban_proposals import (
    PROPOSAL_EVENT,
    PROPOSAL_EVENT_TYPES,
    canonical_proposal_action,
    proposal_payload_digest,
)
from zf.runtime.task_workflow_plans import (
    task_workflow_binding_digest,
    workflow_route_task_eligibility_error,
)
from zf.runtime.workflow_anchor import workflow_task_request_binding
from zf.runtime.task_contract_authority import task_execution_binding
from zf.runtime.workflow_origin import workflow_origin_digest
from zf.runtime.workflow_request_acceptance import bind_task_workflow_inputs
from zf.runtime.workflow_requests import (
    WorkflowRequestError,
    hydrate_workflow_requirement,
    require_current_workflow_request,
)
from zf.runtime.workflow_inputs import hydrate_request_workflow_input_manifest
from zf.runtime.workflow_route_catalog import (
    resolve_workflow_route,
    workflow_route_catalog,
)
from zf.runtime.workflow_task_request_rotation import (
    terminal_task_request_rotation_context,
)


WORKFLOW_START_SCHEMA_VERSION = "workflow-start.v1"
WORKFLOW_START_ACTION = "workflow-start"
LEGACY_WORKFLOW_START_ACTION = "task-workflow-start"
WORKFLOW_START_ACTIONS = frozenset({
    WORKFLOW_START_ACTION,
    LEGACY_WORKFLOW_START_ACTION,
})


def is_workflow_start_action(action: str) -> bool:
    return str(action or "").strip() in WORKFLOW_START_ACTIONS


class WorkflowStartService:
    """Build current route bindings without owning workflow execution."""

    def __init__(
        self,
        state_dir: Path,
        config: ZfConfig | None,
        *,
        project_root: Path | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.config = config
        self.project_root = (
            Path(project_root)
            if project_root is not None
            else self.state_dir.parent
        )

    def routes(self, *, task_id: str) -> dict[str, Any]:
        task = TaskStore(self.state_dir / "kanban.json").get(task_id)
        if task is None:
            return _failure(
                "preflight_blocked",
                f"workflow task {task_id!r} does not exist",
                status_code=404,
                task_id=task_id,
            )
        catalog = workflow_route_catalog(self.config)
        if not str(catalog.get("config_digest") or ""):
            return _failure(
                "workflow_route_unavailable",
                "workflow route catalog is unavailable",
                status_code=409,
                task_id=task_id,
            )
        routes = [dict(route) for route in catalog.get("routes") or []]
        for route in routes:
            reason = workflow_route_task_eligibility_error(route, task)
            if reason:
                route["available"] = False
                route["task_eligibility"] = "blocked"
                route["task_eligibility_reason"] = reason
        return {
            "ok": True,
            "status": "ready",
            "schema_version": WORKFLOW_START_SCHEMA_VERSION,
            "action": WORKFLOW_START_ACTION,
            "task_id": task.id,
            "task_title": task.title,
            "task_contract_digest": task_workflow_binding_digest(task),
            **catalog,
            "routes": routes,
        }

    def preview(
        self,
        payload: dict[str, Any],
        *,
        require_bindings: bool,
        origin: str = "",
    ) -> dict[str, Any]:
        task_id = str(payload.get("task_id") or "").strip()
        route_id = str(payload.get("route_id") or "").strip()
        if not task_id:
            return _failure(
                "invalid_payload",
                "task_id is required",
                status_code=422,
            )
        if not route_id:
            return _failure(
                "invalid_payload",
                "route_id is required",
                status_code=422,
                task_id=task_id,
            )
        parameters = payload.get("parameters")
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, dict):
            return _failure(
                "invalid_payload",
                "parameters must be a mapping",
                status_code=422,
                task_id=task_id,
            )
        requested_parameters = dict(parameters)
        if ("authorize_channel_execution" in parameters
                and not isinstance(parameters["authorize_channel_execution"], bool)):
            return _failure(
                "invalid_payload", "authorize_channel_execution must be a boolean",
                status_code=422, task_id=task_id,
            )

        task = TaskStore(self.state_dir / "kanban.json").get(task_id)
        if task is None:
            return _failure(
                "preflight_blocked",
                f"workflow task {task_id!r} does not exist",
                status_code=404,
                task_id=task_id,
            )
        task_digest = task_workflow_binding_digest(task)
        task_request = workflow_task_request_binding(task)
        expected_task_digest = str(
            payload.get("task_contract_digest") or ""
        ).strip()
        execution_binding = task_execution_binding(task)
        active_replay_origin = str(execution_binding.origin_task_digest or "")
        if (
            (require_bindings and not expected_task_digest)
            or (
                expected_task_digest
                and expected_task_digest != task_digest
                and not (
                    execution_binding.owner == "workflow"
                    and active_replay_origin == expected_task_digest
                )
            )
        ):
            return _failure(
                "workflow_task_stale",
                "workflow Task binding is stale or missing",
                status_code=409,
                task_id=task_id,
            )

        payload_request_id = str(
            payload.get("request_id")
            or parameters.get("request_id")
            or ""
        ).strip()
        try:
            payload_request_revision = int(
                payload.get("request_revision")
                or parameters.get("request_revision")
                or 0
            )
        except (TypeError, ValueError):
            payload_request_revision = 0
        request_projection: dict[str, Any] = {}
        terminal_rotation: dict[str, Any] = {}
        if task_request:
            terminal_rotation = terminal_task_request_rotation_context(
                self.state_dir,
                task_request,
            )
            if terminal_rotation:
                if payload_request_id == task_request["request_id"]:
                    return _failure(
                        "workflow_request_run_terminal",
                        "terminal Workflow Request cannot be replayed; start a distinct Request",
                        status_code=409,
                        task_id=task_id,
                    )
                if payload_request_revision:
                    return _failure(
                        "workflow_task_stale",
                        "fresh Workflow Request must not reuse a prior revision",
                        status_code=409,
                        task_id=task_id,
                    )
                parameters = dict(parameters)
                parameters.pop("request_id", None)
                parameters.pop("request_revision", None)
            else:
                if (
                    payload_request_id
                    and payload_request_id != task_request["request_id"]
                ):
                    return _failure(
                        "workflow_task_stale",
                        "workflow Request does not match the Task binding",
                        status_code=409,
                        task_id=task_id,
                    )
                if (
                    payload_request_revision
                    and payload_request_revision
                    != int(task_request["request_revision"])
                ):
                    return _failure(
                        "workflow_task_stale",
                        "workflow Request revision does not match the Task binding",
                        status_code=409,
                        task_id=task_id,
                    )
                try:
                    request_projection = require_current_workflow_request(
                        self.state_dir,
                        task_request["request_id"],
                        int(task_request["request_revision"]),
                    )
                except WorkflowRequestError as exc:
                    return _failure(
                        "workflow_task_stale",
                        str(exc),
                        status_code=409,
                        task_id=task_id,
                    )
            if (
                task_request.get("origin_binding_digest")
                and task_request["origin_binding_digest"]
                != workflow_origin_digest(
                    request_projection.get("origin_binding")
                    or terminal_rotation.get("origin_binding")
                    or {}
                )
            ):
                return _failure(
                    "workflow_task_stale",
                    "workflow origin binding does not match the Task binding",
                    status_code=409,
                    task_id=task_id,
                )
            origin_binding = (
                request_projection.get("origin_binding")
                or terminal_rotation.get("origin_binding")
                or {}
            )
            if not _matches_canonical_origin(payload, origin_binding):
                return _failure(
                    "origin_binding_mismatch",
                    (
                        "workflow return target does not match the "
                        "Workflow Request origin"
                    ),
                    status_code=409,
                    task_id=task_id,
                )
            # An approved preview may predate Request creation. Keep its input
            # digest intact on retry; the canonical Request is carried in the
            # normalized top-level binding below and was checked above.
            if not terminal_rotation and not isinstance(payload.get("task_input_binding"), dict):
                parameters = dict(parameters)
                parameters["request_id"] = task_request["request_id"]
                parameters["request_revision"] = int(
                    task_request["request_revision"]
                )
        elif payload_request_id:
            from zf.runtime.workflow_pending_request import pending_request_for_task

            try:
                request_projection = pending_request_for_task(
                    self.state_dir, config=self.config, task=task,
                    request_id=payload_request_id,
                    revision=payload_request_revision,
                )
            except WorkflowRequestError as exc:
                return _failure(
                    "workflow_task_stale", str(exc),
                    status_code=409, task_id=task_id,
                )
            if not _matches_canonical_origin(
                payload, request_projection["origin_binding"],
            ):
                return _failure(
                    "origin_binding_mismatch",
                    "pending Request origin does not match the approved return target",
                    status_code=409, task_id=task_id,
                )
            parameters = dict(parameters)
            parameters.update(
                request_id=payload_request_id,
                request_revision=payload_request_revision,
            )

        catalog = workflow_route_catalog(self.config)
        config_digest = str(catalog.get("config_digest") or "")
        expected_config_digest = str(
            payload.get("config_digest") or ""
        ).strip()
        if (
            (require_bindings and not expected_config_digest)
            or (
                expected_config_digest
                and expected_config_digest != config_digest
            )
        ):
            return _failure(
                "workflow_route_unavailable",
                f"workflow route {route_id!r} is stale or unavailable",
                status_code=409,
                task_id=task_id,
            )
        route = resolve_workflow_route(
            self.config,
            route_id,
            expected_config_digest=config_digest,
        )
        if route is None:
            return _failure(
                "workflow_route_unavailable",
                f"workflow route {route_id!r} is stale or unavailable",
                status_code=409,
                task_id=task_id,
            )
        if (
            payload_request_id and not task_request and request_projection
            and str(route.get("kind") or "") != request_projection.get("kind")
        ):
            return _failure(
                "workflow_task_stale", "pending Request workflow kind cannot change",
                status_code=409, task_id=task_id,
            )
        eligibility_error = workflow_route_task_eligibility_error(route, task)
        if eligibility_error:
            return _failure(
                "workflow_route_ineligible",
                eligibility_error,
                status_code=422,
                task_id=task_id,
            )
        is_delivery_route = str(route.get("family") or "") == "delivery"
        is_channel_prd_task = (
            str(getattr(task.contract, "source_mode", "") or "")
            == "channel_prd"
        )
        if is_delivery_route:
            if self.config is None:
                return _failure(
                    "workflow_route_unavailable",
                    "delivery workflow requires an active project config",
                    status_code=409,
                    task_id=task_id,
                )
            contract_errors = validate_task_contract(
                task,
                config=self.config,
                project_root=self.project_root,
            )
            if contract_errors:
                return _failure(
                    "task_contract_invalid",
                    "delivery workflow Task contract is incomplete: "
                    + "; ".join(contract_errors),
                    status_code=422,
                    task_id=task_id,
                )
        if is_delivery_route or is_channel_prd_task:
            parameters = dict(parameters)
            for key in ("artifact_refs", "source_refs"):
                if key not in parameters and payload.get(key) not in (
                    None, "", [], {},
                ):
                    parameters[key] = payload[key]
            prior_binding = (
                payload.get("task_input_binding")
                if isinstance(payload.get("task_input_binding"), dict)
                else None
            )
            try:
                (
                    parameters,
                    task_input_binding,
                    _task_input_contract,
                    requested_parameters,
                ) = bind_task_workflow_inputs(
                    parameters,
                    task,
                    task_contract_digest=task_digest,
                    prior_binding=prior_binding,
                )
            except ValueError as exc:
                return _failure(
                    "workflow_task_stale",
                    str(exc),
                    status_code=409,
                    task_id=task_id,
                )
        else:
            task_input_binding = {}
            task_input_contract = {}

        objective = str(
            payload.get("objective")
            or payload.get("message")
            or task.title
        ).strip()
        if not objective:
            return _failure(
                "invalid_payload",
                "objective or message is required",
                status_code=422,
                task_id=task_id,
            )
        active_request_replay = False
        if str(request_projection.get("status") or "") in {
            "submitted",
            "running",
        }:
            try:
                requirement = hydrate_workflow_requirement(
                    self.state_dir,
                    request_projection,
                )
                manifest = hydrate_request_workflow_input_manifest(
                    self.state_dir,
                    request_projection,
                )
            except WorkflowRequestError as exc:
                return _failure(
                    "workflow_task_stale",
                    str(exc),
                    status_code=409,
                    task_id=task_id,
                )
            replay_error = _active_request_replay_error(
                objective=objective,
                parameters=requested_parameters,
                requirement=requirement,
                manifest=manifest,
                route=route,
            )
            if replay_error:
                return _failure(
                    "workflow_request_active",
                    replay_error,
                    status_code=409,
                    task_id=task_id,
                )
            active_request_replay = True
        normalized = {
            "schema_version": WORKFLOW_START_SCHEMA_VERSION,
            "task_id": task_id,
            "route_id": route_id,
            "objective": objective,
            "parameters": {
                str(key): value
                for key, value in parameters.items()
                if value not in (None, "", [], {})
            },
            "task_contract_digest": task_digest,
            "config_digest": config_digest,
            "origin": str(payload.get("origin") or origin or ""),
        }
        if task_input_binding:
            normalized["task_input_binding"] = task_input_binding
        if active_request_replay:
            normalized["active_request_replay"] = True
        if request_projection:
            normalized.update({
                "request_id": str(request_projection["request_id"]),
                "request_revision": int(request_projection["revision"]),
                "origin_binding": dict(
                    request_projection["origin_binding"]
                ),
            })
            origin_binding = request_projection["origin_binding"]
            if origin_binding.get("surface") == "channel":
                normalized["channel_id"] = str(
                    origin_binding.get("channel_id") or ""
                )
                normalized["thread_id"] = str(
                    origin_binding.get("thread_id") or "main"
                )
            elif origin_binding.get("surface") == "kanban_agent":
                normalized["project_id"] = str(
                    origin_binding.get("project_id") or ""
                )
                normalized["conversation_id"] = str(
                    origin_binding.get("conversation_id") or ""
                )
                normalized["thread_key"] = str(
                    origin_binding.get("thread_key") or "main"
                )
        elif terminal_rotation:
            origin_binding = dict(terminal_rotation["origin_binding"])
            normalized.update({
                "fresh_request": True,
                "prior_request_id": str(
                    terminal_rotation["prior_request_id"]
                ),
                "prior_request_revision": int(
                    terminal_rotation["prior_request_revision"]
                ),
                "prior_terminal_event_id": str(
                    terminal_rotation["prior_terminal_event_id"]
                ),
                "origin_binding": origin_binding,
            })
            if payload_request_id:
                normalized["request_id"] = payload_request_id
            if origin_binding.get("surface") == "channel":
                normalized["channel_id"] = str(
                    origin_binding.get("channel_id") or ""
                )
                normalized["thread_id"] = str(
                    origin_binding.get("thread_id") or "main"
                )
            elif origin_binding.get("surface") == "kanban_agent":
                normalized["project_id"] = str(
                    origin_binding.get("project_id") or ""
                )
                normalized["conversation_id"] = str(
                    origin_binding.get("conversation_id") or ""
                )
                normalized["thread_key"] = str(
                    origin_binding.get("thread_key") or "main"
                )
        for key in (
            "artifact_refs",
            "channel_id",
            "conversation_id",
            "idempotency_key",
            "project_id",
            "reason",
            "source_refs",
            "thread_id",
            "thread_key",
        ):
            if task_input_binding and key in {"artifact_refs", "source_refs"}:
                # Task-derived refs were already compiled into effective
                # parameters. Re-copying a raw top-level override here can
                # discard inherited Task refs after approval.
                continue
            if (request_projection or terminal_rotation) and key in {
                "channel_id",
                "conversation_id",
                "project_id",
                "thread_id",
                "thread_key",
            }:
                continue
            value = payload.get(key)
            if value not in (None, "", [], {}):
                normalized[key] = value
        return {
            "ok": True,
            "status": "ready",
            "schema_version": WORKFLOW_START_SCHEMA_VERSION,
            "action": WORKFLOW_START_ACTION,
            "task_id": task_id,
            "route_id": route_id,
            "payload": redact_obj(normalized),
            "route": redact_obj(route),
            "task": {
                "id": task.id,
                "title": task.title,
                "contract_digest": task_digest,
            },
        }
    def propose(
        self,
        writer: EventWriter,
        payload: dict[str, Any],
        *,
        actor: str,
        origin: str,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        preview = self.preview(
            payload,
            require_bindings=False,
            origin=origin,
        )
        if not preview.get("ok"):
            return preview
        proposed_payload = dict(preview["payload"])
        digest = proposal_payload_digest(
            WORKFLOW_START_ACTION,
            proposed_payload,
        )
        proposal_id = f"proposal-{digest[:24]}"
        prior = _proposal_by_id(
            writer.event_log.read_all(),
            proposal_id=proposal_id,
        )
        if prior is not None:
            return {
                **preview,
                "status": "proposal_ready",
                "proposal_event_id": prior.id,
                "proposal_id": proposal_id,
                "proposal_digest": digest,
                "replayed": True,
            }

        proposal_event = ZfEvent(
            type=PROPOSAL_EVENT,
            actor=actor,
            task_id=str(preview.get("task_id") or ""),
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        proposal = {
            "proposal_event_id": proposal_event.id,
            "proposal_id": proposal_id,
            "proposal_digest": digest,
            "revision": 1,
            "expires_at": "",
            "supersedes": "",
            "action": WORKFLOW_START_ACTION,
            "requested_action": WORKFLOW_START_ACTION,
            "payload": redact_obj(proposed_payload),
            "reason": str(
                proposed_payload.get("reason")
                or f"start workflow route {preview.get('route_id') or ''}"
            ),
            "confidence": "",
            "valid": True,
            "validation_error": "",
            "mutates_task_state": False,
        }
        proposal_event.payload = {
            "schema_version": "operator.action.proposal.v1",
            "proposal": redact_obj(proposal),
            "project_id": str(
                payload.get("project_id")
                or getattr(getattr(self.config, "project", None), "name", "")
                or ""
            ),
            "conversation_id": str(
                payload.get("conversation_id") or ""
            ),
            "thread_key": str(
                payload.get("thread_key")
                or payload.get("thread_id")
                or ""
            ),
            "turn_id": str(payload.get("turn_id") or ""),
            "source": origin,
            "surface": origin,
        }
        writer.append(proposal_event)
        return {
            **preview,
            "status": "proposal_ready",
            "proposal_event_id": proposal_event.id,
            "proposal_id": proposal_id,
            "proposal_digest": digest,
            "replayed": False,
        }


def _matches_canonical_origin(
    payload: dict[str, Any],
    origin_binding: dict[str, Any],
) -> bool:
    """Validate surface-specific return fields without conflating thread ids."""

    supplied_project = str(payload.get("project_id") or "").strip()
    canonical_project = str(origin_binding.get("project_id") or "").strip()
    if supplied_project and supplied_project != canonical_project:
        return False

    surface = str(origin_binding.get("surface") or "").strip()
    supplied_channel = str(payload.get("channel_id") or "").strip()
    if surface == "channel":
        supplied_thread = str(payload.get("thread_id") or "").strip()
        return not (
            supplied_channel
            and supplied_channel
            != str(origin_binding.get("channel_id") or "")
        ) and not (
            supplied_thread
            and supplied_thread
            != str(origin_binding.get("thread_id") or "")
        )

    supplied_conversation = str(
        payload.get("conversation_id") or ""
    ).strip()
    supplied_thread_key = str(
        payload.get("thread_key") or payload.get("thread_id") or ""
    ).strip()
    if surface == "kanban_agent":
        return not supplied_channel and not (
            supplied_conversation
            and supplied_conversation
            != str(origin_binding.get("conversation_id") or "")
        ) and not (
            supplied_thread_key
            and supplied_thread_key
            != str(origin_binding.get("thread_key") or "")
        )
    return not any((
        supplied_channel,
        supplied_conversation,
        supplied_thread_key,
    ))


def _active_request_replay_error(
    *,
    objective: str,
    parameters: dict[str, Any],
    requirement: dict[str, Any],
    manifest: dict[str, Any],
    route: dict[str, Any],
) -> str:
    """Return why an active Request cannot be treated as exact replay."""

    if objective != str(requirement.get("objective") or "").strip():
        return (
            "active Workflow Request objective is immutable; use controlled "
            "recovery/replan instead of workflow-start"
        )
    route_kind = str(route.get("kind") or "").strip()
    if route_kind and route_kind != str(requirement.get("kind") or "").strip():
        return "active Workflow Request kind does not match the selected route"

    aliases = {
        "source_ref": (manifest, "source_ref"),
        "source_root": (requirement, "source_root"),
        "target": (requirement, "target_root"),
        "target_root": (requirement, "target_root"),
        "acceptance": (requirement, "acceptance"),
        "constraints": (requirement, "constraints"),
        "open_questions": (requirement, "open_questions"),
        "backend": (manifest, "requested_backend"),
        "lanes": (manifest, "requested_lanes"),
        "requested_lanes": (manifest, "requested_lanes"),
        "strictness": (manifest, "strictness"),
    }
    ignored = {"request_id", "request_revision"}
    for key, supplied in parameters.items():
        if key in ignored or supplied in (None, "", [], {}):
            continue
        binding = aliases.get(str(key))
        if binding is None:
            return (
                f"active Workflow Request parameter {key!r} cannot be "
                "proven identical; use controlled recovery/replan"
            )
        owner, canonical_key = binding
        canonical = owner.get(canonical_key)
        if isinstance(supplied, (list, tuple)):
            same = list(supplied) == list(canonical or [])
        elif key in {"lanes", "requested_lanes"}:
            try:
                same = int(supplied) == int(canonical or 0)
            except (TypeError, ValueError):
                same = False
        else:
            same = str(supplied).strip() == str(canonical or "").strip()
        if not same:
            return (
                f"active Workflow Request parameter {key!r} is immutable; "
                "use controlled recovery/replan instead of workflow-start"
            )
    return ""


def workflow_start_proposal(
    events: Iterable[ZfEvent],
    proposal_event_id: str,
) -> dict[str, Any] | None:
    event = next(
        (
            item
            for item in events
            if item.id == proposal_event_id
            and item.type in PROPOSAL_EVENT_TYPES
        ),
        None,
    )
    if event is None or not isinstance(event.payload, dict):
        return None
    proposal = event.payload.get("proposal")
    if not isinstance(proposal, dict):
        return None
    if (
        canonical_proposal_action(str(proposal.get("action") or ""))
        != WORKFLOW_START_ACTION
        or not bool(proposal.get("valid"))
    ):
        return None
    return dict(proposal)


def _proposal_by_id(
    events: Iterable[ZfEvent],
    *,
    proposal_id: str,
) -> ZfEvent | None:
    for event in events:
        if event.type not in PROPOSAL_EVENT_TYPES:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        proposal = (
            payload.get("proposal")
            if isinstance(payload.get("proposal"), dict)
            else {}
        )
        if str(proposal.get("proposal_id") or "") == proposal_id:
            return event
    return None


def _failure(
    status: str,
    reason: str,
    *,
    status_code: int,
    task_id: str = "",
) -> dict[str, Any]:
    return {
        "_status_code": status_code,
        "ok": False,
        "status": status,
        "schema_version": WORKFLOW_START_SCHEMA_VERSION,
        "action": WORKFLOW_START_ACTION,
        "task_id": task_id,
        "reason": reason,
    }


__all__ = [
    "LEGACY_WORKFLOW_START_ACTION",
    "WORKFLOW_START_ACTION",
    "WORKFLOW_START_ACTIONS",
    "WORKFLOW_START_SCHEMA_VERSION",
    "WorkflowStartService",
    "is_workflow_start_action",
    "workflow_start_proposal",
]
