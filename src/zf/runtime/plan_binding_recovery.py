"""Repair old Task bindings after a generation was admitted but never dispatched."""

from copy import deepcopy
from typing import Any

from zf.core.events.model import ZfEvent

from zf.core.task.store import TaskStore
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.plan_output_lineage import assert_package_task_map_matches
from zf.runtime.task_contract_authority import TaskContractAuthorityService


def refresh_plan_bindings(
    service: Any, source: ZfEvent, payload: dict[str, Any], requested: ZfEvent,
) -> dict[str, Any]:
    if source.type != "task_map.ready" or source.origin != "kernel" or not str(payload.get("approval_ref") or "").strip():
        raise ValueError("binding repair requires a kernel Plan source and approval_ref")
    events = service.writer.event_log.read_all()
    admitted = next((e for e in reversed(events)
                     if e.type == "task.pipeline.generation.admitted" and e.origin == "kernel"
                     and e.payload.get("trigger_event_id") == source.id), None)
    if admitted is None:
        raise ValueError("binding repair requires an admitted Task Pipeline generation")
    generation = admitted.payload
    run_id = generation["workflow_run_id"]
    current = next(e for e in reversed(events) if e.type == "task.pipeline.generation.admitted"
                   and e.origin == "kernel" and e.payload.get("workflow_run_id") == run_id)
    if current.id != admitted.id:
        raise ValueError("binding repair cannot modify a superseded generation")
    if any(e.type == "task.pipeline.stage.dispatched"
           and e.payload.get("workflow_run_id") == run_id
           and e.payload.get("task_map_generation") == generation["task_map_generation"] for e in events):
        raise ValueError("binding repair cannot alter an already dispatched generation")
    package = hydrate_plan_artifact_package(service.state_dir, {
        "ref": generation["plan_artifact_package_ref"],
        "sha256": generation["plan_artifact_package_digest"],
    })
    if package["workflow_run_id"] != run_id or package["task_map_generation"] != generation["task_map_generation"]:
        raise ValueError("admitted package identity does not match the generation")
    assert_package_task_map_matches(service.state_dir, service.project_root or service.state_dir.parent,
                                    package, generation)
    store = TaskStore(service.state_dir / "kanban.json")
    keys = ("plan_artifact_package_id", "plan_artifact_package_ref",
            "plan_artifact_package_digest", "task_map_generation")
    repairs = []
    for task_id in generation["task_ids"]:
        task = store.get(task_id)
        if task is None or task.status not in {"backlog", "in_progress", "blocked", "failed"}:
            raise ValueError(f"binding repair requires an unfinished Task: {task_id}")
        evidence = task.contract.evidence_contract
        refs = evidence.get("source_refs", {})
        if evidence.get("workflow_run_id") != run_id or refs.get("task_map_ref") != generation["task_map_ref"]:
            raise ValueError(f"binding repair Task source differs: {task_id}")
        if any(refs.get(key) != generation[key] for key in keys):
            contract = deepcopy(task.contract)
            contract.evidence_contract["source_refs"].update({key: generation[key] for key in keys})
            repairs.append((task, contract))
    authority = TaskContractAuthorityService(task_store=store, event_writer=service.writer,
                                            state_dir=service.state_dir)
    for task, contract in repairs:
        authority.replace(task, contract=contract, source="plan_package_binding_recovery",
                          actor=service.actor, causation_id=requested.id, correlation_id=run_id,
                          audit_payload={"approval_ref": payload["approval_ref"],
                                         "generation_admitted_event_id": admitted.id,
                                         "source_event_id": source.id})
    return {**source.payload, **{key: generation[key] for key in keys},
            "plan_binding_recovery": {"approval_ref": payload["approval_ref"],
                                      "generation_admitted_event_id": admitted.id}}
