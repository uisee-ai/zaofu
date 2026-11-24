"""Expose the immutable baseline and admitted OA authority to a replan reader."""

from pathlib import Path
from typing import Any, Mapping

from zf.core.events.log import EventLog
from zf.runtime.artifact_read_ledger import materialize_attempt_source_ref
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


def replan_authority_sources(
    *, state_dir: Path, project_root: Path, payload: Mapping[str, Any],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    package_ref = str(payload.get("plan_artifact_package_ref") or "")
    package_digest = str(payload.get("plan_artifact_package_digest") or "")
    if not package_ref:
        return []
    if not package_digest:
        raise ValueError("replan baseline requires package digest")
    package = hydrate_plan_artifact_package(state_dir, {"ref": package_ref, "sha256": package_digest})
    run_id = str(payload.get("workflow_run_id") or payload.get("run_id") or payload.get("trace_id") or "")
    if run_id and package["workflow_run_id"] != run_id:
        raise ValueError("replan baseline belongs to another workflow run")

    def source(ref: str, digest: str, source_id: str) -> dict[str, Any]:
        if not ref or not digest:
            raise ValueError(f"{source_id} requires ref and digest")
        result = materialize_attempt_source_ref(
            state_dir=state_dir, project_root=project_root, ref=ref,
            source_id=source_id, kind=source_id,
        )
        if result.get("sha256") != digest:
            raise ValueError(f"{source_id} digest mismatch")
        result["allowed_paths"] = ["$"]
        return result

    refs = [source(package_ref, package_digest, "plan-rework-package")]
    refs.extend(source(port["ref"], port["sha256"], f"baseline-plan-port-{port['logical_name']}")
                for port in [*package["produced"], *package["inherited"]])
    historical = payload.get("historical_orchestration_context") or {}
    observation = historical.get("orchestrator_decision_ref")
    if isinstance(observation, Mapping):
        refs.append(source(observation["ref"], observation["sha256"], "plan-rework-observation"))
    authority = dict(payload)
    if not authority.get("orchestrator_decision_ref"):
        authority.update(_legacy_oa_authority(state_dir, payload, context, run_id, package_digest))
    decision_ref = authority.get("orchestrator_decision_ref")
    if not decision_ref:
        return refs
    if not isinstance(decision_ref, Mapping):
        raise ValueError("replan OA decision requires an immutable descriptor")
    decision = hydrate_sidecar_ref(state_dir, dict(decision_ref)).payload
    identity = decision.get("identity", {})
    if (identity.get("workflow_run_id") != package["workflow_run_id"]
            or identity.get("plan_artifact_package_digest") != package_digest
            or identity.get("task_map_generation") != package["task_map_generation"]
            or decision.get("decision") != "return_to_plan"):
        raise ValueError("replan OA decision differs from baseline identity")
    refs.append(source(decision_ref["ref"], decision_ref["sha256"], "plan-rework-decision"))
    refs.append(source(str(authority.get("orchestration_delta_ref") or ""),
                       str(authority.get("orchestration_delta_digest") or ""), "plan-rework-delta"))
    for key in ("orchestrator_decision_ref", "orchestration_delta_ref", "orchestration_delta_digest"):
        context[key] = authority[key]
    scope = set(context.get("task_ids") or context.get("failed_task_ids")
                or payload.get("task_ids") or payload.get("failed_task_ids") or [])
    if context.get("resume_scope") == "candidate_diagnosis_only":
        if not (context.get("rework_summary") or {}).get("candidate_obligations"):
            context["candidate_diagnosis_evidence"] = {"status": "unavailable", "reason": "no_bound_candidate_obligations"}
            return refs
        from zf.runtime.plan_candidate_diagnosis_inputs import candidate_diagnosis_inputs

        evidence, index = candidate_diagnosis_inputs(state_dir, payload, context, decision, decision_ref, refs)
        context["candidate_diagnosis_evidence"] = index
        return [*refs, *evidence]
    evidence = _decision_evidence_sources(state_dir, decision, task_ids=scope)
    refs.extend(evidence)
    for item in evidence:
        if item["source_id"] != "replan-evidence-semantic-failure-input":
            continue
        failure = hydrate_sidecar_ref(state_dir, item).payload
        target = failure["target"]
        if (target.get("task_id") not in scope or failure.get("workflow_run_id") != run_id
                or target.get("task_map_generation") != package["task_map_generation"]):
            continue
        # Historical OA manifests may lack the Verify body. Use the original
        # kernel admissions, never descriptors embedded in the failure report.
        from zf.runtime.task_verification_failure_sources import task_verification_failure_sources

        events = EventLog(state_dir / "events.jsonl").read_all()
        admissions = {row["event_id"] for row in failure["failure_events"]}
        exact = task_verification_failure_sources(
            state_dir, events, admissions, task_id=target["task_id"], workflow_run_id=run_id,
            package={"task_map_generation": package["task_map_generation"], "package_digest": package_digest},
        )
        for descriptor in exact:
            if not any(row["ref"] == descriptor["ref"] and row["sha256"] == descriptor["sha256"] for row in refs):
                refs.append(source(descriptor["ref"], descriptor["sha256"], "replan-" + descriptor["source_id"]))
    return refs


def _legacy_oa_authority(
    state_dir: Path, payload: Mapping[str, Any], context: Mapping[str, Any],
    run_id: str, package_digest: str,
) -> dict[str, Any]:
    """Recover dropped legacy refs only from an exact canonical replan marker."""
    classification = context.get("replan_classification") or context.get("classification")
    if classification != "oa_semantic_return_to_plan":
        return {}
    fingerprint = (context.get("rework_summary") or {}).get("failure_fingerprint")
    if not fingerprint or not run_id:
        return {}
    scope = set(context.get("task_ids") or context.get("failed_task_ids") or [])
    generation = str(payload.get("task_map_generation") or "")
    matches = []
    for event in EventLog(state_dir / "events.jsonl").read_all():
        body = event.payload
        if (event.type == "orchestrator.replan_requested"
                and body.get("workflow_run_id") == run_id
                and body.get("plan_artifact_package_digest") == package_digest
                and body.get("task_map_generation") == generation
                and (body.get("rework_summary") or {}).get("failure_fingerprint") == fingerprint
                and set(body.get("task_ids") or body.get("failed_task_ids") or []) == scope):
            matches.append(body)
    if not matches:
        raise ValueError("legacy OA replan authority has no exact marker")
    identities = {(str(m.get("orchestrator_decision_ref")),
                   m.get("orchestration_delta_ref"), m.get("orchestration_delta_digest"))
                  for m in matches}
    if len(identities) != 1:
        raise ValueError("legacy OA replan authority is ambiguous")
    return {key: matches[0][key] for key in (
        "orchestrator_decision_ref", "orchestration_delta_ref", "orchestration_delta_digest",
    )}


def _decision_evidence_sources(
    state_dir: Path, decision: Mapping[str, Any], *, task_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Reuse only cited sources already pinned in this decision's OA manifest."""
    operation = load_workflow_operation(EventLog(state_dir / "events.jsonl"), str(decision.get("operation_id") or ""))
    if not operation:
        raise ValueError("replan OA decision operation is unavailable")
    request = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload["request"]
    descriptor = request.get("source_manifest_ref")
    if not isinstance(descriptor, Mapping):
        raise ValueError("replan OA source manifest is unavailable")
    manifest = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
    legacy = decision.get("evidence_refs") or []
    citations = list(legacy)
    for directive in (decision.get("delta") or {}).get("directives", []):
        target = directive.get("target") or {}
        if directive.get("action") == "return_to_plan" and (task_ids is None or target.get("task_id") in task_ids):
            citations.extend(directive.get("basis_refs") or [])
    legacy_paths = {item for item in legacy if isinstance(item, str)}
    legacy_paths.update(item.get("ref") for item in legacy if isinstance(item, Mapping) and not item.get("sha256"))
    cited = {(item.get("ref"), item.get("sha256")) for item in citations if isinstance(item, Mapping)}
    refs = []
    for item in manifest.get("sources", []):
        if item["ref"] in legacy_paths or (item["ref"], item["sha256"]) in cited:
            hydrate_sidecar_ref(state_dir, dict(item))
            refs.append({**item, "source_id": f"replan-evidence-{item['source_id']}"})
    return refs
