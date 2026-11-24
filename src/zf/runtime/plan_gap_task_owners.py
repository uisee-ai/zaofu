"""Rebind structured Plan-port owners for an exact copied-contract Task replacement."""

from pathlib import Path
from typing import Any, Mapping

from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.plan_artifact_package_ports import PlanArtifactPackageError
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


_OWNER_ROWS = {
    "capability_matrix": ("capabilities",),
    "acceptance_matrix": ("acceptance", "acceptances"),
    "test_matrix": ("tests", "commands"),
    "real_e2e_matrix": ("rows",),
    "source_index": ("tasks", "traceability"),
    "source_inventory": ("sources",),
    "execution_spec": ("tasks",),
    "task_execution_input": ("tasks",),
}


def rebind_gap_task_owners(
    *, state_dir: Path, payload: Mapping[str, Any], task_map: Mapping[str, Any],
    prior: Mapping[str, Any] | None, flow_kind: str, run_contract_digest: str,
    explicit: dict, inherited: list[dict], source_event_id: str,
) -> None:
    summary = payload.get("rework_summary") or {}
    if (payload.get("resume_scope") != "gap_tasks_only" or not isinstance(summary, Mapping)
            or summary.get("preserve_task_rows") is not True):
        return
    from zf.runtime.module_gap_plan import build_gap_task_map_amend
    from zf.runtime.module_gap_replacement import select_successor_base_commit

    if (not prior or prior["workflow_run_id"] != payload.get("workflow_run_id")
            or prior["flow_kind"] != flow_kind or prior["run_contract_digest"] != run_contract_digest
            or prior["task_map_generation"] != payload.get("supersedes_task_map_generation")):
        raise PlanArtifactPackageError("copied-contract replacement lacks its admitted parent generation")
    parent = next(p for p in [*prior["produced"], *prior["inherited"]] if p["logical_name"] == "task_map")
    if parent["ref"] != payload.get("amend_of"):
        raise PlanArtifactPackageError("copied-contract replacement does not bind the admitted parent Task Map")
    gaps = summary.get("gap_tasks")
    if not isinstance(gaps, list) or not gaps:
        raise PlanArtifactPackageError("copied-contract replacement lacks declared gap Tasks")
    expected = build_gap_task_map_amend(
        hydrate_sidecar_ref(state_dir, parent).payload, gap_tasks=gaps,
        supersedes_task_map_ref=parent["ref"], successor_base_commit=select_successor_base_commit({**payload, **summary}),
        gap_plan_ref=str(summary.get("gap_plan_ref") or ""),
        supersedes_task_ids=summary.get("supersedes_task_ids"), preserve_task_rows=True,
    )
    if expected != task_map:
        raise PlanArtifactPackageError("replacement Task Map differs from its copied-contract amendment")
    owners = {gap["supersedes_task_ids"][0]: gap["task_id"] for gap in gaps}
    if len(owners) != len(gaps) or len(set(owners.values())) != len(owners):
        raise PlanArtifactPackageError("copied-contract replacement requires one successor per predecessor")
    for port in list(inherited):
        name = port["logical_name"]
        if name not in _OWNER_ROWS:
            continue
        body = hydrate_sidecar_ref(state_dir, port).payload
        if not isinstance(body, dict):
            continue
        changed = False
        for collection in _OWNER_ROWS[name]:
            rows = body.get(collection)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key in ("task_id", "owning_task_id", "producer_task_id"):
                    if isinstance(row.get(key), str) and row[key] in owners:
                        row[key] = owners[row[key]]
                        changed = True
                for key in ("task_ids", "owning_task_ids"):
                    ids = row.get(key)
                    if isinstance(ids, list) and all(isinstance(item, str) for item in ids):
                        rewritten = [owners.get(item, item) for item in ids]
                        if rewritten != ids:
                            row[key] = rewritten
                            changed = True
        if changed:
            descriptor = write_immutable_json_sidecar(
                state_dir, body, root="plan-gap-task-owners", kind=name,
                schema_version=port["schema_version"], created_by="plan-artifact-package",
                source_event_id=source_event_id,
            )
            explicit[name] = {**port, "ref": descriptor["ref"], "sha256": descriptor["sha256"],
                              "rebound_from_ref": port["ref"], "rebound_from_digest": port["sha256"]}
            inherited.remove(port)
