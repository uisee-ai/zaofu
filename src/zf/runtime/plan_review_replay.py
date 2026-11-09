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
    # Usage is observational, while all candidate/policy/graph facts remain
    # request identity. A real change must still reach the divergence guard.
    if ({key: value for key, value in old.items() if key != "usage_observations"}
            != {key: value for key, value in new.items() if key != "usage_observations"}):
        return
    for key in ("ref", "sha256", "byte_count"):
        if key in old_source:
            new_source[key] = old_source[key]
        else:
            new_source.pop(key, None)
    old_usage = [source for source in manifest.get("sources", [])
                 if source.get("source_id") == "plan-execution-usage"]
    if len(old_usage) > 1:
        raise ValueError("ambiguous pinned plan execution usage")
    if old_usage:
        detail = old.get("usage_observations") or {}
        if (detail.get("detail_ref") != old_usage[0].get("ref")
                or detail.get("detail_sha256") != old_usage[0].get("sha256")):
            raise ValueError("pinned plan execution usage differs from review")
        hydrate_sidecar_ref(runtime.state_dir, dict(old_usage[0]))
    # Usage is a single frozen observation: pin both its summary and its
    # optional detail source, including upgrades of requests created before
    # the detail was separated from the required review body.
    refs = payload.get("input_refs", [])
    refs[:] = [source for source in refs if not isinstance(source, Mapping)
               or source.get("source_id") != "plan-execution-usage"]
    if old_usage:
        position = next(i for i, source in enumerate(refs)
                        if isinstance(source, Mapping) and source.get("source_id") == "plan-execution-review")
        refs.insert(position, dict(old_usage[0]))
