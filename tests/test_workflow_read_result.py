from zf.core.events.model import ZfEvent
from zf.runtime.workflow_read_result import normalize_workflow_read_result
import pytest


@pytest.mark.parametrize("recommendation,expected", [
    ("abstain", "abstained"), ("blocked", "blocked"),
    ("reject", "rejected"), ("needs_rework", "rejected"), ("", "blocked"),
])
def test_reader_unavailable_verdict_survives_repeated_normalization(recommendation, expected):
    report = {"status": "blocked", "recommendation": recommendation,
              "summary": "Reader report", "findings": []}
    if not recommendation:
        report.pop("recommendation")
    for _ in range(3):
        report, issues = normalize_workflow_read_result(ZfEvent(
            type="workflow.child.completed", payload={"report": report}))
        assert issues == []
        assert report["verdict"] == expected


def test_non_artifact_reader_preserves_typed_execution_failure():
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.child.completed", payload={"report": {
            "status": "blocked", "recommendation": "abstain",
            "failure_class": "reader_execution_failure", "summary": "Probe unavailable",
        }}))
    assert issues == []
    assert result["execution_status"] == "failed"
    assert result["failure_class"] == "reader_execution_failure"
    assert result["verdict"] == "abstained"


def test_reader_rejects_unknown_recommendation_enum():
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.child.completed", payload={"report": {
            "status": "failed", "recommendation": "rework",
            "summary": "Invalid recommendation", "findings": [],
        }},
    ))

    assert result["recommendation"] == "rework"
    assert any(
        issue["field"] == "control_result.recommendation"
        and issue["code"] == "schema_invalid"
        for issue in issues
    )


@pytest.mark.parametrize("raw,verdict,failure_class,vote", [
    ({"status": "blocked"}, "blocked", "dependency_blocked", "abstain"),
    ({"status": "blocked", "recommendation": "block"}, "blocked", "dependency_blocked", "abstain"),
    ({"status": "failed", "recommendation": "blocked"}, "blocked", "dependency_blocked", "abstain"),
    ({"status": "blocked", "recommendation": "abstain", "failure_class": "dependency_blocked"},
     "blocked", "dependency_blocked", "abstain"),
    ({"status": "blocked", "recommendation": "abstain"}, "abstained", "reader_abstained", "abstain"),
    ({"status": "failed", "recommendation": "abstained"}, "abstained", "reader_abstained", "abstain"),
    ({"status": "failed", "recommendation": "rejected"}, "rejected", "semantic_rejection", "reject"),
])
def test_semantic_submit_preserves_reader_outcome_through_fanout(tmp_path, raw, verdict, failure_class, vote):
    from tests.test_result_submit import _running_workflow_read_operation
    from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
    from zf.runtime.fanout_evidence_queries import FanoutEvidenceQueriesMixin

    runtime, prepared, service, token = _running_workflow_read_operation(tmp_path)
    submitted = service.submit(operation_id=prepared.operation_id,
        semantic_result={**raw, "summary": "Reader outcome", "findings": []},
        role_instance="scan-verification", credential=token)
    event = next(e for e in runtime.event_log.read_all() if e.id == submitted.canonical_event_id)
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, event)
    result = hydrated.payload["report"]
    assert (result["verdict"], result["failure_class"], result["recommendation"]) == (verdict, failure_class, vote)
    projected = FanoutEvidenceQueriesMixin._fanout_child_report(runtime, child_id="scan-verification",
                                                               event=hydrated, success=False)
    assert projected.valid, projected.diagnostics
    assert projected.report["recommendation"] == vote
    repeated, issues = normalize_workflow_read_result(ZfEvent(type=event.type, payload={"report": projected.report}))
    assert not issues
    assert (repeated["verdict"], repeated["failure_class"]) == (verdict, failure_class)


def test_artifact_production_findings_are_output_not_execution_failure() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.child.failed",
        payload={
            "result_semantics": "artifact_production",
            "summary": "Scan completed with blocking findings.",
            "findings": [{"severity": "high", "message": "gap"}],
            "recommendation": "needs_rework",
        },
    ))

    assert issues == []
    assert result["execution_status"] == "completed"
    assert result["verdict"] == "passed"
    assert result["subject_verdict"] == "needs_rework"


def test_artifact_production_preserves_explicit_transport_failure() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="workflow.child.failed",
        payload={
            "result_semantics": "artifact_production",
            "summary": "Provider stream stopped.",
            "transport_error": "broken pipe",
        },
    ))

    assert issues == []
    assert result["execution_status"] == "failed"
    assert result["verdict"] == "abstained"
    assert result["failure_class"] == "reader_execution_failure"


def test_structured_artifact_producer_can_report_incomplete_outputs() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="prd.plan.child.failed",
        payload={"report": {
            "result_semantics": "artifact_production",
            "execution_status": "failed",
            "summary": "Draft saved; required task map could not be produced.",
            "artifact_refs": ["artifacts/plan/draft.json"],
            "recommendation": "needs_rework",
        }},
    ))
    assert issues == []
    assert result["execution_status"] == "failed"
    assert result["verdict"] == "abstained"


@pytest.mark.parametrize("profile,expected_valid", [("workflow-read", True), ("task-verify", False)])
def test_legacy_reader_vote_projection_respects_pinned_profile(profile, expected_valid):
    from zf.runtime.fanout_evidence_queries import FanoutEvidenceQueriesMixin

    report = {"status": "failed", "recommendation": "block", "summary": "Dependency unavailable"}
    event = ZfEvent(type="workflow.child.failed", payload={"output_profile_id": profile, "report": report})
    projected = FanoutEvidenceQueriesMixin()._fanout_child_report(child_id="reader", event=event, success=False)
    assert projected.valid is expected_valid
    if expected_valid:
        assert projected.report["subject_verdict"] == "blocked"
        assert projected.report["recommendation"] == "abstain"
    assert event.payload["report"]["recommendation"] == "block"


@pytest.mark.parametrize("status,vote,forged_subject,subject,canonical_vote", [
    ("passed", "approve", "rejected", "passed", "approve"),
    ("failed", "abstain", "blocked", "abstained", "abstain"),
    ("blocked", "block", "passed", "blocked", "abstain"),
])
def test_failed_producer_subject_survives_submit_live_fanout_and_replay(
    tmp_path, status, vote, forged_subject, subject, canonical_vote,
):
    from dataclasses import replace
    from tests.test_result_submit import _running_workflow_read_operation
    from zf.runtime.call_result_adapters import hydrate_profiled_control_result_event
    from zf.runtime.fanout import FanoutContext, FanoutManifestProjector
    from zf.runtime.fanout_evidence_queries import FanoutEvidenceQueriesMixin

    runtime, prepared, service, token = _running_workflow_read_operation(tmp_path)
    raw = {"result_semantics": "artifact_production", "execution_status": "failed", "status": status,
           "recommendation": vote, "subject_verdict": forged_subject, "summary": "Partial data"}
    unadmitted = ZfEvent(type="workflow.child.failed", payload={"report": raw})
    live = FanoutEvidenceQueriesMixin._fanout_child_report
    assert live(runtime, child_id="scan-verification", event=unadmitted, success=False).report["subject_verdict"] == subject

    submitted = service.submit(operation_id=prepared.operation_id, semantic_result=raw,
        role_instance="scan-verification", credential=token)
    event = next(e for e in runtime.event_log.read_all() if e.id == submitted.canonical_event_id)
    hydrated = hydrate_profiled_control_result_event(runtime.state_dir, event)
    admitted = hydrated.payload["report"]
    assert (admitted["verdict"], admitted["failure_class"], admitted["subject_verdict"]) == (
        "abstained", "reader_execution_failure", subject)
    # Inline data cannot override the signed control body even on an admitted event.
    tampered = replace(hydrated, payload={**hydrated.payload, "report": {**admitted, "subject_verdict": forged_subject}})
    projected = live(runtime, child_id="scan-verification", event=tampered, success=False)
    assert projected.valid
    assert (projected.report["subject_verdict"], projected.report["recommendation"]) == (subject, canonical_vote)

    context = FanoutContext.create(stage_id="scan", topology="fanout_reader", trace_id="run-scan",
        trigger_event_id="start", target_ref="ref", role_instances=["scan-verification", "unadmitted"])
    runtime.event_writer.append(context.started_event())
    runtime.event_writer.emit("fanout.child.failed", payload={
        "fanout_id": context.fanout_id, "child_id": "scan-verification", "control_result_schema": admitted["schema_version"],
        "admitted_call_result_ref": event.payload["call_result_envelope_ref"],
        "control_result_ref": event.payload["control_result_ref"], "report": admitted,
    })
    runtime.event_writer.append(ZfEvent(type="fanout.child.failed", origin="worker", payload={
        "fanout_id": context.fanout_id, "child_id": "unadmitted", "control_result_schema": admitted["schema_version"],
        "admitted_call_result_ref": event.payload["call_result_envelope_ref"],
        "control_result_ref": event.payload["control_result_ref"], "report": {**raw, "schema_version": admitted["schema_version"]},
    }))
    replayed = FanoutManifestProjector(runtime.state_dir).rebuild(context.fanout_id, runtime.event_log.read_all())
    children = {item["child_id"]: item["report"] for item in replayed["children"]}
    assert children["scan-verification"] == admitted
    assert children["unadmitted"]["subject_verdict"] == subject
