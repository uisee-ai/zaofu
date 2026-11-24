"""Index authorized successor feedback from the exact aggregate Candidate's retained findings."""
from collections import defaultdict

from zf.runtime.artifact_access import ArtifactAccessError, require_artifact_access
from zf.runtime.orchestrator_agent_role import configured_orchestrator_role
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import operation_request_hash
from zf.runtime.workflow_operation_task_pipeline import task_pipeline_request_hash_body


def aggregate_prior_repair_feedback(runtime, events, run, candidate):
    """The freeze supplies the replacement and Verify event IDs; never look up a Task by name alone."""
    freeze = next((e for e in events if e.id == candidate["candidate_event_id"]), None)
    replacements = freeze.payload.get("completed_task_replacements") if freeze else None
    role = configured_orchestrator_role(runtime)
    if not replacements or role is None:
        return [], []

    def read(ref):
        require_artifact_access(ref.get("access_scope"), actor=role.instance_id, role=role.name,
                                purpose="orchestrator-agent:semantic_failure")
        return hydrate_sidecar_ref(runtime.state_dir, ref).payload

    retained, contracts = defaultdict(dict), set()
    for member in candidate["members"]:
        try:
            result = read(member["control_result_ref"])
        except ArtifactAccessError:
            continue
        digest = result["contract_snapshot_digest"]
        if digest in contracts:
            continue
        contracts.add(digest)
        contract = read({"ref": result["contract_snapshot_ref"], "sha256": digest})
        for row in contract.get("candidate_recovery_obligations", []):
            for parent in row["task_ids"]:
                if parent in replacements:
                    retained[parent][row["finding_id"]] = row
    if not retained:
        return [], []
    rows, requests = {}, {}
    for event in events:
        if event.id == freeze.id:
            break
        if (event.payload.get("workflow_run_id") or event.correlation_id) != run:
            continue
        if event.type in {"task.pipeline.generation.admitted", "task.pipeline.verify.completed",
                          "task_map.ready", "orchestrator.semantic.decision.admitted"}:
            rows[event.id] = event
        elif event.type == "workflow.operation.requested" and event.origin == "kernel":
            requests[event.payload.get("operation_id"), event.payload.get("request_hash")] = event

    sources, index = {}, []
    for parent, obligations in retained.items():
        for link in replacements[parent]:
            for task in link["edges"].get(parent, []):
                verify = rows.get(link["verification_event_ids"].get(task))
                generation = rows.get(link["generation_event_id"])
                if not verify or not generation:
                    continue
                ready = rows.get(generation.payload.get("trigger_event_id"))
                if (not ready or ready.origin != "kernel" or ready.type != "task_map.ready"
                        or not str(ready.payload.get("resume_checkpoint_ref", "")).startswith("oa-candidate-repair-")):
                    continue
                same = {row["finding_id"]: row for row in (ready.payload.get("rework_summary") or {})
                        .get("candidate_obligations", []) if obligations.get(row["finding_id"]) == row}
                if not same:
                    continue
                entry = {"predecessor_task_id": parent, "successor_task_id": task,
                         "ready_event_id": ready.id, "verification_event_id": verify.id}
                try:
                    ref, finding_ids = _feedback(read, run, freeze.payload["pdd_id"], parent, task, same,
                                                 link, generation, ready, verify, requests, rows)
                except (KeyError, ValueError, OSError, TypeError):
                    index.append({**entry, "status": "unavailable", "finding_ids": sorted(same)})
                    continue
                source_id = "candidate-prior-repair-" + ref["sha256"]
                sources[source_id] = {**ref, "source_id": source_id}
                index.append({**entry, "status": "available", "source_id": source_id, "finding_ids": finding_ids,
                              "decision_admission_event_id": ref["source_event_id"]})
    return list(sources.values()), index


def _feedback(read, run, anchor, parent, task, same, link, generation, ready, verify, requests, rows):
    v, g, r = verify.payload, generation.payload, ready.payload
    if (generation.type != "task.pipeline.generation.admitted" or generation.origin != "kernel"
            or g["pdd_id"] != anchor or g["plan_artifact_package_digest"] != link["package_digest"]
            or r["pdd_id"] != anchor or r["task_map_generation"] != g["task_map_generation"]
            or verify.type != "task.pipeline.verify.completed" or verify.origin not in {"", "kernel"}
            or v["parent_task_id"] != anchor or v["task_id"] != task or v["output_profile_id"] != "task-verify"
            or v["task_map_generation"] != g["task_map_generation"]
            or v["plan_artifact_package_digest"] != link["package_digest"]):
        raise ValueError("replacement Verify target mismatch")
    request = requests[v["operation_id"], v["request_hash"]]
    envelope = read(request.payload["request_ref"])
    if (operation_request_hash(task_pipeline_request_hash_body(envelope, envelope["request"])) != v["request_hash"]
            or envelope["operation_id"] != v["operation_id"]
            or envelope["task_id"] != task or envelope["workflow_run_id"] != run
            or envelope["request"]["task_pipeline_stage"] != "verify"):
        raise ValueError("replacement Verify request mismatch")
    ref = envelope["request"]["candidate_repair_feedback_ref"]
    feedback = read(ref)
    admitted = rows[ref["source_event_id"]]
    a = admitted.payload
    if (feedback["schema_version"] != "candidate-repair-feedback.v1" or feedback["workflow_run_id"] != run
            or feedback["ready_event_id"] != ready.id or feedback["predecessor_task_id"] != parent
            or feedback["task_id"] != task or feedback["task_map_generation"] != g["task_map_generation"]
            or feedback["candidate_commit"] != r["source_commit"]
            or admitted.type != "orchestrator.semantic.decision.admitted" or admitted.origin != "kernel"
            or a["status"] != "admitted" or a["decision"] != "rework" or a["checkpoint"] != "semantic_failure"
            or a["checkpoint_policy"] != "blocking" or a["decision_ref"] != feedback["decision_ref"]
            or r["resume_checkpoint_ref"] != "oa-candidate-repair-" + a["operation_id"]):
        raise ValueError("replacement feedback authority mismatch")
    decision = read(a["decision_ref"])
    directive, = [d for d in decision["delta"]["directives"] if any(
        gap["task_id"] == task and gap.get("supersedes_task_ids") == [parent]
        for gap in (d.get("candidate_repair") or {}).get("gap_tasks", []))]
    repair = directive["candidate_repair"]
    if (decision["identity"]["workflow_run_id"] != run or decision["identity"]["operation_id"] != a["operation_id"]
            or repair.get("contract_changed") is not False
            or ("rationale" in feedback or "required_actions" in feedback) and (
                len(repair["gap_tasks"]) != 1 or feedback.get("rationale") != repair["rationale"]
                or feedback.get("required_actions") != directive["required_actions"])):
        raise ValueError("replacement feedback no longer matches the admitted repair scope")
    finding_ids = []
    for finding in feedback["findings"]:
        identifier = finding["finding_id"]
        original = same[identifier]
        if (original["task_ids"] != [parent] or not original["product_repair_eligible"]
                or any(finding[k] != original[k] for k in ("control_result_ref", "pointer", "evidence", "disposition"))):
            raise ValueError("replacement feedback finding mismatch")
        read(finding["control_result_ref"])
        finding_ids.append(identifier)
    if not finding_ids:
        raise ValueError("empty replacement feedback")
    return ref, sorted(finding_ids)
