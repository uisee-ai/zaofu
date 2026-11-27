"""Keep incidental usage observations stable within one prepared Plan review."""

from typing import Any, Mapping

from zf.runtime.artifact_read_ledger import _safe_component
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


def remove_replayed_scoped_read_projection(
    runtime: Any, *, payload: dict[str, Any], operation_id: str, attempt_id: str,
) -> None:
    """Do not reinterpret a frozen derived policy as fresh explicit input."""
    projected = payload.get("required_reads")
    if not isinstance(projected, list):
        return
    operation = load_workflow_operation(runtime.event_log, operation_id)
    if operation is None:
        return
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload["request"]
    if request.get("active_attempt_id") != attempt_id or projected != request.get("required_reads"):
        return
    descriptor = request.get("input_consumption_policy_ref")
    if not isinstance(descriptor, Mapping) or descriptor.get("sha256") != request.get("read_policy_digest"):
        return
    policy = hydrate_sidecar_ref(runtime.state_dir, dict(descriptor)).payload
    if (policy.get("attempt_id") == attempt_id and policy.get("required_reads") == projected
            and any(isinstance(row, Mapping) and row.get("artifact_id") == "unchanged-plan-review.json"
                    for row in projected)):
        from zf.runtime.plan_local_review_output import _scoped_policy

        if _scoped_policy(runtime, operation):
            payload.pop("required_reads", None)
            return
    scoped_sources = {
        str(row.get("source_id") or "") for row in projected if isinstance(row, Mapping)
        and str(row.get("source_id") or "").startswith("child-artifact-")
        and str(row.get("json_path") or "").startswith("$.tasks[")
    }
    full_sources = {str(row.get("source_id") or "") for row in projected
                    if isinstance(row, Mapping) and row.get("json_path") == "$"}
    if (policy.get("attempt_id") != attempt_id or policy.get("required_reads") != projected
            or not any(isinstance(row, Mapping) and row.get("artifact_id") == "scoped_plan_review.json" for row in projected)
            or not any(isinstance(row, Mapping) and str(row.get("source_id") or "").startswith("baseline-plan-port-")
                       and str(row.get("json_path") or "").startswith("$.tasks[") for row in projected)
            or not scoped_sources - full_sources):
        return
    payload.pop("required_reads", None)


def pin_replayed_review_usage(
    runtime: Any, *, payload: dict[str, Any], operation_id: str,
) -> None:
    sources = [source for source in payload.get("input_refs", [])
               if isinstance(source, Mapping)
               and source.get("source_id") == "plan-execution-review"]
    if len(sources) != 1:
        return
    operation = load_workflow_operation(runtime.event_log, operation_id)
    if operation is None:
        return
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload["request"]
    digest = str(request.get("source_manifest_digest") or "")
    attempt = _safe_component(str(request.get("active_attempt_id") or ""))
    manifest = hydrate_sidecar_ref(runtime.state_dir, {
        "ref": f"artifacts/attempts/{attempt}/source-manifests/{digest}.json",
        "sha256": digest,
    }).payload
    previous = [source for source in manifest.get("sources", [])
                if source.get("source_id") == "plan-execution-review"]
    if len(previous) != 1:
        return
    old_source, new_source = previous[0], sources[0]
    old = hydrate_sidecar_ref(runtime.state_dir, dict(old_source)).payload
    new = hydrate_sidecar_ref(runtime.state_dir, dict(new_source)).payload
    from zf.runtime.plan_execution_delivery import expanded_verification

    old_comparable = expanded_verification(runtime.state_dir, old, manifest.get("sources", []))
    new_comparable = expanded_verification(runtime.state_dir, new, payload.get("input_refs", []))
    from zf.runtime.plan_readonly_acceptance_review import INITIAL_ACCEPTANCE, READONLY_SCOPE

    old_checks = old_comparable.get("execution_comparisons") or {}
    new_checks = new_comparable.get("execution_comparisons") or {}
    old_scope, new_scope = old_checks.get(READONLY_SCOPE) or {}, new_checks.get(READONLY_SCOPE) or {}
    if (old_scope and "initial_acceptance_review" not in old_scope
            and INITIAL_ACCEPTANCE not in (old_scope.get("facts") or {})
            and "initial_acceptance_review" in new_scope):
        legacy = {k: v for k, v in new_scope.items() if k != "initial_acceptance_review"}
        legacy["facts"] = {k: v for k, v in legacy["facts"].items() if k != INITIAL_ACCEPTANCE}
        new_comparable["execution_comparisons"] = {**new_checks, READONLY_SCOPE: legacy}
    # The new comparison index is derived entirely from the bound Task Map and
    # compiled policy. Keep older attempts' exact summary and review obligations.
    bindings = old_comparable.get("input_binding") or {}
    if (all(bindings.get(k) for k in ("task_map_digest", "effective_policy_digest"))
            and bindings == new_comparable.get("input_binding")):
        if "execution_comparisons" not in old_comparable:
            new_comparable.pop("execution_comparisons", None)
        else:
            key = "standard_with_empty_write_scope"
            old_check = old_comparable["execution_comparisons"].get(key)
            new_checks = dict(new_comparable.get("execution_comparisons") or {})
            # An already issued review retains the citation rules it advertised.
            for name, frozen in old_comparable["execution_comparisons"].items():
                if name in new_checks and "runtime_evidence_fields" not in frozen:
                    new_checks[name] = {field: value for field, value in new_checks[name].items()
                                        if field != "runtime_evidence_fields"}
            new_comparable["execution_comparisons"] = new_checks
            current = new_checks.get(key) or {}
            if old_check == {"task_ids": current.get("task_ids")} and current.get("before_rework"):
                new_comparable["execution_comparisons"] = {**new_checks, key: old_check}
    # Usage is observational, while all candidate/policy/graph facts remain
    # request identity. A real change must still reach the divergence guard.
    if ({key: value for key, value in old_comparable.items() if key != "usage_observations"}
            != {key: value for key, value in new_comparable.items() if key != "usage_observations"}):
        return
    if payload.get("plan_review_disposition_digest") == new_source["sha256"]:
        pinned = (request.get("result_identity") or {}).get("plan_review_disposition_digest")
        if pinned:
            payload["plan_review_disposition_digest"] = pinned
        else:
            payload.pop("plan_review_disposition_digest")
    for key in ("ref", "sha256", "byte_count"):
        if key in old_source:
            new_source[key] = old_source[key]
        else:
            new_source.pop(key, None)
    detail_ids = {"plan-execution-usage", "plan-execution-verification"}
    old_details = [source for source in manifest.get("sources", []) if source.get("source_id") in detail_ids]
    old_usage = [source for source in old_details if source["source_id"] == "plan-execution-usage"]
    if len(old_usage) > 1:
        raise ValueError("ambiguous pinned plan execution usage")
    if old_usage:
        detail = old.get("usage_observations") or {}
        if (detail.get("detail_ref") != old_usage[0].get("ref")
                or detail.get("detail_sha256") != old_usage[0].get("sha256")):
            raise ValueError("pinned plan execution usage differs from review")
        hydrate_sidecar_ref(runtime.state_dir, dict(old_usage[0]))
    # Both details are issued together immediately before the review. Their
    # order is part of the manifest digest, including for pre-upgrade attempts.
    refs = payload["input_refs"]
    refs[:] = [source for source in refs if not isinstance(source, Mapping)
               or source.get("source_id") not in detail_ids]
    position = next(i for i, source in enumerate(refs) if source is new_source)
    refs[position:position] = [dict(source) for source in old_details]
