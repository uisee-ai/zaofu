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
            # A control result already identifies the report. Only a result-less
            # terminal outcome uses the child event as its evidence identity.
            source = {"operation_id": member["operation_id"], "control_result_ref": ref,
                      "child_event_id": None if ref else member.get("child_event_id"), "pointer": pointer}
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


def partition_candidate_findings(findings: list[dict], dispositions: object) -> tuple[set[str], list[dict]]:
    if not isinstance(dispositions, list):
        raise ValueError("candidate decision requires finding_dispositions")
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
    if not expected or seen != set(expected):
        raise ValueError("candidate finding dispositions must cover every finding")
    return affected, retained


def partition_product_tasks(findings: list[dict], dispositions: object) -> tuple[set[str], list[dict]]:
    affected, retained = partition_candidate_findings(findings, dispositions)
    if not affected:
        raise ValueError("candidate product repair requires at least one eligible product finding")
    return affected, retained


def _anchored_freezes(events, candidate, run_id, task_id):
    """Older native receipts may bind the anchor through their admitted generation or child snapshots."""
    freezes, child_owners, generation_owners = [], {}, {}
    for e in events:
        p = e.payload
        if (p.get("workflow_run_id") or e.correlation_id) != run_id:
            continue
        generation = p.get("task_map_generation")
        if e.type == "task.pipeline.generation.admitted" and e.origin == "kernel" and p.get("pdd_id"):
            generation_owners[p.get("generation_id"), generation] = p["pdd_id"]
        elif (e.type == "task.pipeline.verify.completed" and e.origin in {"", "kernel"} and p.get("parent_task_id")
                and all(p.get(k) for k in ("contract_snapshot_digest", "contract_snapshot_ref",
                                           "target_snapshot_digest", "target_snapshot_ref"))):
            child_owners.setdefault((generation, e.task_id), set()).add(p["parent_task_id"])
        elif (e.type == "candidate.ready" and e.origin in {"", "kernel"}
              and p.get("schema_version") == "candidate-freeze-receipt.v1" and not p.get("repaired_failure_event_id")
              and p.get("candidate_ref") == candidate.payload.get("candidate_ref")
              and (p.get("candidate_head_commit") or p.get("candidate_head")) and p.get("freeze_receipt_digest")
              and (p.get("freeze_receipt_ref") or {}).get("sha256") == p["freeze_receipt_digest"]):
            owner = p.get("pdd_id") or (p.get("generation_id") and generation_owners.get((p["generation_id"], generation)))
            if owner == task_id or (not owner and p.get("completed_task_ids") and all(
                    child_owners.get((generation, child)) == {task_id} for child in p["completed_task_ids"])):
                freezes.append(e)
    return freezes


def _freeze_predecessors(freezes, candidate):
    """Walk native same-branch base -> head links backward; a SHA collision needs Plan provenance."""
    by_head = {}
    for index, freeze in enumerate(freezes):
        head = freeze.payload.get("candidate_head_commit") or freeze.payload["candidate_head"]
        by_head.setdefault(head, []).append((index, freeze))
    ancestors = {}
    before, current = len(freezes) - 1, candidate
    while current:
        matches = [(idx, e) for idx, e in by_head.get(current.payload.get("candidate_base_commit"), []) if idx < before]
        if len(matches) != 1:
            break
        before, current = matches[0]
        ancestors[current.id] = current
    return ancestors


def _admitted_plan_sources(events, candidate, run_id, task_id, positions):
    """Find the planning inputs of the exact admitted generation, including unchanged-commit replans."""
    body = candidate.payload
    rows = {e.id:e for e in events}
    pending = [e for e in events if e.type == "task.pipeline.generation.admitted" and e.origin == "kernel"
               and body.get("generation_id") and e.payload.get("generation_id") == body["generation_id"]
               and e.payload.get("task_map_generation") == body.get("task_map_generation")
               and e.payload.get("plan_artifact_package_digest") == body.get("plan_artifact_package_digest")
               and e.payload.get("workflow_run_id") == run_id and e.payload.get("pdd_id") == task_id]
    seen, sources = set(), {}
    while pending:
        e = pending.pop()
        if e.id in seen:
            continue
        p = e.payload
        if (p.get("workflow_run_id") or e.correlation_id) != run_id or (p.get("pdd_id") and p["pdd_id"] != task_id):
            continue
        decision = p.get("orchestrator_decision_ref") or {}
        if (p.get("replan_classification") == "oa_semantic_return_to_plan" and isinstance(decision, dict)
                and (e.origin == "kernel" or (not e.origin and e.actor == "zf-cli"))):
            # A legacy resynthesis trigger may omit origin. It is a referenced input;
            # authority still comes from the exact kernel marker and admitted generation.
            key = (p.get("candidate_event_id"), p.get("rework_of"), decision.get("ref"), decision.get("sha256"))
            sources[key] = max(sources.get(key, -1), positions[e.id])
        if e.origin != "kernel":
            continue
        seen.add(e.id)
        # Child assertions are not Plan ancestry. Use the same kernel admission and
        # causation fields written by the Plan and Task Pipeline.
        parents = [e.causation_id, p.get("trigger_event_id"), p.get("task_map_admitted_event_id")]
        pending.extend(rows[parent] for parent in parents if parent in rows and parent not in seen
                       and positions[parent] < positions[e.id])
    return sources, seen


def retained_candidate_obligations(events: list, *, run_id: str, task_id: str, candidate=None) -> list[dict]:
    """Return duties relevant to this freeze, or the admitted repair-map duties for an unscoped query."""
    relevant = [e for e in events if e.origin == "kernel" and e.payload.get("workflow_run_id") == run_id
                and e.payload.get("pdd_id") == task_id and e.type in {"task_map.ready", "orchestrator.replan_requested"}
                and (e.payload.get("rework_summary") or {}).get("candidate_obligations")]
    if not relevant:
        return []
    ancestors, plan_sources, native, admitted = {}, {}, {}, {}
    plan_ancestors = set()
    positions = {event.id: i for i, event in enumerate(events)}
    generations, branches = set(), set()
    if candidate is not None:
        freezes = _anchored_freezes(events, candidate, run_id, task_id)
        if candidate not in freezes:
            # A compatible old receipt can omit its anchor. If a matching obligation has
            # a direct claim on its base or generation, require a mechanical binding.
            body = candidate.payload
            if any(e.payload.get("source_commit") == body.get("candidate_base_commit")
                   or e.payload.get("task_map_generation") == body.get("task_map_generation") for e in relevant):
                raise ValueError("cannot bind Candidate recovery obligations to this frozen workflow anchor")
            return []
        native = {e.id:e for e in freezes}
        ancestors = _freeze_predecessors(freezes, candidate)
        plan_sources, plan_ancestors = _admitted_plan_sources(events, candidate, run_id, task_id, positions)
        generations = {e.payload.get("task_map_generation") for e in [*ancestors.values(), candidate]}
        branches = {(e.payload.get("candidate_head_commit") or e.payload["candidate_head"], e.payload["candidate_ref"])
                    for e in ancestors.values()}
        for e in events:
            p = e.payload
            decision = p.get("decision_ref") or {}
            if (e.type == "orchestrator.semantic.decision.admitted" and e.origin == "kernel"
                    and p.get("decision") == "return_to_plan" and p.get("checkpoint") == "semantic_failure"
                    and p.get("workflow_run_id") == run_id and isinstance(decision, dict)):
                admitted.setdefault((e.causation_id, p.get("operation_id"), decision.get("ref"), decision.get("sha256")), e)
    retained = {}
    for event in relevant:
        p = event.payload
        if event.type == "orchestrator.replan_requested":
            source = native.get(p.get("candidate_event_id"))
            decision = p.get("orchestrator_decision_ref") or {}
            op = p.get("orchestrator_operation_id")
            admission = admitted.get((event.causation_id, op, decision.get("ref"), decision.get("sha256")))
            source_key = (p.get("candidate_event_id"), p.get("candidate_failure_id"), decision.get("ref"), decision.get("sha256"))
            if (not source or not admission or not op or not decision.get("sha256") or not event.causation_id
                    or event.actor != "zf-cli" or p.get("classification") != "oa_semantic_return_to_plan"
                    or not p.get("candidate_failure_id") or admission.payload.get("decision_ref") != decision
                    or not positions[source.id] < positions[admission.id] < positions[event.id] < positions[candidate.id]
                    or p.get("source_commit") != (source.payload.get("candidate_head_commit") or source.payload["candidate_head"])
                    or p.get("task_map_generation") != source.payload.get("task_map_generation")
                    or p.get("target_ref") != source.payload.get("candidate_ref")
                    or (source.id not in ancestors and plan_sources.get(source_key, -1) <= positions[event.id])):
                continue
        elif candidate is not None:
            if (event.id not in plan_ancestors and (p.get("task_map_generation") not in generations
                    or (p.get("source_commit"), p.get("candidate_ref") or p.get("target_ref")) not in branches)):
                continue
        for finding in p["rework_summary"]["candidate_obligations"]:
            retained.setdefault(finding["finding_id"], finding)
    return list(retained.values())


def pending_obligation_diagnoses(events: list, *, state_dir=None, run_id="", config=None) -> list[dict]:
    from zf.runtime.candidate_obligation_followup import pending_reader_obligations

    actions = pending_reader_obligations(state_dir, events, run_id=run_id, config=config) if state_dir else []
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
        raise VerificationResultError(
            "passed candidate requires evidence-bound disposition of every retained recovery obligation. "
            "Compare result.recovery_obligation_results[] with the frozen contract's candidate_recovery_obligations[].finding_id. "
            "For a passed verdict, each finding_id needs exactly one row with status resolved, an independent rationale "
            "and nonempty evidence_refs containing ref and sha256 from the authorized inputs. "
            "Choose the verdict supported by the current evidence."
        )
