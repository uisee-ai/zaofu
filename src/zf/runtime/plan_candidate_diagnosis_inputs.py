"""Read-only Planner evidence from one admitted, aggregate Candidate diagnosis."""
from collections import defaultdict
from pathlib import Path

from zf.core.events import EventLog
from zf.runtime.artifact_access import artifact_access_allowed
from zf.runtime.artifact_query.extractors import _intersect_access_scopes
from zf.runtime.candidate_failure_checkpoint import aggregate_candidate_binding
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _diagnosis_authority(state: Path, payload, context, decision, decision_ref):
    run = decision["identity"]["workflow_run_id"]
    anchor, failure = payload.get("pdd_id"), context.get("rework_of")
    scope = ("task_ids", "failed_task_ids", "downstream_task_ids")
    if (not anchor or not failure or context.get("replan_classification") != "oa_semantic_return_to_plan"
            or any(payload.get(key) or context.get(key) for key in scope)):
        raise ValueError("Candidate diagnosis requires its read-only anchor and empty Writer scope")
    events = EventLog(state / "events.jsonl").read_all()
    marker_binding = {"workflow_run_id": run, "pdd_id": anchor, "resume_scope": "candidate_diagnosis_only",
                      "classification": "oa_semantic_return_to_plan", "candidate_failure_id": failure, "rework_of": failure,
                      "orchestrator_operation_id": decision["operation_id"], "orchestrator_decision_ref": decision_ref,
                      "rework_summary": context["rework_summary"]}
    for key in ("candidate_event_id", "source_commit", "target_ref", "task_map_generation", "plan_artifact_package_digest",
                "orchestration_delta_ref", "orchestration_delta_digest"):
        marker_binding[key] = payload.get(key) or context.get(key)
    markers = [e for e in events if e.origin == "kernel" and e.type == "orchestrator.replan_requested"
               and e.actor == "zf-cli" and e.task_id == anchor and not any(e.payload.get(k) for k in scope)
               and all(value and e.payload.get(key) == value for key, value in marker_binding.items())]
    if len(markers) != 1:
        raise ValueError("Candidate diagnosis lacks its exact frozen replan marker")
    before = events[:events.index(markers[0])]
    admissions = [e for e in before if e.origin == "kernel" and e.type == "orchestrator.semantic.decision.admitted"
                  and e.causation_id and e.causation_id == markers[0].causation_id
                  and all(e.payload.get(key) == value for key, value in {
                      "workflow_run_id": run, "operation_id": decision["operation_id"], "decision_ref": decision_ref,
                      "checkpoint": "semantic_failure", "checkpoint_policy": "blocking", "decision": "return_to_plan",
                      "status": "admitted"}.items())]
    if len(admissions) != 1:
        raise ValueError("Candidate diagnosis lacks an admitted blocking OA decision")
    history = before[:before.index(admissions[0])]
    bound = aggregate_candidate_binding(state, history, failure_id=failure, run_id=run, task_id=anchor)
    if (any(marker_binding[k] != bound[k] for k in ("candidate_event_id", "source_commit", "task_map_generation",
                                                    "plan_artifact_package_digest"))
            or marker_binding["target_ref"] != bound["candidate_ref"]):
        raise ValueError("Candidate diagnosis aggregate differs from the admitted frozen target")
    target = {"task_id": anchor, "role_instance": bound["role_instance"], "stage_id": bound["stage_id"],
              "attempt_id": bound["attempt_id"]}
    directives = [d for d in decision["delta"]["directives"] if d.get("action") == "return_to_plan"
                  and all((d.get("target") or {}).get(k) == v for k, v in target.items())
                  and all(k not in d["target"] or d["target"][k] == v for k, v in {
                      "candidate_failure_id": failure, "operation_id": bound["operation_id"],
                      "task_map_generation": bound["task_map_generation"], "source_commit": bound["source_commit"]}.items())]
    if len(directives) != 1:
        raise ValueError("Candidate diagnosis lacks a unique directive for its rejected Reader")
    return bound, directives[0], history


def _offered_sources(state, history, decision, bound, baseline_sources, failure_id):
    run = decision["identity"]["workflow_run_id"]
    operations = [e for e in history if e.origin == "kernel" and e.type == "workflow.operation.requested"
                  and e.payload.get("operation_id") == decision["operation_id"] and e.payload.get("workflow_run_id") == run]
    if len(operations) != 1:
        raise ValueError("Candidate diagnosis lacks its OA request")
    request = hydrate_sidecar_ref(state, operations[0].payload["request_ref"]).payload["request"]
    manifest = hydrate_sidecar_ref(state, request["source_manifest_ref"]).payload
    if (request.get("operation_type") != "orchestrator_agent_semantic"
            or request.get("role_instance") != decision["role_instance"] or request.get("operation_id") != decision["operation_id"]
            or manifest.get("workflow_run_id") != run or manifest.get("attempt_id") != decision["attempt_id"]
            or manifest.get("plan_artifact_package_digest") != bound["plan_artifact_package_digest"]
            or manifest.get("task_map_generation") != bound["task_map_generation"]):
        raise ValueError("Candidate diagnosis OA manifest identity differs")
    checkpoint = hydrate_sidecar_ref(state, request["checkpoint_input_ref"]).payload
    target = checkpoint.get("checkpoint_context") or {}
    if (request["checkpoint_input_ref"]["sha256"] != decision["identity"]["input_digest"]
            or target.get("candidate_failure_scope") != "aggregate" or target.get("candidate_failure_id") != failure_id
            or target.get("candidate_operation_id") != bound["operation_id"]):
        raise ValueError("Candidate diagnosis OA request lacks its aggregate checkpoint")
    offered = defaultdict(list)
    for item in manifest["sources"]:
        offered[item["ref"], item["sha256"]].append(item)
    # Older OA manifests drop an input descriptor's original ACL and field paths.
    # Each cross-role transfer must also have an independently bound origin.
    origins = defaultdict(list)
    source_event = next((e for e in history if e.id == request["source_event_id"] and e.origin == "kernel"), None)
    descriptors = [*baseline_sources, *(m["control_result_ref"] for m in bound["members"]),
                   *request.get("aggregation_input_refs", []), checkpoint.get("execution_context_ref"),
                   request.get("effective_config_ref"), request.get("checkpoint_input_ref"), request.get("stage_execution_card_ref")]
    if source_event:
        descriptors.append(source_event.payload.get("recovery_context_ref"))
        for key in ("artifact_refs", "input_refs", "result_refs", "evidence_refs"):
            descriptors.extend(source_event.payload.get(key) or [])
    for ref in descriptors:
        if isinstance(ref, dict) and ref.get("ref") and ref.get("sha256"):
            origins[ref["ref"], ref["sha256"]].append(ref)
    return offered, origins, request["source_event_id"]


def _permitted_paths(paths, limits):
    if "$" in paths:
        return limits
    if "$" in limits:
        return paths
    return [path for path in paths if path in limits]


def candidate_diagnosis_inputs(state: Path, payload, context, decision, decision_ref, baseline_sources):
    bound, directive, history = _diagnosis_authority(state, payload, context, decision, decision_ref)
    offered, origins, source_event_id = _offered_sources(state, history, decision, bound, baseline_sources, context["rework_of"])
    selected = {}
    actor = str(payload.get("role_instance") or "")
    identity = dict(actor=actor, role=str(payload.get("role_name") or actor),
                    purpose=str(payload.get("output_profile_id") or "workflow-read"))

    def index(ref, expected_source=None):
        if not isinstance(ref, dict) or not ref.get("ref") or not ref.get("sha256"):
            return {"status": "unavailable", "reason": "unbound_citation"}
        matches = [row for row in offered.get((ref.get("ref"), ref.get("sha256")), [])
                   if expected_source is None or expected_source == row["source_id"]]
        if len(matches) != 1:
            return {"status": "unavailable", "reason": "missing_or_ambiguous_oa_source"}
        item = matches[0]
        acl = _intersect_access_scopes(item.get("access_scope"), ref.get("access_scope"))
        permitted = _permitted_paths(item.get("allowed_paths") or ["$"], ref.get("allowed_paths") or ["$"])
        origin_refs = origins.get((item["ref"], item["sha256"]), [])
        if not origin_refs:
            # This sidecar is created by the kernel with project scope, directly
            # from the same failure. Only its native envelope authenticates it.
            if (item["source_id"] != "semantic-failure-input" or item.get("schema_version") != "semantic-failure-input.v1"
                    or not item["ref"].startswith("artifacts/orchestrator-agent/semantic-failures/")):
                return {"status": "unavailable", "reason": "unproven_original_scope"}
            failure = hydrate_sidecar_ref(state, item).payload
            verify = failure.get("candidate_verify") or {}
            if (failure.get("workflow_run_id") != decision["identity"]["workflow_run_id"]
                    or failure.get("request_event_id") != source_event_id
                    or verify.get("failure_event_id") != context["rework_of"]
                    or verify.get("candidate_event_id") != bound["candidate_event_id"]):
                raise ValueError("Candidate diagnosis semantic failure input differs from its aggregate")
        for original in origin_refs:
            if original.get("access_scope") != acl:
                acl = _intersect_access_scopes(acl, original.get("access_scope"))
            permitted = _permitted_paths(permitted, original.get("allowed_paths") or ["$"])
        if not permitted or not artifact_access_allowed(acl, **identity):
            return {"status": "unavailable", "reason": "outside_consumer_scope"}
        source = {**item, "source_id": "replan-evidence-" + item["source_id"], "access_scope": acl,
                  "allowed_paths": permitted}
        for baseline in baseline_sources:
            if ((baseline["ref"], baseline["sha256"]) == (source["ref"], source["sha256"])
                    and (baseline.get("access_scope") or {}) == acl and baseline.get("allowed_paths") == permitted):
                return {"status": "available", "source_id": baseline["source_id"]}
        previous = selected.get(source["source_id"])
        if previous is not None and previous != source:
            raise ValueError("Candidate diagnosis evidence has conflicting occurrence scopes")
        if previous is None:
            if origin_refs:
                hydrate_sidecar_ref(state, source)
            selected[source["source_id"]] = source
        return {"status": "available", "source_id": source["source_id"]}

    basis = [index(ref) for ref in directive.get("basis_refs", [])]
    members = {row["operation_id"]: row for row in bound["members"]}
    findings = {f["finding_id"]: f for f in bound["findings"]}
    reports = []
    for obligation in context["rework_summary"]["candidate_obligations"]:
        finding = findings.get(obligation["finding_id"])
        if not finding or any(finding[k] != obligation.get(k) for k in finding):
            raise ValueError("Candidate diagnosis finding differs from its admitted aggregate")
        member = members.get(finding["operation_id"])
        ref = finding.get("control_result_ref")
        evidence = (index(ref, "candidate-verification-" + member["child_id"])
                    if ref and member and member["control_result_ref"] == ref
                    else {"status": "unavailable", "reason": "no_admitted_reader_report"})
        reports.append({"finding_id": finding["finding_id"], **evidence})
    return list(selected.values()), {"candidate_event_id": bound["candidate_event_id"], "failure_event_id": context["rework_of"],
                                    "basis": basis, "reader_findings": reports}
