from pathlib import Path

import pytest
from zf.core.events import ZfEvent
from zf.runtime.workflow_resume import build_workflow_batch_resume_checkpoints


def failure_events():
    ref = {"ref": "artifacts/result.json", "sha256": "a" * 64, "schema_version": "verification-result.v1"}
    identity = {"operation_id": "op-reader", "request_hash": "request", "workflow_run_id": "workflow-1"}
    return [
        ZfEvent(type="candidate.ready", id="candidate", correlation_id="workflow-1", payload={"pdd_id": "PRD", "candidate_ref": "candidate/PRD", "candidate_head_commit": "a" * 40}),
        ZfEvent(type="fanout.started", id="started", correlation_id="workflow-1", payload={"fanout_id": "f", "stage_id": "verify", "topology": "fanout_reader", "trigger_event_id": "candidate", "pdd_id": "PRD"}),
        ZfEvent(type="workflow.call.result.admitted", id="admitted", origin="kernel", payload={**identity, "control_result_schema": "verification-result.v1", "semantic_verdict": "abstained", "source_event_id": "child", "control_result_ref": ref}),
        ZfEvent(type="verify.child.failed", id="child", payload={**identity, "fanout_id": "f", "output_profile_id": "candidate-verify", "semantic_submit_admission_event_id": "admitted", "control_result_ref": ref}),
        ZfEvent(type="fanout.aggregate.completed", id="aggregate", correlation_id="workflow-1", payload={"fanout_id": "f", "stage_id": "verify", "pdd_id": "PRD", "status": "failed", "failure_kind": "infra", "failure_event": "test.failed", "failed_children": ["verify"]}),
        ZfEvent(type="test.failed", id="failed", correlation_id="workflow-1", payload={"fanout_id": "f", "stage_id": "verify", "status": "failed", "failure_kind": "infra"}),
    ]


def test_admitted_reader_abstention_keeps_its_recovery_owner(tmp_path: Path):
    checkpoints = build_workflow_batch_resume_checkpoints(tmp_path, events=failure_events())
    assert not [c for c in checkpoints if c.safe_resume_action == "reemit_candidate_ready"]


@pytest.mark.parametrize("mismatch", ["missing", "origin", "operation_id", "request_hash", "workflow_run_id", "control_result_ref", "source_event_id"])
def test_unbound_admission_does_not_override_legacy_runtime_retry(tmp_path, mismatch):
    events = failure_events()
    if mismatch == "missing":
        events.pop(2)
    elif mismatch == "origin":
        events[2].origin = "agent"
    else:
        events[2].payload[mismatch] = "different"
    checkpoints = build_workflow_batch_resume_checkpoints(tmp_path, events=events)
    assert any(c.safe_resume_action == "reemit_candidate_ready" for c in checkpoints)


def test_mixed_reader_publication_reports_exact_current_results(tmp_path):
    from types import SimpleNamespace
    from zf.runtime.fanout_failure_findings import fanout_failure_context
    from zf.runtime.reader_aggregate_publication import publication_payload

    _, _, admission, source, _, _ = failure_events()
    source.payload.update(child_id="verify-0", attempt_id="attempt-0")
    admission.payload["attempt_id"] = "attempt-0"
    terminal = ZfEvent(type="fanout.child.failed", id="terminal-0", causation_id=source.id,
        origin="kernel", payload={**source.payload, "semantic_verdict": "abstained"})
    missing = ZfEvent(type="fanout.child.failed", id="terminal-1", origin="kernel",
        payload={"fanout_id": "f", "child_id": "verify-1", "failure_class": "worker_noop_or_terminal_missing"})
    events = [admission, source, terminal, missing]
    manifest = {"workflow_run_id": "workflow-1", "fanout_id": "f", "topology": "fanout_reader",
                "children": [{"child_id": row.payload["child_id"], "last_event_id": row.id,
                              "status": "failed", "payload": {"output_profile_id": "candidate-verify"}}
                             for row in (terminal, missing)]}
    owner = SimpleNamespace(event_log=SimpleNamespace(read_all=lambda: events), _fanout_child_payloads=lambda _: [])
    findings, recovery = fanout_failure_context(owner, manifest, failed=True)
    publication = publication_payload(manifest=manifest, artifact_payload={}, final_status="failed",
        publish_event="test.failed", rebuild_request=None, rebuild_of=None, recovery_generation=0,
        failure_recovery=recovery, failure_findings=findings, aggregate_payload={"failed_children": ["verify-0", "verify-1"]})
    assert publication["reader_outcomes"] == {"abstained": {"verify-0": "terminal-0"},
                                              "without_result": {"verify-1": "terminal-1"}}
    assert "abstained=1, without_result=1" in publication["reason"]
    admission.payload["request_hash"] = "wrong-attempt"
    assert fanout_failure_context(owner, manifest, failed=True)[1]["reader_outcomes"] == {
        "unconfirmed": {"verify-0": "terminal-0"}, "without_result": {"verify-1": "terminal-1"}}
