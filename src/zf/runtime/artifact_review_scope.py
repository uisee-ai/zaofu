"""Apply owner-pinned, version-specific selector mappings only to new review requests."""

from zf.runtime.artifact_read_snapshot import open_selection
from zf.runtime.call_result_envelope import canonical_json_sha256
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation

PROFILES = {
    "candidate-verify",
    "integration-acceptance-review",
    "thin-judge-goal-closure",
}


def select_review_inputs(
    runtime, operation_id, profile, payload, manifest, required, explicit
):
    payload.pop("review_scope_ref", None)
    if profile not in PROFILES:
        return required
    existing = load_workflow_operation(runtime.event_log, operation_id)
    if existing and existing.get("request_ref"):
        request = hydrate_sidecar_ref(
            runtime.state_dir, existing["request_ref"]
        ).payload["request"]
        if request.get("review_scope_ref"):
            payload["review_scope_ref"] = request["review_scope_ref"]
            return request["required_reads"]
        return required
    meta = getattr(getattr(runtime.config, "workflow", None), "flow_metadata", {}) or {}
    ref = (meta.get("approved_review_scopes") or {}).get(profile)
    if not ref:
        return required
    try:
        certificate = hydrate_sidecar_ref(runtime.state_dir, ref).payload
    except (OSError, ValueError, TypeError, KeyError):
        return required
    if not isinstance(certificate, dict):
        return required
    binding = certificate.get("binding", {})
    actor = payload.get("role_instance", "")
    if (
        certificate.get("schema_version") != "approved-review-scope.v1"
        or not certificate.get("approved_by")
        or not certificate.get("revision")
        or binding.get("output_profile_id") != profile
        or not all(
            binding.get(k) and binding[k] == payload.get(k)
            for k in ("workflow_run_id", "target_commit")
        )
        or binding.get("baseline_sha256") != canonical_json_sha256(required)
    ):
        return required
    if not isinstance(explicit, list):
        explicit = []
    replacements = certificate.get("replacements", [])
    if not isinstance(replacements, list) or not replacements:
        return required
    result = [dict(r) for r in required]
    for change in replacements:
        if (
            not isinstance(change, dict)
            or not isinstance(change.get("obligations"), list)
            or not change["obligations"]
            or not all(isinstance(o, str) and o.strip() for o in change["obligations"])
            or not isinstance(change.get("selectors"), list)
            or not change["selectors"]
        ):
            return required
        original = change.get("original", {})
        fields = ("source_id", "artifact_id", "artifact_sha256", "json_path")
        matches = [
            r
            for r in result
            if all(
                r.get(k, "$" if k == "json_path" else "") == original.get(k)
                for k in fields
            )
        ]
        # Explicit requirements and structural handoff/target bindings remain independently applicable.
        if (
            len(matches) != 1
            or original.get("source_id")
            in {"contract", "target", "task-handoff", "milestone-handoff"}
            or any(
                all(
                    r.get(k, "$" if k == "json_path" else "") == original.get(k)
                    for k in ("source_id", "artifact_id", "json_path")
                )
                for r in explicit or []
            )
        ):
            return required
        row = matches[0]
        selected = []
        for selector in change["selectors"]:
            if (
                not isinstance(selector, str)
                or not selector.startswith("$.")
                or original.get("json_path") != "$"
            ):
                return required
            replacement = {**row, "json_path": selector}
            try:
                text = open_selection(
                    runtime.state_dir,
                    manifest=manifest,
                    row=replacement,
                    identity={
                        "actor": actor,
                        "role": actor,
                        "purpose": manifest.get("read_purpose", ""),
                    },
                )
                if text.byte_count < row.get("min_returned_bytes", 1):
                    return required
            except (OSError, ValueError, KeyError):
                return required
            if replacement not in selected:
                selected.append(replacement)
        i = result.index(row)
        result[i : i + 1] = selected
    payload["review_scope_ref"] = dict(ref)
    return result
