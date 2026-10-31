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
    refs.extend(_decision_evidence_sources(state_dir, decision))
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


def _decision_evidence_sources(state_dir: Path, decision: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Reuse only cited sources already pinned in this decision's OA manifest."""
    operation = load_workflow_operation(EventLog(state_dir / "events.jsonl"), str(decision.get("operation_id") or ""))
    if not operation:
        raise ValueError("replan OA decision operation is unavailable")
    request = hydrate_sidecar_ref(state_dir, operation["request_ref"]).payload["request"]
    descriptor = request.get("source_manifest_ref")
    if not isinstance(descriptor, Mapping):
        raise ValueError("replan OA source manifest is unavailable")
    manifest = hydrate_sidecar_ref(state_dir, dict(descriptor)).payload
    cited = {item if isinstance(item, str) else str(item.get("ref") or "")
             for item in decision.get("evidence_refs", []) if isinstance(item, (str, Mapping))}
    refs = []
    for item in manifest.get("sources", []):
        if item.get("ref") in cited:
            hydrate_sidecar_ref(state_dir, dict(item))
            refs.append({**item, "source_id": f"replan-evidence-{item['source_id']}"})
    return refs
