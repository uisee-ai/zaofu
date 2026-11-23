"""Mechanical evidence coverage for an Agent's mixed Candidate diagnosis."""
import hashlib
import json
from pathlib import Path

from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def candidate_findings(state_dir: Path, members: list, unresolved: list, events: list) -> list[dict]:
    findings = []
    for member in [*members, *unresolved]:
        ref = member.get("control_result_ref")
        result = hydrate_sidecar_ref(state_dir, ref).payload if ref else {}
        criteria = {}
        if result:
            contract = hydrate_sidecar_ref(state_dir, {"ref": result["contract_snapshot_ref"],
                "sha256": result["contract_snapshot_digest"]}).payload
            criteria = {row["acceptance_id"]: row.get("source_task_id")
                        for row in contract.get("acceptance_criteria", [])}
        rows = [(f"/requirement_results/{i}", row) for i, row in enumerate(result.get("requirement_results", []))
                if row.get("status") in {"failed", "blocked"}]
        rows += [(f"/rework_items/{i}", row) for i, row in enumerate(result.get("rework_items", []))]
        if not rows or member.get("execution_status") != "completed":
            rows.append(("/outcome", {"verdict": member.get("verdict"),
                                     "execution_status": member.get("execution_status")}))
        for pointer, row in rows:
            source = {"operation_id": member["operation_id"], "control_result_ref": ref,
                      "child_event_id": member.get("child_event_id"), "pointer": pointer}
            key = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
            task = criteria.get(row.get("acceptance_id"))
            findings.append({**source, "finding_id": "finding-" + key,
                "task_ids": [task] if task else [], "evidence": row,
                "product_repair_eligible": bool(task and pointer != "/outcome"
                    and member.get("verdict") == "rejected" and member.get("execution_status") == "completed"
                    and row.get("status") in {"failed", "incorrect", "missing", "incomplete"})})
    from zf.runtime.host_probe_diagnosis import bound_unknown_host_effect

    operations = {member['operation_id'] for member in [*members, *unresolved]}
    prior = {}
    for event in events:
        p = event.payload
        if (p.get('operation_id') in operations
                and bound_unknown_host_effect(event, prior, p.get('approval_ref', ''))
                and not any(e.origin == 'kernel' and e.type == 'runtime.action.attempt.completed'
                            and e.causation_id == event.causation_id for e in events)):
            findings.append({'finding_id': 'host-effect-' + event.causation_id,
                'operation_id': p['operation_id'], 'control_result_ref': None,
                'original_start_event_id': event.causation_id, 'child_event_id': event.id,
                'original_manifest_ref': prior[event.causation_id].payload.get('manifest_ref'),
                'pointer': '/host_effect', 'task_ids': [], 'product_repair_eligible': False,
                'evidence': {'status': 'unknown', 'manifest_digest': p['manifest_digest']}})
        prior[event.id] = event
    return findings


def partition_product_tasks(findings: list[dict], dispositions: object) -> tuple[set[str], list[dict]]:
    if not isinstance(dispositions, list):
        raise ValueError("mixed candidate repair requires finding_dispositions")
    expected = {row["finding_id"]: row for row in findings}
    seen, affected, retained = set(), set(), []
    for row in dispositions:
        if not isinstance(row, dict):
            raise ValueError("candidate finding disposition must be an object")
        key = row.get("finding_id")
        if not isinstance(key, str) or key not in expected or key in seen:
            raise ValueError("candidate finding disposition reference unknown or duplicated")
        kind = row.get("classification")
        if (not isinstance(kind, str) or kind not in {"product_defect", "contract_question", "execution_unknown"}
                or any(not isinstance(row.get(k), str) or not row[k].strip()
                       for k in ("owner", "next_action", "rationale"))):
            raise ValueError("candidate finding requires classification, owner, next_action and rationale")
        finding = expected[key]
        if kind == "product_defect":
            if not finding["product_repair_eligible"]:
                raise ValueError("unknown or blocked execution cannot authorize product repair")
            affected.update(finding["task_ids"])
        retained.append({**finding, "disposition": dict(row), "status": "pending"})
        seen.add(key)
    if seen != set(expected) or not affected:
        raise ValueError("candidate finding dispositions must cover every finding and select a product repair")
    return affected, retained


def retained_candidate_obligations(events: list, *, run_id: str, task_id: str) -> list[dict]:
    """Ledger-backed duties survive task-map replacement and controller restart."""
    retained = {}
    for event in events:
        if (event.origin != "kernel" or event.type != "task_map.ready"
                or event.payload.get("workflow_run_id") != run_id or event.payload.get("pdd_id") != task_id):
            continue
        for finding in (event.payload.get("rework_summary") or {}).get("candidate_obligations", []):
            retained[finding["finding_id"]] = finding
    return list(retained.values())


def pending_obligation_diagnoses(events: list) -> list[dict]:
    actions = []
    for event in events:
        p = event.payload
        if event.origin != "kernel" or event.type != "task_map.ready":
            continue
        obligations = [row for row in (p.get("rework_summary") or {}).get("candidate_obligations", [])
                       if row["disposition"]["classification"] != "product_defect"]
        if not obligations:
            continue
        actions.append({
            "schema_version": "run-manager.pending-action.v1", "action": "diagnose-attention",
            "checkpoint_id": "candidate-obligations-" + event.id,
            "fingerprint": "candidate-obligations:" + event.id,
            "safe_resume_action": "diagnose_attention", "failure_class": "candidate_recovery_obligations",
            "workflow_run_id": p["workflow_run_id"], "task_id": p["pdd_id"],
            "source_event_ids": [event.id], "candidate_obligations": obligations,
            "owner_route": "run_manager", "suggested_route": "run_manager_resident_agent",
            "action_policy": "needs_diagnosis", "intervention_class": "diagnose",
            "expected_output": ["run.manager.agent.recommendation"],
            "expected_downstream_events": ["run.manager.resident.prompted", "run.manager.autoresearch.requested"],
            "reason": "Product repair is dispatched; diagnose the retained independent obligations in parallel. "
                "Respect each designated owner and existing approvals; this request grants no host retry, "
                "verification replay, contract change or product verdict.",
        })
    return actions


def validate_obligation_results(result: dict, contract: dict) -> None:
    from zf.runtime.verification_result import VerificationResultError

    expected = {row["finding_id"] for row in contract.get("candidate_recovery_obligations", [])}
    if not expected or result.get("verdict") != "passed":
        return
    rows = result.get("recovery_obligation_results")
    if (not isinstance(rows, list) or len(rows) != len(expected)
            or any(not isinstance(row, dict) or not isinstance(row.get('finding_id'), str) for row in rows)
            or {row.get("finding_id") for row in rows} != expected
            or any(row.get("status") != "resolved" or not str(row.get("rationale") or "").strip()
                   or not isinstance(row.get("evidence_refs"), list) or not row["evidence_refs"]
                   or any(not isinstance(ref, dict) or not ref.get('ref') or not ref.get('sha256')
                          for ref in row["evidence_refs"])
                   for row in rows)):
        raise VerificationResultError("passed candidate requires evidence-bound disposition of every retained recovery obligation")
