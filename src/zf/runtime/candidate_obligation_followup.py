"""Run Manager delivery of an admitted OA decision's independent Reader duties."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.run_contract import stable_json_sha256
from zf.runtime.sidecar_refs import hydrate_sidecar_ref

REASSESS = "candidate-reader-reassess"
REVIEW = "candidate-reader-results-review"
UNAVAILABLE = "candidate.reader.reassessment.unavailable"
REQUESTED = "candidate.reader.reassessment.requested"
OA_REQUEST = "orchestrator.semantic.failure.requested"


def _reader_groups(marker: ZfEvent) -> dict[str, list[str]]:
    p = marker.payload
    if (marker.type != "orchestrator.replan_requested" or marker.origin != "kernel"
            or p.get("classification") != "oa_semantic_return_to_plan"
            or not all(p.get(k) for k in ("workflow_run_id", "pdd_id", "task_map_generation",
                       "source_commit", "orchestrator_operation_id", "orchestrator_decision_ref",
                       "candidate_event_id", "candidate_failure_id"))):
        return {}
    groups = {}
    for row in (p.get("rework_summary") or {}).get("candidate_obligations", []):
        if row["disposition"]["classification"] == "execution_unknown":
            groups.setdefault(row["operation_id"], []).append(row["finding_id"])
    return {op: sorted(ids) for op, ids in groups.items()}


def _action(marker, operation, name, findings, expected, *, receipt_digest=""):
    key = stable_json_sha256([marker.id, operation, name, findings, receipt_digest])
    p = marker.payload
    return {
        "schema_version": "run-manager.pending-action.v1",
        "action": name, "safe_resume_action": name.replace("-", "_"),
        "checkpoint_id": "reader-duty-" + key, "fingerprint": key,
        "recovery_case_id": "rcase-reader-" + key,
        "workflow_run_id": p["workflow_run_id"], "task_id": p["pdd_id"], "pdd_id": p["pdd_id"],
        "source_event_ids": [marker.id], "task_map_generation": p["task_map_generation"],
        "reader_marker_id": marker.id, "reader_source_operation_id": operation,
        "reader_finding_ids": findings, "reader_receipt_digest": receipt_digest, "expected_downstream_events": expected,
        "failure_class": "candidate_reader_evidence_followup",
        "owner_route": "run_manager", "action_policy": "auto_decide",
        "reason": "Collect the OA's evidence-only Reader duties on their original freeze and return the receipts.",
    }


def _delivery_stalled(state_dir, events, event, operation_id, role, now):
    from zf.core.state.task_attempts import TaskAttemptStore
    from zf.runtime.task_attempt_liveness import iso_epoch
    from zf.runtime.workflow_operation import reduce_workflow_operations

    if role is None:
        return False
    deadline = role.orphan_escalate_seconds
    if deadline <= 0:
        return False
    operation = reduce_workflow_operations(events).get(operation_id, {}) if operation_id else {}
    attempt = operation.get("active_attempt_id")
    current = now.timestamp()
    latest = max(iso_epoch(event.ts), iso_epoch(operation.get("last_event_at", "")))
    if iso_epoch(operation.get("reservation_expires_at", "")) > current:
        return False
    if attempt:
        lease = TaskAttemptStore(state_dir / "task_attempts.json").get(attempt) or {}
        if (lease.get("operation_id") == operation_id and lease.get("instance_id") == role.instance_id
                and lease.get("status") in {"prepared", "delivering", "sent"}
                and iso_epoch(lease.get("lease_expires_at", "")) > current):
            return False
        for e in events:
            if (e.type == "worker.heartbeat" and e.actor == role.instance_id
                    and e.payload.get("operation_id") == operation_id and e.payload.get("attempt_id") == attempt):
                latest = max(latest, iso_epoch(e.ts))
    return current - latest >= deadline


def _progress(state_dir, events, marker, groups, now, config):
    from zf.runtime.candidate_reader_reassessment import reader_reassessment_state

    actions, receipts = [], []
    for op, ids in groups.items():
        admitted = next((e for e in events if e.type in {REQUESTED, UNAVAILABLE}
            and e.origin == "kernel" and e.causation_id == marker.id
            and e.payload.get("marker_event_id") == marker.id
            and e.payload.get("candidate_event_id") == marker.payload["candidate_event_id"]
            and e.payload.get("workflow_run_id") == marker.payload["workflow_run_id"]
            and e.payload.get("source_operation_id") == op and e.payload.get("finding_ids") == ids), None)
        if not admitted:
            actions.append(_action(marker, op, REASSESS, ids, [REQUESTED, UNAVAILABLE]))
            continue
        row = {"request_event_id": admitted.id, "source_operation_id": op, "finding_ids": ids}
        if admitted.type == UNAVAILABLE:
            receipts.append({**row, "status": "needs_owner", "reason": admitted.payload["reason"]})
            continue
        try:
            state = reader_reassessment_state(state_dir, events, request_event_id=admitted.id)
        except (KeyError, ValueError, OSError) as exc:
            state = {"status": "needs_owner", "reason": f"Reader evidence unavailable: {exc}"}
        role = next((role for role in getattr(config, "roles", [])
                     if role.instance_id == admitted.payload.get("role_instance")), None)
        if state["status"] in {"requested", "running"} and (
                _delivery_stalled(state_dir, events, admitted, state.get("operation_id", ""), role, now)
                or (config is not None and role is None)):
            reason = "Reader role unavailable" if role is None else "Reader receipt deadline elapsed"
            state = {**state, "status": "needs_owner", "reason": reason + "; outcome unknown"}
        if state["status"] in {"completed", "needs_owner"}:
            receipts.append({**row, **state})
    return actions, receipts


def _report_obligations(marker, receipts):
    """A Reader can answer only its bound evidence question. The other owners keep their duties."""
    unknown = []
    for receipt in receipts:
        rows = receipt.get("finding_results") or []
        for finding_id in receipt["finding_ids"]:
            matches = [row for row in rows if row.get("finding_id") == finding_id]
            if (not receipt.get("control_result_ref") or len(matches) != 1
                    or matches[0].get("outcome") not in {"supported", "refuted"}):
                unknown.append(finding_id)
    retained = marker.payload["rework_summary"]["candidate_obligations"]
    planner = [r["finding_id"] for r in retained if r["disposition"]["classification"] == "contract_question"]
    product = [r["finding_id"] for r in retained if r["disposition"]["classification"] == "product_defect"]
    return {"reader_evidence_unresolved": bool(unknown),
            "reader_unresolved_finding_ids": sorted(unknown),
            "planner_disposition_required_finding_ids": sorted(planner),
            "product_repair_required_finding_ids": sorted(product),
            "unresolved": bool(unknown or planner or product),
            "authorizes_delivery": False}


def pending_reader_obligations(state_dir: Path, events: list[ZfEvent], *, run_id: str = "",
                               now: datetime | None = None, config=None) -> list[dict[str, Any]]:
    """Project independent Reader requests and one OA delivery per observed receipt revision."""
    now = now or datetime.now(timezone.utc)
    actions = []
    for marker in events:
        if run_id and marker.payload.get("workflow_run_id") != run_id:
            continue
        groups = _reader_groups(marker)
        if not groups:
            continue
        pending, receipts = _progress(state_dir, events, marker, groups, now, config)
        actions.extend(pending)
        if len(receipts) != len(groups):
            continue
        duties = _report_obligations(marker, receipts)
        digest = stable_json_sha256({"reader_receipts": receipts, "obligation_status": duties})
        previous = next((e for e in events if e.type == OA_REQUEST and e.origin == "kernel"
                         and e.causation_id == marker.id and e.payload.get("reader_marker_id") == marker.id
                         and e.payload.get("reader_receipt_digest") == digest), None)
        if previous:
            bound = [e for e in events if e.causation_id == previous.id or e.payload.get("source_event_id") == previous.id]
            fault = next((e for e in bound if e.origin == "kernel" and e.type in {
                "orchestrator.semantic.checkpoint.rejected", "orchestrator.semantic.checkpoint.skipped"}), None)
            from zf.runtime.orchestrator_agent_role import configured_orchestrator_role_from_config
            checkpoint = next((e for e in bound if e.origin == "kernel"
                               and e.type == "orchestrator.semantic.checkpoint.requested"), None)
            op = checkpoint.payload.get("operation_id") if checkpoint else ""
            decision = next((e for e in reversed(events) if op and e.payload.get("operation_id") == op
                             and e.origin == "kernel" and e.type in {"orchestrator.semantic.decision.applied",
                             "orchestrator.semantic.decision.rejected", "orchestrator.semantic.decision.shadowed"}), None)
            if decision and (decision.type != "orchestrator.semantic.decision.applied"
                             or decision.payload.get("status") == "rejected"
                             or (decision.payload.get("decision") == "continue" and duties["reader_evidence_unresolved"])):
                fault = decision
            role = configured_orchestrator_role_from_config(config) if config else None
            if fault or (not decision and ((config is not None and role is None)
                    or _delivery_stalled(state_dir, events, checkpoint or previous, op, role, now))):
                action = _action(marker, previous.id, "diagnose-attention", [], ["run.manager.resident.prompted",
                                                                                 "run.manager.autoresearch.requested"])
                action.update(action_policy="needs_diagnosis", suggested_route="run_manager_resident_agent",
                              safe_resume_action="diagnose_attention", source_event_ids=[marker.id, previous.id]
                              + ([fault.id] if fault else []),
                              required_evidence_refs=[marker.payload["orchestrator_decision_ref"]["ref"],
                                                      previous.payload["reader_report_ref"]["ref"]],
                              summary="Outstanding Candidate Reader findings require a bound next action. "
                                      "Review the prior OA decision, report and outcome. Identify the next owner "
                                      "and required evidence; product verdicts and replay require their own authority.",
                              expected_output=["diagnosis_report", "next_owner", "evidence_or_approval_needed"],
                              reason="OA follow-up did not resolve the outstanding Reader evidence.")
                actions.append(action)
            continue
        actions.append(_action(marker, "", REVIEW, [], [OA_REQUEST], receipt_digest=digest))
    return actions


def execute_reader_obligation(service, action: dict[str, Any]) -> ZfEvent:
    """Rebind an admitted RM action immediately before the durable Reader or OA handoff."""
    from zf.runtime.candidate_reader_reassessment import request_reader_reassessment

    history = service.writer.event_log.read_all()
    marker = next((e for e in history if e.id == action["reader_marker_id"]), None)
    if not marker or action.get("task_id") != marker.payload.get("pdd_id"):
        raise ValueError("Reader obligation marker missing or target changed")
    groups = _reader_groups(marker)
    op = action.get("reader_source_operation_id")
    action_name = action["action"]
    ids = groups.get(op, []) if action_name == REASSESS else []
    digest = action.get("reader_receipt_digest") or ""
    exact = _action(marker, op, action_name, ids, [], receipt_digest=digest)
    if not groups or any(action.get(k) != exact[k] for k in (
            "workflow_run_id", "task_map_generation", "checkpoint_id", "reader_finding_ids", "recovery_case_id")):
        raise ValueError("Reader obligation action binding changed")
    previous = next((e for e in history if e.origin == "kernel" and e.causation_id == marker.id and
                     ((action_name == REVIEW and e.type == OA_REQUEST and e.payload.get("reader_marker_id") == marker.id
                       and e.payload.get("reader_receipt_digest") == digest)
                      or (action_name == REASSESS and e.type in {REQUESTED, UNAVAILABLE}
                          and e.payload.get("source_operation_id") == op))), None)
    if previous:
        return previous
    p = marker.payload
    if action_name == REASSESS and op in groups:
        try:
            return request_reader_reassessment(service, marker=marker, operation_id=op, finding_ids=ids)
        except (ValueError, KeyError, OSError) as exc:
            event_type = UNAVAILABLE
            payload = {"source_operation_id": op, "finding_ids": ids, "reason": str(exc),
                       "candidate_event_id": p["candidate_event_id"], "marker_event_id": marker.id}
    elif action_name == REVIEW:
        _, receipts = _progress(service.state_dir, history, marker, groups, datetime.now(timezone.utc), service.config)
        duties = _report_obligations(marker, receipts)
        if (len(receipts) != len(groups)
                or stable_json_sha256({"reader_receipts": receipts, "obligation_status": duties}) != digest):
            raise ValueError("Reader obligations outstanding or receipts changed")
        from zf.runtime.call_result_envelope import write_immutable_json_sidecar

        report = {"schema_version": "candidate-reader-obligation-report.v1",
                  **{k: p[k] for k in ("workflow_run_id", "task_map_generation", "candidate_event_id", "source_commit",
                                       "orchestrator_decision_ref", "orchestrator_operation_id")},
                  "marker_event_id": marker.id, "candidate_obligations": p["rework_summary"]["candidate_obligations"],
                  "reader_receipts": receipts, "obligation_status": duties,
                  "review_instruction": "Review the prior decision and the exact new receipts. Unknown and missing "
                     "evidence remain unresolved. This checkpoint can acknowledge receipt or escalate for a new "
                     "authorized action. It grants no retry, contract change, Writer dispatch, or verdict."}
        ref = write_immutable_json_sidecar(service.state_dir, report, root="candidate-reader-reassessments",
            schema_version=report["schema_version"], kind="reader_obligation_report", created_by="run-manager",
            source_event_id=marker.id)
        event_type = OA_REQUEST
        payload = {"problem_class": "semantic", "flow_kind": p.get("flow_kind", ""), "reader_marker_id": marker.id,
                   "reader_receipt_digest": digest, **duties,
                   "failure_event_ids": [marker.id], "active_attempt_id": action["checkpoint_id"],
                   "failure_fingerprint": action["checkpoint_id"], "target_stage_id": "reader-obligation-followup",
                   "task_map_generation": p["task_map_generation"], "reader_report_ref": ref}
    else:
        raise ValueError("Unknown Reader obligation action")
    return service.writer.append(ZfEvent(type=event_type, origin="kernel", actor="run-manager", task_id=p["pdd_id"],
        correlation_id=p["workflow_run_id"], causation_id=marker.id,
        payload={**payload, "workflow_run_id": p["workflow_run_id"]}))


def report_sources(state_dir: Path, events: list[ZfEvent], event: ZfEvent) -> list[dict]:
    """Expose the prior OA decision and current evidence through the new attempt read manifest."""
    marker = next((e for e in events if e.id == event.causation_id), None)
    if (event.origin != "kernel" or event.actor != "run-manager" or not marker or not _reader_groups(marker)
            or event.payload["reader_marker_id"] != marker.id or event.task_id != marker.payload["pdd_id"]
            or event.payload["workflow_run_id"] != marker.payload["workflow_run_id"]):
        raise ValueError("Reader review source not bound to a kernel OA marker")
    ref = event.payload["reader_report_ref"]
    report = hydrate_sidecar_ref(state_dir, ref).payload
    duties = _report_obligations(marker, report["reader_receipts"])
    if (duties != report.get("obligation_status") or any(event.payload.get(k) != v for k, v in duties.items())
            or stable_json_sha256({"reader_receipts": report["reader_receipts"], "obligation_status": duties})
               != event.payload.get("reader_receipt_digest")
            or report["candidate_obligations"] != marker.payload["rework_summary"]["candidate_obligations"]
            or report["marker_event_id"] != marker.id or report["orchestrator_decision_ref"]
            != marker.payload["orchestrator_decision_ref"] or report["candidate_event_id"] != marker.payload["candidate_event_id"]):
        raise ValueError("Reader review report target changed")
    refs = [{**ref, "source_id": "reader-obligation-report"},
            {**report["orchestrator_decision_ref"], "source_id": "prior-oa-decision"}]
    for row in report["reader_receipts"]:
        if row.get("control_result_ref"):
            refs.append({**row["control_result_ref"], "source_id": "reader-receipt-" + row["source_operation_id"]})
    for source in refs:
        hydrate_sidecar_ref(state_dir, source)
    return refs
