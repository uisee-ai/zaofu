"""Owner-authorized redrive of a Plan output carrying its input identity."""

from dataclasses import asdict
from typing import Any

from zf.core.events.model import ZfEvent
from zf.core.task.store import TaskStore
from zf.runtime.plan_artifact_package import hydrate_plan_artifact_package
from zf.runtime.plan_artifact_package_runtime import plan_output_payload
from zf.runtime.plan_output_lineage import package_task_map
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_map import load_task_map, resolve_artifact_file


def repair_plan_output(service: Any, source: ZfEvent, payload: dict, requested: ZfEvent) -> dict:
    """Repair bindings only; normal Plan admission and approval still follow."""
    if source.type != "task_map.ready" or source.origin != "kernel":
        raise ValueError("Plan output recovery requires a kernel task_map.ready")
    approval = str(payload.get("approval_ref") or "").strip()
    if not approval:
        raise ValueError("Plan output recovery requires approval_ref")
    base = dict(source.payload)
    package = hydrate_plan_artifact_package(service.state_dir, {
        "ref": base.get("plan_artifact_package_ref"),
        "sha256": base.get("plan_artifact_package_digest"),
    })
    run_id = str(base.get("workflow_run_id") or source.correlation_id or "")
    if not run_id or package.get("workflow_run_id") != run_id:
        raise ValueError("Plan output recovery requires the same workflow Run")
    task_map = load_task_map(resolve_artifact_file(
        base.get("task_map_ref", ""), state_dir=service.state_dir,
        project_root=service.project_root or service.state_dir.parent))
    if task_map == package_task_map(service.state_dir, package):
        raise ValueError("Plan output already matches its package; no lineage repair needed")
    retained = payload.get("retain_completed_task_ids", [])
    if not isinstance(retained, list) or any(not isinstance(t, str) or not t for t in retained):
        raise ValueError("retain_completed_task_ids must be a list of nonempty task ids")
    if len(set(retained)) != len(retained):
        raise ValueError("retain_completed_task_ids contains duplicates")
    task_ids = [item["task_id"] for item in task_map["tasks"]]
    if not set(retained).issubset(task_ids) or (retained and set(retained) == set(task_ids)):
        raise ValueError("retained tasks must be a proper subset of the complete Task Map")
    store = TaskStore(service.state_dir / "kanban.json")
    events = service.writer.event_log.read_all()
    # Validate every task before touching canonical state. CAS below fences
    # concurrent authority changes; a partially applied repair is retryable.
    proofs = [_retention_proof(service, store, events, tid, base, run_id) for tid in retained]
    repaired = plan_output_payload({"task_map_ref": ""}, base)
    repaired["plan_output_recovery"] = {
        "source_event_id": source.id, "approval_ref": approval,
        "prior_package_ref": base["plan_artifact_package_ref"],
        "prior_package_digest": base["plan_artifact_package_digest"],
        "retained_completion_events": {current.id: done.id for current, _, done in proofs},
    }
    if retained:
        repaired["task_ids"] = [tid for tid in task_ids if tid not in retained]
        repaired["resume_scope"] = "failed_children_and_downstream"
    for current, archived, done in proofs:
        if current.status != "done":
            _, applied = store.compare_and_update_fields(
                current.id, expected_authority_revision=current.contract_authority_revision,
                expected_status="backlog", updates={
                    "status": "done", "completed_at": archived.completed_at,
                    "evidence": archived.evidence, "assigned_to": None,
                })
            if not applied:
                raise ValueError(f"task changed during Plan recovery: {current.id}")
        service.writer.append(ZfEvent(
            type="task.status_changed", origin="kernel", actor=service.actor,
            task_id=current.id, causation_id=requested.id, correlation_id=run_id,
            payload={"from": current.status, "to": "done", "status": "done",
                     "source": "plan_output_recovery", "approval_ref": approval,
                     "source_event_id": source.id, "completion_event_id": done.id,
                     "contract_authority_revision": current.contract_authority_revision},
        ))
    return repaired


def _retention_proof(service: Any, store: TaskStore, events: list[ZfEvent],
                     task_id: str, payload: dict, run_id: str) -> tuple:
    current = store.get(task_id)
    if current is None or current.status not in {"backlog", "done"}:
        raise ValueError(f"retained task must be inactive: {task_id}")
    reopened = next((e for e in reversed(events) if e.type == "task.contract.revision.applied"
                     and e.origin == "kernel" and e.task_id == task_id
                     and e.payload.get("source") == "refactor_replan_adoption"
                     and e.payload.get("reopened_from_terminal") is True
                     and e.payload.get("new_task_map_ref") == payload.get("task_map_ref")
                     and e.payload.get("contract_authority_revision")
                     == current.contract_authority_revision), None)
    if reopened is None:
        raise ValueError(f"no matching completed-task reopening receipt: {task_id}")
    cut = events.index(reopened)
    if any((e.task_id or e.payload.get("task_id")) == task_id
           and e.type in {"task.dispatched", "fanout.child.dispatched", "task.pipeline.stage.dispatched"}
           for e in events[cut + 1:]):
        raise ValueError(f"task has a later execution attempt: {task_id}")
    archived = next((t for t in reversed(store.list_all_with_archive())
                     if t.id == task_id and t.status == "done" and t.contract_authority_revision
                     == reopened.payload.get("previous_authority_revision")), None)
    done = next((e for e in reversed(events[:cut]) if e.type == "task.done"
                 and e.origin == "kernel" and e.task_id == task_id
                 and e.payload.get("workflow_run_id") == run_id), None)
    if archived is None or done is None or not done.payload.get("integration_receipt_ref"):
        raise ValueError(f"retained task has no archived integrated completion: {task_id}")
    receipt = hydrate_sidecar_ref(service.state_dir, done.payload["integration_receipt_ref"]).payload
    if (receipt.get("task_id") != task_id or receipt.get("workflow_run_id") != run_id
            or receipt.get("status") != "integrated"
            or receipt.get("new_candidate_head") != done.payload.get("candidate_head")):
        raise ValueError(f"retained task integration receipt identity differs: {task_id}")
    if _acceptance_contract(archived.contract) != _acceptance_contract(current.contract):
        raise ValueError(f"retained task acceptance or execution contract changed: {task_id}")
    if current.status == "done" and current.completed_at != archived.completed_at:
        raise ValueError(f"retained task completion identity changed: {task_id}")
    return current, archived, done


def _acceptance_contract(contract: Any) -> dict:
    body = asdict(contract)
    # These are source/navigation bindings, reviewed explicitly by the owner.
    # Requirements, scope, verification, ownership and evidence rules stay exact.
    for key in ("source_ref", "source_index_ref", "product_contract_ref", "goal_claim_ids"):
        body.pop(key, None)
    evidence = body.get("evidence_contract", {})
    for key in ("source_refs", "plan_input_selection"):
        evidence.pop(key, None)
    return body
