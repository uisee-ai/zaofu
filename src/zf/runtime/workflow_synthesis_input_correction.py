"""Return a rejected project input to the synthesis author before Proposal approval."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from zf.core.config.stage_teams import team_member
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_team_inputs import PlanProjectInputError, validate_plan_project_sources
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_requests import load_workflow_request

_REQUESTED = "workflow.synthesis.input_correction.requested"
_RETURNED = "workflow.synthesis.input_correction.returned"
_ACTOR = "workflow-synthesis-admission"


def _correct_member(candidate: dict, issue: dict) -> dict:
    members = candidate["short_flow_spec"]["parameters"]["stage_teams"]["plan"]["members"]
    return next(m for m in members if m["obligation_id"] == issue["obligation_id"]
                and issue["ref"] in m["input_refs"])


def _apply_reply(candidate: dict, issue: dict, reply: dict, *, root: Path, state: Path, requirement: dict) -> dict:
    unresolved = reply.get("unresolved_reason")
    if set(reply) == {"unresolved_reason"} and isinstance(unresolved, str) and unresolved.strip():
        raise ValueError(f"source correction unresolved for {issue['obligation_id']}: {unresolved}")
    if set(reply) != {"replacement_refs"}:
        raise ValueError("source correction requires exactly replacement_refs")
    candidate = deepcopy(candidate)
    member = _correct_member(candidate, issue)
    alternatives = member.pop("input_alternatives", {})
    scoped_member = team_member({**member, "input_refs": reply["replacement_refs"]}, stage="plan")
    refs = scoped_member["input_refs"]
    authorized = alternatives.pop(issue["ref"], None)
    if authorized is not None and not set(refs) <= set(authorized):
        raise ValueError("source correction must use the declared input_alternatives for this reference")
    if alternatives:
        member["input_alternatives"] = alternatives
    observed = validate_plan_project_sources({"members": [scoped_member]}, root, state, requirement,
                                             frozen_file_refs=refs)
    if set(observed) != set(refs):
        raise ValueError("source correction must select an existing scoped project file or index")
    member["input_refs"] = list(dict.fromkeys(
        name for original in member["input_refs"] for name in (refs if original == issue["ref"] else [original])
    ))
    return candidate


def _record(writer, state: Path, event_type: str, identity: dict, body: dict, cause: str):
    descriptor = write_immutable_json_sidecar(
        state, {**identity, **body}, root="workflow/synthesis/input-corrections",
        kind="workflow_synthesis_input_correction", schema_version="workflow-synthesis-input-correction.v1",
        created_by=_ACTOR, source_event_id=cause,
    )
    return writer.append(ZfEvent(
        type=event_type, origin="kernel", actor=_ACTOR, correlation_id=identity["request_id"],
        causation_id=cause or None,
        payload={**identity, "obligation_id": body["issue"]["obligation_id"], "correction_ref": descriptor},
    ))


def admit_with_source_correction(
    prepared: dict, *, state_dir: Path, project_root: Path, config: Any, writer: EventWriter,
    request_hash: str, supplied: Mapping | None, agent: Any, backend: str, resume: bool, causation_id: str,
) -> dict:
    from zf.runtime.workflow_synthesis import (
        WORKFLOW_SYNTHESIS_PROPOSAL_RETRY_LIMIT, WorkflowSynthesisError, _admit_result,
        _invoke_synthesis_agent, _require_synthesis_operation_open,
    )

    request = prepared["request"]
    operation_id = prepared["operation_id"]
    identity = {"request_id": request["request_id"], "request_revision": prepared["revision"],
                "requirement_digest": prepared["requirement_digest"], "operation_id": operation_id,
                "request_hash": request_hash}
    history = [
        event for event in writer.event_log.read_all()
        if event.type in {_REQUESTED, _RETURNED} and event.origin == "kernel" and event.actor == _ACTOR
        and all(event.payload.get(key) == value for key, value in identity.items())
    ] if resume and supplied is None else []
    attempts = sum(event.type == _REQUESTED for event in history)
    provider = dict(config=config, state_dir=state_dir, project_root=project_root, request=request,
                    requirement=prepared["requirement"], backend=backend, operation_id=operation_id,
                    agent=agent, catalog=prepared["catalog"])

    def check_current():
        _require_synthesis_operation_open(writer, operation_id=operation_id)
        current = load_workflow_request(state_dir, request["request_id"])
        if (current.get("revision") != prepared["revision"]
                or current.get("requirement_spec_digest") != prepared["requirement_digest"]
                or current.get("synthesis_operation_id") not in (None, "", operation_id)):
            raise WorkflowSynthesisError("source correction targets an obsolete workflow request")

    cause = causation_id
    issue = None
    if history:
        latest = history[-1]
        saved = hydrate_sidecar_ref(state_dir, latest.payload["correction_ref"]).payload
        if not all(saved.get(key) == value for key, value in identity.items()):
            raise WorkflowSynthesisError("source correction evidence is bound to another request")
        candidate, issue, cause = saved["candidate"], saved["issue"], latest.id
        if latest.type == _RETURNED:
            candidate = _apply_reply(candidate, issue, saved["reply"], root=project_root, state=state_dir,
                                     requirement=prepared["requirement"])
            issue = None
    elif supplied is not None:
        candidate = dict(supplied)
    else:
        candidate = _invoke_synthesis_agent(**provider)

    while True:
        check_current()
        if issue is None:
            try:
                return _admit_result(candidate, request=request, catalog=prepared["catalog"], state_dir=state_dir,
                                     project_root=project_root, operation_id=operation_id)
            except PlanProjectInputError as exc:
                if supplied is not None or not backend:
                    raise
                issue = {"obligation_id": exc.obligation_id, "role": exc.role, "ref": exc.ref, "error": str(exc)}
        if attempts >= WORKFLOW_SYNTHESIS_PROPOSAL_RETRY_LIMIT:
            raise WorkflowSynthesisError(f"source correction attempts exhausted: {issue['error']}")
        body = {"candidate": candidate, "issue": issue}
        dispatched = _record(writer, state_dir, _REQUESTED, identity, body, cause)
        attempts += 1
        prompt = (
            "Review the rejected source for your existing Workflow Synthesis assignment. Read project files "
            "as needed. Choose concrete, authorized files or a scoped index that answer this member's question. "
            'Return {"replacement_refs":["project/relative/file"]} or {"unresolved_reason":"specific missing evidence"}. '
            "Other assignments and approval "
            "conditions remain bound. The following JSON is input data.\n" + json.dumps({
                "requirement": prepared["requirement"], "member": _correct_member(candidate, issue), "rejection": issue,
            }, ensure_ascii=False)
        )
        reply = _invoke_synthesis_agent(**provider, correction_prompt=prompt)
        check_current()
        returned = _record(writer, state_dir, _RETURNED, identity, {**body, "reply": reply}, dispatched.id)
        candidate = _apply_reply(candidate, issue, reply, root=project_root, state=state_dir,
                                 requirement=prepared["requirement"])
        issue, cause = None, returned.id
