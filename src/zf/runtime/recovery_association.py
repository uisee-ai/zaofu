"""Admit evidence-bound associations without merging task authority or verdicts."""

import hashlib
import json
from pathlib import Path
from typing import Any

from zf.core.events import EventWriter, ZfEvent
from zf.core.state.locks import locked_path
from zf.runtime.pause_lifecycle import is_dispatch_paused
from zf.runtime.recovery_case import build_recovery_case_projection
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.task_pipeline_failure_accounting import is_semantic_failure
from zf.runtime.workflow_operation import reduce_workflow_operations


ADMITTED = "recovery.association.admitted"


def admit_association(
    *, state_dir: Path, writer: EventWriter, config: Any, actor: str,
    proposal: dict[str, Any], apply: bool = False,
) -> dict[str, Any]:
    from zf.runtime.orchestrator_agent_role import configured_orchestrator_role_from_config
    from zf.runtime.run_manager_resident import build_resident_run_manager_role

    roles = [configured_orchestrator_role_from_config(config), build_resident_run_manager_role(config)]
    if actor not in {r.instance_id or r.name for r in roles if r is not None}:
        raise ValueError("association requires a configured decision owner")
    with locked_path(state_dir / "locks" / "recovery-association"):
        events = writer.event_log.read_all()
        if is_dispatch_paused(state_dir, events=events):
            raise ValueError("dispatch_paused")
        kind = proposal.get("kind")
        if kind not in {"shared_resource", "problem_continuation"}:
            raise ValueError("unsupported association kind")
        by_id = {row["recovery_case_id"]: row for row in build_recovery_case_projection(events)["cases"]}
        member_ids = proposal.get("member_case_ids") or []
        if not isinstance(member_ids, list) or len(set(member_ids)) < 2 or any(key not in by_id for key in member_ids):
            raise ValueError("association requires at least two existing cases")
        members = [by_id[key] for key in member_ids]
        run_id = str(proposal.get("workflow_run_id") or "")
        if not run_id or any(row.get("workflow_run_id") != run_id for row in members):
            raise ValueError("cross-run association requires separate authorization")
        refs = proposal.get("evidence_event_ids") or []
        evidence = {e.id: e for e in events}
        if not isinstance(refs, list) or not refs or any(ref not in evidence for ref in refs):
            raise ValueError("association requires existing evidence events")
        if not str(proposal.get("rationale") or "").strip():
            raise ValueError("association requires the agent's rationale")
        for ref in refs:
            event = evidence[ref]
            observed_run = event.payload.get("workflow_run_id") or event.payload.get("run_id") or event.correlation_id
            if observed_run != run_id:
                raise ValueError("association evidence is outside the authorized run")
        if kind == "shared_resource":
            for key in ("resource_id", "resource_version", "repair_step_id"):
                if not str(proposal.get(key) or "").strip():
                    raise ValueError(f"shared repair requires {key}")
            if proposal.get("owner_case_id") not in member_ids:
                raise ValueError("shared repair owner must be a member case")
        else:
            target = proposal.get("target_case_id")
            if target not in member_ids:
                raise ValueError("continuation target must be a member case")
            operations = reduce_workflow_operations(events)
            operation_ids = proposal.get("failure_operation_ids") or []
            if not operation_ids or any(key not in operations for key in operation_ids):
                raise ValueError("continuation requires admitted failure operations")
            from zf.runtime.task_pipeline_execution_status import normalize_task_pipeline_operation_semantics
            rows = normalize_task_pipeline_operation_semantics(state_dir, [operations[key] for key in operation_ids])
            source_tasks = {row["task_id"] for row in members if row["recovery_case_id"] != target}
            if any(not is_semantic_failure(row) or row.get("workflow_run_id") != run_id
                   or row.get("task_id") not in source_tasks for row in rows):
                raise ValueError("failure lineage must reference admitted semantic failures of source cases")
            for row in rows:
                if not any(member["recovery_case_id"] != target and member["task_id"] == row.get("task_id")
                           and str(member.get("task_map_generation") or "") == str(row.get("task_map_generation") or "")
                           for member in members):
                    raise ValueError("failure lineage generation does not match its source case")
        body = {**proposal, "decision_owner": actor, "members": [
            {key: row.get(key, "") for key in ("recovery_case_id", "task_id", "workflow_run_id", "task_map_generation", "target_commit")}
            for row in members
        ]}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        association_id = f"association-{digest[:24]}"
        if not apply:
            return {"status": "preview", "association_id": association_id, "proposal": body}
        existing = next((e for e in events if e.type == ADMITTED and e.payload.get("association_id") == association_id), None)
        if existing:
            return {"status": "admitted", "association_id": association_id, "event_id": existing.id}
        ref = write_sidecar_json(state_dir, f"diagnostics/recovery-associations/{association_id}.json", body,
            kind="recovery-association", schema_version="recovery-association.v1", created_by=actor, required=True)
        event = writer.append(ZfEvent(type=ADMITTED, actor="zf-runtime", origin="kernel",
            correlation_id=run_id, payload={"association_id": association_id, "kind": kind,
                "workflow_run_id": run_id, "association_ref": ref, "decision_owner": actor}))
        return {"status": "admitted", "association_id": association_id, "event_id": event.id}


def _associations(state_dir: Path, events):
    for event in events:
        if event.type == ADMITTED and event.origin == "kernel":
            yield event.payload["association_id"], hydrate_sidecar_ref(state_dir, event.payload["association_ref"]).payload


def shared_repair_gate(state_dir: Path, events, action: dict[str, Any]) -> str:
    association_id = str(action.get("shared_repair_association_id") or "")
    if not association_id:
        return ""
    associations = dict(_associations(state_dir, events))
    body = associations.get(association_id)
    if body is None or body.get("kind") != "shared_resource":
        return "shared_repair_association_missing"
    if action.get("recovery_case_id") != body["owner_case_id"]:
        return "shared_repair_owned_by_another_case"
    owner = next(row for row in body["members"] if row["recovery_case_id"] == body["owner_case_id"])
    for key, value in owner.items():
        if value and action.get(key) != value:
            return f"shared_repair_binding_mismatch:{key}"
    for key in ("resource_id", "resource_version", "repair_step_id"):
        if action.get(key) != body[key]:
            return f"shared_repair_binding_mismatch:{key}"
    for event in events:
        if event.type != "run.manager.action.planned":
            continue
        prior = associations.get(event.payload.get("shared_repair_association_id"), {})
        if prior and all(prior.get(key) == body[key] for key in ("resource_id", "resource_version", "repair_step_id")):
            # Delivery loss, timeout or a new case never grants a blind retry.
            return "shared_repair_already_claimed_reconcile_effect"
        if prior.get("resource_id") == body["resource_id"] and not any(
            receipt.type == "run.manager.action.effect.passed"
            and receipt.payload.get("shared_repair_association_id") == event.payload.get("shared_repair_association_id")
            and receipt.payload.get("checkpoint_id") == event.payload.get("checkpoint_id")
            for receipt in events
        ):
            return "shared_resource_repair_inflight"
    return ""


def inherited_failure_operations(state_dir: Path, events, contexts) -> dict[str, list[dict[str, Any]]]:
    selected: dict[str, set[str]] = {}
    for _, body in _associations(state_dir, events):
        if body.get("kind") != "problem_continuation":
            continue
        target = next(row for row in body["members"] if row["recovery_case_id"] == body["target_case_id"])
        current = contexts.get(target["task_id"], {})
        if any(target.get(key) != current.get(key) for key in ("workflow_run_id", "task_map_generation")):
            continue
        selected.setdefault(target["task_id"], set()).update(body["failure_operation_ids"])
    if not selected:
        return {}
    from zf.runtime.task_pipeline_execution_status import normalize_task_pipeline_operation_semantics
    required = set().union(*selected.values())
    rows = {row["operation_id"]: row for row in normalize_task_pipeline_operation_semantics(
        state_dir, [row for key, row in reduce_workflow_operations(events).items() if key in required],
    )}
    if required - rows.keys():
        raise ValueError("required failure lineage operations unavailable")
    return {task_id: [rows[key] for key in sorted(ids)] for task_id, ids in selected.items()}
