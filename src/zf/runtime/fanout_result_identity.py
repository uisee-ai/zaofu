"""Bind blocking writer results to the immutable fanout child identity."""

from __future__ import annotations

from typing import Any, Mapping


KERNEL_BOUND_WRITER_RESULT_FIELDS = (
    "task_id",
    "role_instance",
    "fanout_id",
    "stage_id",
    "child_id",
    "run_id",
    "workflow_run_id",
    "contract_authority_revision",
    "execution_owner",
    "workflow_request_id",
    "workflow_request_revision",
    "origin_binding_digest",
    "contract_revision",
    "task_map_generation",
    "base_commit",
    "task_ref",
    "contract_snapshot_ref",
    "contract_snapshot_digest",
    "plan_artifact_package_id",
    "plan_artifact_package_ref",
    "plan_artifact_package_digest",
    "operation_id",
    "parent_operation_id",
    "request_hash",
    "attempt_id",
    "result_protocol_mode",
    "attempt_source_manifest_ref",
    "attempt_source_manifest_digest",
    "attempt_source_manifest",
    "input_consumption_policy_ref",
    "input_consumption_policy",
    "input_consumption_policy_digest",
    "required_reads",
)


def bind_blocking_writer_result_identity(
    payload: Mapping[str, Any],
    child: Mapping[str, Any],
    *,
    pinned_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a result whose request identity comes from the Kernel child."""

    result = dict(payload)
    child_payload = (
        child.get("payload")
        if isinstance(child.get("payload"), Mapping)
        else {}
    )
    canonical = {**child_payload, **child}
    if pinned_identity:
        canonical.update(pinned_identity)
    protocol_mode = str(
        canonical.get("result_protocol_mode")
        or result.get("result_protocol_mode")
        or ""
    ).strip()
    if protocol_mode != "blocking":
        return result

    for key in KERNEL_BOUND_WRITER_RESULT_FIELDS:
        value = canonical.get(key)
        if value not in (None, ""):
            if pinned_identity and key in pinned_identity:
                # Preserve explicit conflicts for the authority gate. A queue
                # snapshot must never replace the immutable dispatch identity.
                result.setdefault(key, value)
            else:
                result[key] = value

    raw_self_check = result.get("impl_self_check")
    if isinstance(raw_self_check, Mapping):
        self_check = dict(raw_self_check)
        self_check_identity = {
            "workflow_run_id": canonical.get("workflow_run_id"),
            "task_id": canonical.get("task_id"),
            "attempt_id": canonical.get("attempt_id"),
            "contract_authority_revision": canonical.get(
                "contract_authority_revision"
            ),
            "contract_revision": canonical.get("contract_revision"),
            "task_map_generation": canonical.get("task_map_generation"),
            "contract_snapshot_ref": canonical.get("contract_snapshot_ref"),
            "contract_snapshot_digest": canonical.get("contract_snapshot_digest"),
            "source_commit": result.get("source_commit"),
            "target_commit": (
                result.get("target_commit") or result.get("source_commit")
            ),
        }
        for key, value in self_check_identity.items():
            if value not in (None, ""):
                if pinned_identity:
                    self_check.setdefault(key, value)
                else:
                    self_check[key] = value
        result["impl_self_check"] = self_check
    return result


__all__ = [
    "KERNEL_BOUND_WRITER_RESULT_FIELDS",
    "bind_blocking_writer_result_identity",
]
