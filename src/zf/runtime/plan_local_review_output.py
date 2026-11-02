"""Bind a locally reviewed result to the exact candidate before admission."""
from __future__ import annotations

from typing import Any, Mapping

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_operation import load_workflow_operation


def _scoped_policy(runtime: Any, operation: Mapping[str, Any] | None) -> bool:
    if not operation:
        return False
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload["request"]
    policy_ref = request.get("input_consumption_policy_ref")
    if not isinstance(policy_ref, Mapping) or not policy_ref.get("ref"):
        return False
    policy = hydrate_sidecar_ref(runtime.state_dir, dict(policy_ref)).payload
    rows = policy.get("required_reads") or []
    scoped = {str(row.get("source_id") or "") for row in rows if isinstance(row, Mapping)
              and str(row.get("source_id") or "").startswith("child-artifact-")
              and str(row.get("json_path") or "").startswith("$.tasks[")}
    full = {str(row.get("source_id") or "") for row in rows
            if isinstance(row, Mapping) and row.get("json_path") == "$"}
    return bool(scoped - full)


def validate_local_review_output(runtime: Any, *, event: ZfEvent, payload: Mapping[str, Any]) -> None:
    """Leave ordinary reviews unchanged; reject scoped output identity drift."""
    event_body = event.payload if isinstance(event.payload, Mapping) else {}
    operation_id = str(event_body.get("operation_id") or "")
    operation = load_workflow_operation(runtime.event_log, operation_id) if operation_id else None
    fanout_id = str(event_body.get("fanout_id") or "")
    if fanout_id and not _scoped_policy(runtime, operation):
        dispatches = [item for item in runtime.event_log.read_all()
                      if item.type == "fanout.synth.dispatched" and item.origin == "kernel"
                      and item.payload.get("fanout_id") == fanout_id and item.payload.get("operation_id")]
        if dispatches:
            dispatched = load_workflow_operation(runtime.event_log, str(dispatches[-1].payload["operation_id"]))
            if _scoped_policy(runtime, dispatched):
                raise ValueError("local review output is missing its dispatched operation identity")
    if not operation:
        return
    if event_body.get("request_hash") != operation.get("request_hash"):
        raise ValueError("local review operation request identity differs")
    request = hydrate_sidecar_ref(runtime.state_dir, operation["request_ref"]).payload["request"]
    policy_ref = request.get("input_consumption_policy_ref")
    if not isinstance(policy_ref, Mapping) or not policy_ref.get("ref"):
        return
    policy = hydrate_sidecar_ref(runtime.state_dir, dict(policy_ref)).payload
    rows = policy.get("required_reads") or []
    full = {str(row.get("source_id") or "") for row in rows
            if isinstance(row, Mapping) and row.get("json_path") == "$"}
    if not _scoped_policy(runtime, operation):
        return
    if (request.get("fanout_id") != fanout_id
            or request.get("workflow_run_id") != event_body.get("workflow_run_id")):
        raise ValueError("local review result does not match the dispatched fanout")
    if (policy_ref.get("sha256") != request.get("read_policy_digest")
            or policy.get("required_reads") != request.get("required_reads")
            or policy.get("attempt_id") != request.get("active_attempt_id")):
        raise ValueError("local review policy does not match the frozen request")
    from zf.runtime.call_result_runtime import _has_semantic_submit_provenance

    events = runtime.event_log.read_all()
    admitted = any(item.type == "workflow.call.result.admitted" and item.actor == "zf-cli"
                   and item.payload.get("source_event_id") == event.id
                   and item.payload.get("operation_id") == operation_id
                   and item.payload.get("request_hash") == event_body.get("request_hash") for item in events)
    if not admitted and not _has_semantic_submit_provenance(runtime, event, operation_request=request):
        raise ValueError("local review result was not admitted")
    from zf.runtime.artifact_read_ledger import _safe_component

    digest = str(request.get("source_manifest_digest") or "")
    attempt = _safe_component(str(request.get("active_attempt_id") or ""))
    supplied = event_body.get("attempt_source_manifest")
    if not digest or (isinstance(supplied, Mapping) and supplied.get("sha256") != digest):
        raise ValueError("local review source manifest does not match the frozen request")
    manifest = hydrate_sidecar_ref(runtime.state_dir, {
        "ref": f"artifacts/attempts/{attempt}/source-manifests/{digest}.json", "sha256": digest,
    }).payload
    if (manifest.get("attempt_id") != request.get("active_attempt_id")
            or manifest.get("workflow_run_id") != event_body.get("workflow_run_id")):
        raise ValueError("local review source manifest identity differs")
    sources = [source for source in manifest.get("sources", []) if isinstance(source, Mapping)]

    def one(predicate):
        found = [source for source in sources if predicate(source)]
        if len(found) != 1:
            raise ValueError("local review candidate source is missing or ambiguous")
        return found[0]

    cert_source = one(lambda source: source.get("artifact_id") == "scoped_plan_review.json"
                      and source.get("source_id") in full)
    report_source = one(lambda source: str(source.get("source_id") or "").startswith("child-result-"))
    cert = hydrate_sidecar_ref(runtime.state_dir, dict(cert_source)).payload
    report = hydrate_sidecar_ref(runtime.state_dir, dict(report_source)).payload
    from zf.runtime.plan_artifact_package import reduce_plan_artifact_packages
    from zf.runtime.plan_requirement_amendment import amendment_binding

    run = str(event_body.get("workflow_run_id") or "")
    current = reduce_plan_artifact_packages(events, workflow_run_id=run).get("current") or {}
    if current.get("package_digest") != cert.get("baseline_package_sha256") or amendment_binding(
        runtime.state_dir, run, events=events,
    ):
        raise ValueError("local review baseline or requirement authority changed")
    task_source = one(lambda source: source.get("ref") == report.get("task_map_ref")
                      and str(source.get("source_id") or "").startswith("child-artifact-"))
    if cert.get("candidate_task_map_sha256") != task_source.get("sha256"):
        raise ValueError("local review candidate task map differs from its certificate")
    expected = {str(row.get("logical_name") or ""): str(row.get("sha256") or "")
                for row in report.get("plan_ports", []) if isinstance(row, Mapping)}
    if not expected or "" in expected or any(not digest for digest in expected.values()):
        raise ValueError("local review candidate Plan ports are invalid")
    expected["task_map"] = str(task_source["sha256"])
    from zf.runtime.plan_artifact_package_ports import materialize_explicit_plan_ports

    actual = materialize_explicit_plan_ports(
        state_dir=runtime.state_dir, project_root=runtime.project_root, payload=payload,
        flow_kind=str(payload.get("flow_kind") or ""), required_ports=expected,
    )
    actual_digests = {name: str(port.get("sha256") or "") for name, port in actual.items()
                      if name != "goal_claim_set"}
    if "planning_result" not in expected and actual_digests.get("planning_result") == expected["task_map"]:
        actual_digests.pop("planning_result")
    if actual_digests != expected:
        raise ValueError("local review output does not match the reviewed Task Map and Plan ports")
    if payload.get("plan_md") not in (None, "", report.get("plan_md")):
        raise ValueError("local review output changes the reviewed Plan text")
    if payload.get("refactor_plan_md") not in (None, "", report.get("refactor_plan_md"), report.get("plan_md")):
        raise ValueError("local review output changes the reviewed Plan text")
    plan_ref = str(payload.get("plan_artifact_ref") or "")
    if plan_ref:
        from zf.runtime.artifact_refs import runtime_artifact_sha256

        old_ref = str(report.get("plan_artifact_ref") or "")
        expected_source = one(lambda source: source.get("ref") == old_ref
                              and str(source.get("source_id") or "").startswith("child-artifact-"))
        if runtime_artifact_sha256(plan_ref, state_dir=runtime.state_dir, project_root=runtime.project_root) != expected_source["sha256"]:
            raise ValueError("local review output changes the reviewed Plan artifact")
