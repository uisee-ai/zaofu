"""Bind bounded Candidate findings to their approved owner and the integration reader."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import canonical_json_sha256, write_immutable_json_sidecar
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def reader_fanout_trigger(
    events: Sequence[ZfEvent], by_id: Mapping[str, ZfEvent], fanout_id: str,
) -> ZfEvent | None:
    if not fanout_id:
        return None
    start = next((e for e in reversed(events) if e.type == "fanout.started" and e.origin == "kernel"
                  and e.payload.get("fanout_id") == fanout_id), None)
    return by_id.get(start.payload.get("trigger_event_id")) if start else None


def scoped_journey_spec(spec: Mapping[str, Any], scope: Mapping[str, Any]) -> dict:
    """Derive a report view; an explicitly assigned journey is required for this Reader."""
    ids = set(scope["journey_ids"])
    journeys = [{**row, "mandatory": True} for row in spec["user_journeys"] if row["journey_id"] in ids]
    if len(journeys) != len(ids):
        raise ValueError("Candidate Reader scope references an unknown journey")
    return {**spec, "user_journeys": journeys}


def _verify_assignment(
    spec: Mapping[str, Any], scope: Mapping[str, Any], role: str,
    target: str, package_digest: str,
) -> set[str]:
    from zf.runtime.candidate_reader_assignments import candidate_acceptance_id

    assignments = spec.get("candidate_reader_assignments") or []
    assignment = next((row for row in assignments if row["role_instance"] == role), None)
    if (not assignment or not isinstance(scope, Mapping) or scope.get("role_instance") != role
            or not target or scope.get("target_commit") != target
            or not package_digest or scope.get("plan_artifact_package_digest") != package_digest
            or not scope.get("candidate_event_id")
            or scope.get("assignments_digest") != canonical_json_sha256(assignments)):
        raise ValueError("Candidate Reader assignment does not match the frozen target, Plan or owner")
    expected = {candidate_acceptance_id(row["task_id"], row["acceptance_id"])
                for row in assignment["acceptance"]}
    if (set(scope.get("acceptance_ids") or []) != expected
            or set(scope.get("journey_ids") or []) != set(assignment["journey_ids"])):
        raise ValueError("Candidate Reader scope does not match its approved acceptance or journeys")
    return expected


def bind_scoped_report(
    adapted: Any, state: Path, operation: Mapping[str, Any], result: dict[str, Any],
    spec: Mapping[str, Any], expected: Mapping[str, str], scope: Mapping[str, Any],
    *, source_event_id: str, persist: bool,
) -> Any:
    from zf.runtime.product_acceptance import validate_product_acceptance_report

    role = operation.get("role_instance") or (operation.get("result_identity") or {}).get("role_instance")
    assigned = _verify_assignment(spec, scope, role, expected["target_commit"],
                                  result.get("plan_artifact_package_digest"))
    rows = result.get("requirement_results") or []
    if (not isinstance(rows, list) or len(rows) != len(assigned)
            or any(not isinstance(row, Mapping) for row in rows)
            or {row.get("acceptance_id") for row in rows} != assigned):
        raise ValueError("Candidate Reader result must cover exactly its assigned acceptance IDs")
    if result.get("verdict") == "passed" and any(row.get("status") != "passed" for row in rows):
        raise ValueError("passed Candidate Reader requires passing evidence for every assigned acceptance ID")
    if any(result.get(key) for key in ("product_acceptance_report_ref", "product_acceptance_report_digest")):
        raise ValueError("bounded Candidate Reader cannot supply a full-product report reference")
    report = result.pop("product_acceptance_report", result.get("candidate_reader_journeys") or None)
    if scope["journey_ids"] or report is not None:
        if not isinstance(report, Mapping):
            raise ValueError("Candidate Reader must submit its assigned journey observations")
        validate_product_acceptance_report(report, spec=scoped_journey_spec(spec, scope), expected=expected)
        if result.get("verdict") == "passed" and report["verdict"] != "passed":
            raise ValueError("passed Candidate Reader has a nonpassing journey report")
    for field in ("product_acceptance_required", "product_acceptance_verdict",
                  "provider_qualification_required", "provider_qualification_status",
                  "provider_qualification_receipt_refs"):
        result.pop(field, None)
    result.update(candidate_reader_scope=dict(scope),
                  candidate_reader_journeys=(dict(report) if report is not None else {}),
                  product_acceptance_spec_ref=expected["product_acceptance_spec_ref"],
                  product_acceptance_spec_digest=expected["product_acceptance_spec_digest"])
    if not persist:
        return replace(adapted, payload=result)
    descriptor = write_immutable_json_sidecar(
        state, result, root=f"call-results/control/{adapted.schema_version}", kind="call_control_result",
        schema_version=adapted.schema_version, created_by="call-result-admission:candidate-reader",
        source_event_id=source_event_id,
    )
    return replace(adapted, payload=result, descriptor=descriptor)


def validate_integration_readers(
    state: Path, events: Sequence[ZfEvent], operation: Mapping[str, Any],
    spec: Mapping[str, Any], expected: Mapping[str, str], package_digest: str,
) -> list[dict[str, str]]:
    """Require admitted results of all and only the owners in this discovery's predecessor."""
    from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
    from zf.runtime.flow_discovery_context import _admitted_verify_results

    run = expected["workflow_run_id"]
    fanout = operation.get("fanout_id") or (operation.get("result_identity") or {}).get("fanout_id")
    if not fanout:
        raise ValueError("Candidate integration needs a frozen discovery fanout")
    by_id = {event.id: event for event in events}

    discovery = reader_fanout_trigger(events, by_id, fanout)
    if (not discovery or discovery.type != "flow.discovery.requested" or discovery.origin != "kernel"
            or discovery.payload.get("workflow_run_id") != run):
        raise ValueError("Candidate integration lacks its exact discovery request")
    source = by_id.get(discovery.payload.get("verification_event_id") or discovery.causation_id)
    if (not source or source.type not in {"test.passed", "verify.passed"} or source.origin != "kernel"
            or source.payload.get("workflow_run_id") != run):
        raise ValueError("Candidate integration lacks a successful Candidate fanout")
    readers = source.payload.get("fanout_id")
    trigger = reader_fanout_trigger(events, by_id, readers)
    if not trigger or trigger.type != "candidate.ready" or trigger.origin != "kernel":
        raise ValueError("Candidate integration lacks a frozen Candidate fanout trigger")
    history = list(events)
    freeze = frozen_candidate_for_payload(history, run, payload={"candidate_event_id": trigger.id})
    current = frozen_candidate_for_payload(history, run, payload={})
    if (not freeze or not current or freeze.id != current.id or freeze.origin != "kernel"
            or freeze.payload.get("candidate_head_commit") != expected["target_commit"]
            or freeze.payload.get("plan_artifact_package_digest") != package_digest):
        raise ValueError("Candidate integration results are not bound to the current freeze")
    admissions = [e for e in events if e.type != "workflow.call.result.admitted" or e.origin == "kernel"]
    refs = _admitted_verify_results(admissions, payload=discovery.payload, fanout_id=readers)
    offered = {(row.get("ref"), row.get("sha256")) for row in discovery.payload.get("result_refs") or []
               if isinstance(row, Mapping)}
    by_role = {}
    for ref in refs:
        if ref.get("kind") != "call_control_result":
            continue
        if (ref["ref"], ref["sha256"]) not in offered:
            raise ValueError("Candidate integration is missing the admitted Reader input")
        body = hydrate_sidecar_ref(state, ref).payload
        role = body.get("role_instance")
        scope = body.get("candidate_reader_scope") or {}
        _verify_assignment(spec, scope, role, expected["target_commit"], package_digest)
        if (role in by_role or body.get("fanout_id") != readers
                or scope["candidate_event_id"] != freeze.id or body.get("target_commit") != expected["target_commit"]
                or body.get("workflow_run_id") != run or body.get("task_map_generation") != expected["task_map_generation"]
                or body.get("product_acceptance_spec_digest") != expected["product_acceptance_spec_digest"]
                or body.get("execution_status") != "completed" or body.get("verdict") != "passed"):
            raise ValueError("Candidate integration has duplicate, stale or nonpassing Reader evidence")
        by_role[role] = {"role_instance": role, "ref": ref["ref"], "sha256": ref["sha256"]}
    if set(by_role) != {row["role_instance"] for row in spec["candidate_reader_assignments"]}:
        raise ValueError("Candidate integration has incomplete assigned Reader coverage")
    return [by_role[role] for role in sorted(by_role)]
