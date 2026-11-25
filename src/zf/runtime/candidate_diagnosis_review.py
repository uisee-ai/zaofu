"""Bind an independent assessment of an OA diagnosis without producing a Plan."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.plan_candidate_owner_review import bound_candidate_diagnosis
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


DIAGNOSIS_CRITIC_GUIDANCE = (
    "Review the frozen OA diagnosis only. For approval give one report.diagnosis_assessments row "
    "per signed finding_id, with finding_id, conclusion and basis. Cite plan-rework-context as "
    "requirement_evidence and the matching child finding_results[i] entry as current_evidence. Approval closes "
    "only this diagnosis. Reject unresolved conclusions or raise a genuinely open Owner decision "
    "using the usual evidence contract."
)


def diagnosis_review_scope(state: Path, manifest: Mapping, reports: list, refs: list, children: list, *, events: list[ZfEvent] | None
) -> dict:
    if not any(
        isinstance(body := row.get("report"), Mapping) and body.get("candidate_kind") == "diagnosis_review"
        for row in reports
    ):
        return {}
    trigger = manifest.get("trigger_payload") or {}
    if not bound_candidate_diagnosis(trigger) or len(children) != 1 or len(reports) != 1:
        raise ValueError("A diagnosis review requires one Planner and its bound OA diagnosis")
    (child,) = children
    sources = {r["source_id"]: r for r in refs}
    child_ref = sources[child["source_id"]]
    body = hydrate_sidecar_ref(state, child_ref).payload
    context_ref = sources.get("plan-rework-context")
    if not context_ref:
        raise ValueError("Missing OA diagnosis evidence")
    context = hydrate_sidecar_ref(state, context_ref).payload
    index = context.get("candidate_diagnosis_evidence") or {}
    findings = [row["finding_id"] for row in index.get("reader_findings", [])]
    planner_findings = [r["finding_id"] for r in body.get("finding_results", [])]
    if (
        not findings
        or len(set(findings)) != len(findings)
        or not index.get("failure_event_id")
        or not child.get("result_event_id")
        or body.get("candidate_kind") != "diagnosis_review"
        or context.get("source_commit") != trigger["source_commit"]
        or index.get("candidate_event_id") != trigger["candidate_event_id"]
        or body.get("operator_feedback", []) != context.get("operator_feedback", [])
        or sorted(planner_findings) != sorted(findings)
    ):
        raise ValueError("The Planner diagnosis differs from the frozen OA evidence")
    admissions = [
        e
        for e in events or ()
        if e.type == "workflow.call.result.admitted"
        and e.origin == "kernel"
        and e.payload.get("source_event_id") == child["result_event_id"]
        and e.payload.get("workflow_run_id") == trigger["workflow_run_id"]
        and e.payload.get("operation_id") == body.get("operation_id")
        and e.payload.get("request_hash") == body.get("request_hash")
    ]
    if len(admissions) != 1:
        raise ValueError("Missing current Planner admission")
    (admitted,) = admissions
    original = hydrate_sidecar_ref(state, admitted.payload["control_result_ref"]).payload
    if (
        original.get("fanout_id") != manifest["fanout_id"]
        or original.get("target_commit") != trigger["source_commit"]
        or any(
            original.get(k) != body.get(k)
            for k in ("role_instance", "candidate_kind", "finding_results", "operator_feedback")
        )
    ):
        raise ValueError("Planner admission differs from the reviewed source")
    return {
        "planner_admitted_event_id": admitted.id,
        "candidate_event_id": index["candidate_event_id"],
        "candidate_failure_id": index["failure_event_id"],
        "source_commit": trigger["source_commit"],
        "task_map_generation": trigger["task_map_generation"],
        "plan_artifact_package_digest": trigger["plan_artifact_package_digest"],
        "finding_ids": planner_findings,
        "planner_result_event_id": child["result_event_id"],
        "planner_source_id": child["source_id"],
        "planner_report_sha256": child_ref["sha256"],
        "context_sha256": context_ref["sha256"],
    }


def diagnosis_assessments(scope: Mapping, body: Mapping, *, error) -> list:
    """Check complete, attributable assessments; interpretation belongs to the Critic."""
    rows = body.get("diagnosis_assessments")
    path = "report.diagnosis_assessments"
    if (
        not isinstance(rows, list)
        or not all(isinstance(row, Mapping) for row in rows)
        or sorted(str(row.get("finding_id") or "") for row in rows) != sorted(scope["finding_ids"])
    ):
        error(path, "Assess each original finding_id exactly once")
        return []
    assessed = []
    for i, row in enumerate(rows):
        field = f"{path}[{i}]"
        if not isinstance(row.get("conclusion"), str) or not row["conclusion"].strip():
            error(field, "Give the independent diagnosis conclusion")
        basis = row.get("basis")
        for key, source in (
            ("current_evidence", scope["planner_source_id"]),
            ("requirement_evidence", "plan-rework-context"),
        ):
            citations = basis.get(key) if isinstance(basis, Mapping) else None
            if not isinstance(citations, list) or not any(
                isinstance(c, Mapping) and c.get("source_id") == source for c in citations
            ):
                error(field, f"{key} must cite {source}")
        finding_id = row["finding_id"]
        selector = f"$.finding_results[{scope['finding_ids'].index(finding_id)}]"
        citations = basis.get("current_evidence") if isinstance(basis, Mapping) else None
        if not isinstance(citations, list) or not any(
            isinstance(c, Mapping)
            and c.get("source_id") == scope["planner_source_id"]
            and (
                c.get("json_path") == selector
                or str(c.get("json_path") or "").startswith(selector + ".")
                or (c.get("json_path") == "$" and finding_id in str(c.get("quote") or ""))
            )
            for c in citations
        ):
            error(field, f"Bind current_evidence to {selector} or quote this finding_id from the child root")
        assessed.append((field, row))
    return assessed


def settle_diagnosis_review(runtime: Any, event: ZfEvent, manifest: dict) -> None:
    """Write only the normal aggregate terminal receipt, after both signed results."""
    from zf.runtime.candidate_verification_authority import frozen_candidate_for_payload
    from zf.runtime.plan_synth_recovery_refs import bound_critic_candidate_refs

    payload = event.payload
    trigger = manifest["trigger_payload"]
    run = manifest.get("workflow_run_id") or manifest["trace_id"]
    if not bound_candidate_diagnosis(trigger) or payload.get("output_profile_id") != "plan-synth":
        raise ValueError("Unbound OA diagnosis review")
    events = runtime.event_log.read_all()
    admissions = [
        e
        for e in events
        if e.type == "workflow.call.result.admitted"
        and e.origin == "kernel"
        and e.payload.get("workflow_run_id") == run
        and e.payload.get("source_event_id") == event.id
        and e.payload.get("operation_id") == payload.get("operation_id")
        and e.payload.get("request_hash") == payload.get("request_hash")
        and e.payload.get("control_result_ref") == payload.get("control_result_ref")
    ]
    if len(admissions) != 1:
        raise ValueError("Missing current Critic admission")
    (admitted,) = admissions
    decision = hydrate_sidecar_ref(runtime.state_dir, admitted.payload["control_result_ref"]).payload
    contract = hydrate_sidecar_ref(
        runtime.state_dir,
        {
            "ref": decision["plan_synth_contract_ref"],
            "sha256": decision["plan_synth_contract_digest"],
        },
    ).payload
    scope = contract.get("diagnosis_review") or {}
    if (
        not scope
        or contract.get("trigger_event_id") != manifest.get("trigger_event_id")
        or contract.get("workflow_run_id") != run
        or contract.get("fanout_id") != manifest["fanout_id"]
        or decision.get("execution_status") != "completed"
        or decision.get("verdict") != "passed"
        or not decision.get("diagnosis_assessments")
    ):
        raise ValueError("Signed Critic verdict does not approve this diagnosis")
    retained = frozen_candidate_for_payload(events, run, payload={})
    if (
        retained is None
        or retained.id != scope["candidate_event_id"]
        or any(
            scope[key] != trigger.get(key)
            for key in (
                "source_commit",
                "candidate_event_id",
                "task_map_generation",
                "plan_artifact_package_digest",
            )
        )
    ):
        raise ValueError("The frozen candidate changed after the diagnosis was requested")
    sources = bound_critic_candidate_refs(runtime.state_dir, events, {**payload, **decision})
    (planner_ref,) = [
        r
        for r in sources
        if r["source_id"] == scope["planner_source_id"] and r["sha256"] == scope["planner_report_sha256"]
    ]
    planner = hydrate_sidecar_ref(runtime.state_dir, planner_ref).payload
    if (
        planner.get("role_instance") == payload.get("role_instance")
        or planner.get("candidate_kind") != "diagnosis_review"
        or not any(c.get("result_event_id") == scope["planner_result_event_id"] for c in manifest["children"])
    ):
        raise ValueError("The diagnosed Planner report is no longer current")
    previous = next(
        (
            e
            for e in events
            if e.type == "fanout.aggregate.completed"
            and e.origin == "kernel"
            and e.causation_id == event.id
            and e.payload.get("fanout_id") == manifest["fanout_id"]
            and (e.payload.get("candidate_diagnosis_review") or {}).get("critic_admitted_event_id")
            == admitted.id
        ),
        None,
    )
    if previous:
        runtime._consume_durable_fanout_aggregate_result(previous)
        return
    receipt = runtime.event_writer.append(
        ZfEvent(
            type="fanout.aggregate.completed",
            actor="zf-cli",
            origin="kernel",
            payload={
                "fanout_id": manifest["fanout_id"],
                "trace_id": run,
                "workflow_run_id": run,
                "stage_id": manifest["stage_id"],
                "pdd_id": trigger["pdd_id"],
                "status": "completed",
                "recommendation": "approve",
                "success_event": "",
                "failure_event": "",
                "synth_event_id": event.id,
                "candidate_diagnosis_review": {
                    **scope,
                    "critic_admitted_event_id": admitted.id,
                    "critic_control_result_ref": admitted.payload["control_result_ref"],
                    "plan_synth_contract_digest": decision["plan_synth_contract_digest"],
                },
            },
            causation_id=event.id,
            correlation_id=run,
        )
    )
    runtime._consume_durable_fanout_aggregate_result(receipt)
