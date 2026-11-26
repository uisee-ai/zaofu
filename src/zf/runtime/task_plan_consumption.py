"""Validate task-local Plan port selections against the pinned Task Map."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def selected_plan_ports(
    *, state_dir: Any, task_id: str, profile: str, evidence: Mapping[str, Any],
    ports: Mapping[str, Mapping[str, Any]], required: list[str],
) -> list[str] | None:
    """Return an approved task selection; absent policy preserves legacy reads.

    Semantic completeness belongs to Planner/Critic. This check binds an
    explicit selection to the immutable Package's exact Task Map entry.
    """
    policy = evidence.get("plan_input_selection")
    if policy is None:
        return None
    if profile not in {"implementation", "task-verify"}:
        return None
    if not isinstance(policy, Mapping) or set(policy) != {
        "schema_version", "implementation", "task-verify",
    } or policy.get("schema_version") != "task-plan-input-selection.v1":
        raise ValueError("invalid task plan input selection")
    required_full = {"task_map", "execution_spec", "acceptance_matrix", "test_matrix", "real_e2e_matrix"}
    if not required_full.issubset(required):
        raise ValueError("task selection requires the full canonical matrices in the package")
    task_map = ports.get("task_map")
    if not task_map:
        raise ValueError("task plan input selection requires pinned task_map")
    body = hydrate_sidecar_ref(state_dir, {
        "ref": task_map.get("ref"), "sha256": task_map.get("sha256"),
    }).payload
    items = body.get("tasks") if isinstance(body, Mapping) else None
    matches = [item for item in items or [] if isinstance(item, Mapping)
               and str(item.get("task_id") or item.get("id") or "") == task_id]
    if len(matches) != 1:
        raise ValueError("task selection must match exactly one pinned task")
    source = matches[0].get("evidence_contract")
    if not isinstance(source, Mapping) or source.get("plan_input_selection") != policy:
        raise ValueError("task selection differs from pinned task_map")
    for stage in ("implementation", "task-verify"):
        value = policy.get(stage)
        if not isinstance(value, list) or not value or any(
            not isinstance(name, str) or not name.strip() or name != name.strip()
            for name in value
        ):
            raise ValueError(f"{stage} input selection must be a non-empty port list")
        if len(set(value)) != len(value) or any(name not in required for name in value):
            raise ValueError(f"{stage} input selection has duplicate or unbound ports")
        if not {"execution_spec", "task_execution_input"}.intersection(value):
            raise ValueError(f"{stage} input selection requires a task-scoped execution input")
    return list(policy[profile])


def task_context_route_sources(
    routes: list[dict[str, Any]], ports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """An OA route names an admitted file; its task partition still governs the alias."""
    from zf.runtime.artifact_query.extractors import _intersect_access_scopes

    for route in routes:
        for port in ports:
            if (port.get("kind") != "task_scoped_plan_input" or
                    (port["ref"], port["sha256"]) != (route["ref"], route["sha256"])):
                continue
            permitted = port["allowed_paths"]
            if "$" in permitted:
                continue
            grants = route.get("allowed_paths") or ["$"]
            selected = [path for path in permitted if "$" in grants or path in grants]
            if not selected:
                raise ValueError("OA route and its pinned task partition have no common authorized path")
            route["allowed_paths"] = selected
            route["access_scope"] = _intersect_access_scopes(route.get("access_scope"), port.get("access_scope"))
    return routes


def replayed_task_context_routes(
    state_dir: Path, request: Mapping[str, Any] | None, *, attempt_id: str,
    dispatch_id: str, workflow_run_id: str, task_id: str, currentness: Mapping[str, Any],
) -> list[dict[str, Any]] | None:
    """Reuse only the OA aliases from this attempt's immutable request, including an empty list."""
    if not request or request.get("active_attempt_id") != attempt_id or not request.get("source_manifest_digest"):
        return None
    from zf.runtime.artifact_read_ledger import _safe_component

    digest = request["source_manifest_digest"]
    manifest = hydrate_sidecar_ref(state_dir, {
        "ref": f"artifacts/attempts/{_safe_component(attempt_id)}/source-manifests/{digest}.json",
        "sha256": digest,
    }).payload
    expected = {"workflow_run_id": workflow_run_id, "task_id": task_id,
                "attempt_id": attempt_id, "dispatch_id": dispatch_id,
                **{key: currentness.get(key) for key in
                   ("task_map_generation", "plan_artifact_package_ref", "plan_artifact_package_digest")}}
    if any(str(manifest.get(key) or "") != str(value or "") for key, value in expected.items()):
        raise ValueError("OA route replay does not match the signed task attempt")
    ids = manifest.get("orchestrator_context_route_source_ids", [])
    rows = [row for row in manifest["sources"] if row["source_id"] in ids]
    if [row["source_id"] for row in rows] != ids:
        raise ValueError("signed OA route descriptors do not match their manifest inventory")
    return rows
