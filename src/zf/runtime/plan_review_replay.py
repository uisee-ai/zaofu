"""Keep incidental usage observations stable within one prepared Plan review."""

from typing import Any, Mapping

from zf.runtime.artifact_read_ledger import _safe_component
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


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
