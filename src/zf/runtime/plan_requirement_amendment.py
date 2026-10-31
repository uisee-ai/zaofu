"""Exact owner-authorized Requirement revisions within an existing Run.

The immutable intake Request remains the Run's historical launch authority.
Only a controlled operator Plan rejection can publish a new effective Run
requirement. Drafts and agent-authored refs alone never authorize a change.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_preview_artifacts import read_preview_artifact
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

SCHEMA = "plan-requirement-amendment.v1"
_LEAF = re.compile(r"/(?:objective|(?:acceptance|constraints|scope)/\d+|clarification_answers/\d+/answer)$")


def current_owner_amendment(state_dir: Path, workflow_run_id: str, *, events=None) -> dict:
    """Resolve accepted owner evidence, never a loose payload's claimed ref."""
    if not workflow_run_id:
        return {}
    records = list(events) if events is not None else EventLog(state_dir / "events.jsonl").read_all()
    for event in reversed(records):
        body = event.payload if hasattr(event, "payload") else event.get("payload", {})
        kind = event.type if hasattr(event, "type") else event.get("type")
        actor = event.actor if hasattr(event, "actor") else event.get("actor")
        if (kind != "plan.rejected" or actor != "operator"
                or str(body.get("workflow_run_id") or body.get("trace_id") or "") != workflow_run_id
                or not str(body.get("via") or "").startswith("controlled-action:")):
            continue
        descriptor = body.get("requirement_amendment_ref")
        if not isinstance(descriptor, dict):
            continue
        amendment = hydrate_sidecar_ref(state_dir, descriptor).payload
        if (not isinstance(amendment, dict) or amendment.get("schema_version") != SCHEMA
                or amendment.get("workflow_run_id") != workflow_run_id
                or amendment.get("plan_id") != body.get("plan_id")
                or amendment.get("request_event_id") != body.get("request_event_id")
                or amendment.get("review_ref") != body.get("plan_review_ref")
                or amendment.get("requirement", {}).get("ref") != body.get("requirement_spec_ref")
                or amendment.get("requirement", {}).get("sha256") != body.get("requirement_spec_digest")):
            raise ValueError("owner requirement amendment identity mismatch")
        # Always validate the admitted body even when only its ref is requested.
        hydrate_sidecar_ref(state_dir, amendment["requirement"])
        return {**amendment, "descriptor": descriptor}
    return {}


def review_contract_context(state_dir: Path, project_root: Path, events: list, preview: dict) -> dict:
    request = next((e for e in events if e.id == preview.get("requested_event_id")), None)
    if request is None:
        return {}
    payload = request.payload
    run = str(payload.get("workflow_run_id") or payload.get("trace_id") or "")
    if not run:
        return {}
    amendment = current_owner_amendment(state_dir, run, events=events)
    from zf.runtime.goal_closure_bridge import _resolve_run_objective_ref

    ref = str(amendment.get("requirement", {}).get("ref") or _resolve_run_objective_ref(
        events, workflow_run_id=run, payload=payload, metadata={},
    ))
    expected = str(amendment.get("requirement", {}).get("sha256") or payload.get("requirement_spec_digest") or "")
    if not amendment:
        # A Planner's derived PRD port is not the intake Requirement version.
        # Prefer the immutable Requirement bound to this actual invocation.
        for event in reversed(events):
            body = event.payload
            if (event.type == "workflow.invoke.requested" and run in {
                    str(body.get(k) or "") for k in ("workflow_run_id", "run_id", "request_id", "trace_id")}
                    and body.get("requirement_spec_ref") and body.get("requirement_spec_digest")):
                ref, expected = str(body["requirement_spec_ref"]), str(body["requirement_spec_digest"])
                break
    task_map = next((a for a in preview.get("artifacts", []) if a.get("name") == "task_map"), {})
    task_ref = str(task_map.get("ref") or payload.get("task_map_ref") or "")
    if not ref or not task_ref:
        return {}
    if not amendment and ref.lower().endswith((".md", ".markdown", ".txt")):
        return {}  # Legacy document-only review has no Requirement JSON contract.

    def read(reference: str, digest: str = "") -> dict:
        raw = read_preview_artifact(reference, state_dir=state_dir, project_root=project_root, digest=digest)
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("review contract must contain a JSON object")
        return {"ref": reference, "sha256": hashlib.sha256(raw.encode()).hexdigest(), "body": body}

    if not expected:
        return {}  # No digest-bound formal version to authorize text changes against.
    requirement = read(ref, expected)
    if requirement["body"].get("schema_version") != "requirement-spec.v1":
        return {}  # Legacy Markdown requirements retain document-only review.
    return {"workflow_run_id": run, "request_event_id": request.id,
            "requirement": requirement,
            "task_map": read(task_ref, str(task_map.get("sha256") or payload.get("task_map_digest") or ""))}


def _claim_text(claim: Mapping[str, Any]) -> str:
    return str(claim.get("statement") or claim.get("text") or claim.get("claim") or "").strip()


def validate_contract_changes(context: dict, changes: Any) -> tuple[dict, dict]:
    if not isinstance(changes, dict) or set(changes) != {"requirement", "claims"}:
        raise ValueError("contract_changes requires requirement and claims lists")
    if any(not isinstance(changes[key], list) or len(changes[key]) > 64 for key in changes):
        raise ValueError("contract changes must be bounded lists")
    if not context:
        if any(changes.values()):
            raise ValueError("no bound Requirement and Task Map for contract amendment")
        return {}, {}
    requirement = copy.deepcopy(context["requirement"]["body"])
    task_map = copy.deepcopy(context["task_map"]["body"])
    seen = set()
    for patch in changes["requirement"]:
        if (not isinstance(patch, dict) or set(patch) != {"path", "before", "after"}
                or not isinstance(patch["path"], str) or not _LEAF.fullmatch(patch["path"])
                or patch["path"] in seen):
            raise ValueError("invalid or duplicate Requirement text path")
        seen.add(patch["path"])
        if any(not isinstance(patch[k], str) or not patch[k].strip() or len(patch[k]) > 100_000
               for k in ("before", "after")):
            raise ValueError("Requirement amendment requires nonempty bounded before/after text")
        parts = patch["path"].strip("/").split("/")
        parent = requirement
        try:
            for part in parts[:-1]:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
            key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
            if parent[key] != patch["before"]:
                raise ValueError("stale Requirement text")
            parent[key] = patch["after"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Requirement path does not exist") from exc
    claims = {str(c.get("goal_claim_id") or c.get("id") or ""): c
              for c in task_map.get("goal_claims", []) if isinstance(c, dict)}
    seen.clear()
    for patch in changes["claims"]:
        if (not isinstance(patch, dict) or set(patch) != {"goal_claim_id", "before", "after"}
                or not isinstance(patch["goal_claim_id"], str) or patch["goal_claim_id"] in seen):
            raise ValueError("invalid or duplicate claim amendment")
        seen.add(patch["goal_claim_id"])
        claim = claims.get(patch["goal_claim_id"])
        if (claim is None or _claim_text(claim) != patch["before"]
                or not isinstance(patch["after"], str) or not patch["after"].strip()
                or len(patch["after"]) > 100_000):
            raise ValueError("stale, missing or empty claim amendment")
        field = next((k for k in ("statement", "text", "claim") if claim.get(k)), "text")
        claim[field] = patch["after"]
    if changes["claims"] and not changes["requirement"]:
        raise ValueError("claim changes require a matching formal Requirement revision")
    return requirement, task_map


def authorize_plan_review(state_dir: Path, project_root: Path, writer, pending, descriptor: dict) -> dict:
    """Called under the Plan decision lock before its sole committing event."""
    from zf.runtime.plan_artifact_review import PlanArtifactReview, SCHEMA as REVIEW_SCHEMA
    from zf.runtime.workflow_preview_review import _quote_range

    draft = hydrate_sidecar_ref(state_dir, descriptor).payload
    if draft.get("schema_version") != REVIEW_SCHEMA or draft.get("plan_id") != pending.payload.get("plan_id"):
        raise ValueError("review does not bind the pending Plan")
    service = PlanArtifactReview(state_dir, project_root, writer)
    ctx = service.context(draft)
    if ctx["preview"].get("requested_event_id") != pending.id or not ctx["editable"]:
        raise ValueError("stale Plan review")
    start, end = _quote_range(ctx["body"], draft["quote"])
    if draft["body"] != ctx["body"][:start] + draft["replacement"] + ctx["body"][end:]:
        raise ValueError("review changes text outside the quote")
    changes = draft.get("contract_changes") or {"requirement": [], "claims": []}
    if not any(changes.values()):
        return {"plan_review_ref": descriptor}
    context = review_contract_context(state_dir, project_root, writer.event_log.read_all(), ctx["preview"])
    if context != draft.get("contract_context"):
        raise ValueError("stale Requirement or Task Map binding")
    requirement, _ = validate_contract_changes(context, changes)
    previous = context["requirement"]["body"]
    from zf.runtime.goal_claim_set import _objective_acceptance_claims
    requirement["acceptance_claim_ids"] = [c["goal_claim_id"] for c in
        _objective_acceptance_claims(previous, objective_ref=context["requirement"]["ref"])]
    requirement.update(revision=int(previous.get("revision") or 0) + 1,
                       revision_kind="owner_plan_amendment",
                       supersedes_requirement_ref=context["requirement"]["ref"],
                       confirmed=True, confirmed_by="operator")

    def persist(body: dict, kind: str, schema: str) -> dict:
        return write_immutable_json_sidecar(state_dir, body, root="workflow/plan-amendments",
            kind=kind, schema_version=schema, created_by="plan-owner-amendment", source_event_id=pending.id)

    requirement_ref = persist(requirement, "requirement_spec", "requirement-spec.v1")
    amendment_ref = persist({
        "schema_version": SCHEMA, "workflow_run_id": context["workflow_run_id"],
        "plan_id": draft["plan_id"], "request_event_id": pending.id, "review_ref": descriptor,
        "base_requirement": {k: v for k, v in context["requirement"].items() if k != "body"},
        "previous_requirement": persist(previous, "requirement_spec", "requirement-spec.v1"),
        "requirement": requirement_ref, "contract_changes": changes,
        "base_task_map": {k: v for k, v in context["task_map"].items() if k != "body"},
    }, "requirement_amendment", SCHEMA)
    return {"plan_review_ref": descriptor, "requirement_amendment_ref": amendment_ref,
            "requirement_spec_ref": requirement_ref["ref"],
            "requirement_spec_digest": requirement_ref["sha256"]}


def amended_task_map_baseline(state_dir: Path, workflow_run_id: str, previous: Mapping) -> dict:
    amendment = current_owner_amendment(state_dir, workflow_run_id)
    baseline = copy.deepcopy(dict(previous))
    if not amendment:
        return baseline
    changes = {p["goal_claim_id"]: p for p in amendment["contract_changes"]["claims"]}
    for claim in baseline.get("goal_claims", []):
        change = changes.get(str(claim.get("goal_claim_id") or claim.get("id") or ""))
        if change and _claim_text(claim) == change["before"]:
            key = next((k for k in ("statement", "text", "claim") if claim.get(k)), "text")
            claim[key] = change["after"]
    return baseline


def amendment_binding(state_dir: Path, workflow_run_id: str, *, events=None) -> dict:
    amendment = current_owner_amendment(state_dir, workflow_run_id, events=events)
    if not amendment:
        return {}
    return {"requirement_amendment_ref": amendment["descriptor"],
            "requirement_spec_ref": amendment["requirement"]["ref"],
            "requirement_spec_digest": amendment["requirement"]["sha256"]}


def amended_claim_set_baseline(state_dir: Path, workflow_run_id: str, previous: Mapping | None) -> dict | None:
    """Authorize exact before/after text pairs; preserve all other safeguards."""
    if previous is None:
        return None
    amendment = current_owner_amendment(state_dir, workflow_run_id)
    baseline = copy.deepcopy(dict(previous))
    if not amendment:
        return baseline
    changes = {p["goal_claim_id"]: (p["before"], p["after"])
               for p in amendment["contract_changes"]["claims"]}
    from zf.runtime.goal_claim_set import _objective_acceptance_claims
    old = hydrate_sidecar_ref(state_dir, amendment["previous_requirement"]).payload
    new = hydrate_sidecar_ref(state_dir, amendment["requirement"]).payload
    old_claims = {c["goal_claim_id"]: c for c in _objective_acceptance_claims(old, objective_ref="")}
    for claim in _objective_acceptance_claims(new, objective_ref=""):
        before = old_claims.get(claim["goal_claim_id"])
        if before:
            changes[claim["goal_claim_id"]] = (before["text"], claim["text"])
    for claim in baseline.get("claims", []):
        pair = changes.get(claim.get("goal_claim_id"))
        if pair and claim.get("text") == pair[0]:
            claim["text"] = pair[1]
    return baseline
