"""Resolve gap amendments against the package that admitted their generation."""

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.plan_artifact_package import (
    PlanArtifactPackageError,
    admitted_plan_artifact_package_for_generation,
    hydrate_plan_artifact_package,
    reduce_plan_artifact_packages,
)
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def gap_plan_base_ref(
    *, state_dir: Path, events: list, payload: Mapping[str, Any],
    fallback_ref: str, required: bool,
) -> str:
    run_id = str(payload.get("workflow_run_id") or "")
    generation = str(payload.get("task_map_generation") or "")
    package = admitted_plan_artifact_package_for_generation(
        reduce_plan_artifact_packages(events, workflow_run_id=run_id),
        task_map_generation=generation,
    ) if run_id and generation else {}
    if not package:
        if required:
            raise PlanArtifactPackageError("gap amendment has no admitted baseline generation")
        return fallback_ref
    for key in ("id", "ref", "digest"):
        claimed = payload.get("plan_artifact_package_" + key)
        if claimed and claimed != package.get("package_" + key):
            raise PlanArtifactPackageError("gap amendment package binding mismatch: " + key)
    body = hydrate_plan_artifact_package(state_dir, {
        "ref": package["package_ref"], "sha256": package["package_digest"],
    })
    port = next((p for p in [*body["produced"], *body["inherited"]]
                 if p.get("logical_name") == "task_map"), None)
    if port is None:
        raise PlanArtifactPackageError("gap amendment baseline has no task_map port")
    hydrate_sidecar_ref(state_dir, port)
    return str(port["ref"])


def gap_plan_amend_baseline(
    *, state_dir: Path, events: list, payload: Mapping[str, Any],
    fallback_ref: str, required: bool, gap_tasks: list[dict],
) -> tuple[str, str]:
    """Rebase a task-local delta only when its pinned inputs remain unchanged.

    Reader authority stays on the source generation. The full-map amendment
    inherits current admitted work so an independent task's changes cannot vanish.
    """
    source_ref = gap_plan_base_ref(state_dir=state_dir, events=events,
        payload=payload, fallback_ref=fallback_ref, required=required)
    generation = str(payload.get("task_map_generation") or "")
    reduced = reduce_plan_artifact_packages(events,
        workflow_run_id=str(payload.get("workflow_run_id") or ""))
    current = reduced.get("current") or {}
    if not current or current.get("task_map_generation") == generation:
        return source_ref, generation
    source = admitted_plan_artifact_package_for_generation(reduced, task_map_generation=generation)
    if not source:
        raise PlanArtifactPackageError("gap amendment source generation is not admitted")
    def hydrate(row):
        return hydrate_plan_artifact_package(state_dir,
            {"ref": row["package_ref"], "sha256": row["package_digest"]})
    old, new = hydrate(source), hydrate(current)
    # Prove immutable lineage, including intermediate packages repaired before admission.
    cursor, seen = new, set()
    ancestry = [new]
    while cursor.get("supersedes_package_digest") != source["package_digest"]:
        digest = str(cursor.get("supersedes_package_digest") or "")
        ref = str(cursor.get("supersedes_package_ref") or "")
        if not digest or not ref or digest in seen:
            raise PlanArtifactPackageError("gap amendment current package is not a descendant")
        seen.add(digest)
        cursor = hydrate_plan_artifact_package(state_dir, {"ref": ref, "sha256": digest})
        ancestry.append(cursor)
        if cursor.get("workflow_run_id") != old.get("workflow_run_id"):
            raise PlanArtifactPackageError("gap amendment package lineage crosses runs")
    if old.get("run_contract_digest") != new.get("run_contract_digest"):
        raise PlanArtifactPackageError("gap amendment run contract changed")
    def ports(body):
        return {row["logical_name"]: row for row in [*body["produced"], *body["inherited"]]}
    previous, latest = ports(old), ports(new)
    before = hydrate_sidecar_ref(state_dir, previous["task_map"]).payload
    after = hydrate_sidecar_ref(state_dir, latest["task_map"]).payload
    for key in ("shared_conventions", "owner_decision_items"):
        if before.get(key) != after.get(key):
            raise PlanArtifactPackageError("gap amendment shared input changed: " + key)
    targets = set()
    for gap in gap_tasks:
        ids = gap.get("supersedes_task_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(v, str) or not v for v in ids):
            raise PlanArtifactPackageError("cross-generation gap must explicitly replace existing tasks")
        targets.update(ids)
    old_tasks = {row["task_id"]: row for row in before["tasks"]}
    new_tasks = {row["task_id"]: row for row in after["tasks"]}
    maps = None
    selected = {"requirement_spec", "execution_spec"}
    for task_id in targets:
        task = old_tasks.get(task_id)
        if task is None or task_id not in new_tasks:
            raise PlanArtifactPackageError("gap amendment target task changed: " + task_id)
        if task != new_tasks[task_id]:
            from zf.runtime.gap_dependency_continuity import task_has_only_replayed_dependency_changes

            if maps is None:
                maps = [before, *[hydrate_sidecar_ref(state_dir, ports(body)["task_map"]).payload
                                  for body in reversed(ancestry)]]
            if not task_has_only_replayed_dependency_changes(task_id, maps):
                raise PlanArtifactPackageError("gap amendment target task changed: " + task_id)
        selection = (task.get("evidence_contract") or {}).get("plan_input_selection") or {}
        if not any(isinstance(values, list) and values for values in selection.values()):
            raise PlanArtifactPackageError("cross-generation gap needs explicit input selection: " + task_id)
        for values in selection.values():
            if isinstance(values, list):
                selected.update(v for v in values if isinstance(v, str))
    for name in selected:
        a, b = previous.get(name), latest.get(name)
        if a is None and b is None:
            continue
        if a is None or b is None or a.get("sha256") != b.get("sha256"):
            raise PlanArtifactPackageError("gap amendment selected input changed: " + name)
        hydrate_sidecar_ref(state_dir, b)
    return str(latest["task_map"]["ref"]), str(current["task_map_generation"])
