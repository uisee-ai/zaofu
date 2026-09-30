"""Repository triage Workflow through real ingress and independent verification."""

from pathlib import Path

import pytest

from tests.testkit.triage_route import TriageRoute
from zf.runtime.result_submit import ResultSubmitError


@pytest.fixture
def route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    scenario = TriageRoute(tmp_path, monkeypatch)
    try:
        yield scenario
    finally:
        scenario.assert_isolated()


@pytest.mark.parametrize("reopen_at", ["none", "reader_fact", "verify_pending", "verify_fact", "terminal"])
def test_triage_route_reaches_verified_report_and_outcome(route: TriageRoute, reopen_at: str) -> None:
    with route.evidence.guard("actual_triage_route", {"reopen_at": reopen_at}):
        reader = route.start()
        for _ in range(3):
            route.tick()
            route.ingress.poll_once()
        assert len(route.events("fanout.child.dispatched")) == 1
        assert not route.events("workflow.call.result.admitted")
        route.assert_no_delivery()
        route.complete_reader(reader)
        if reopen_at == "reader_fact":
            route.reopen()
        verifier = route.dispatch("external-issue-triage-verify")
        if reopen_at == "verify_pending":
            route.reopen()
        route.complete_verify(verifier)
        if reopen_at == "verify_fact":
            route.reopen()
        route.assert_settled()
        if reopen_at == "terminal":
            route.reopen()
        for _ in range(3):
            route.tick()
            route.ingress.poll_once()
        route.assert_settled()


def test_triage_wrong_credential_then_valid_submit_and_duplicate_rejection(route: TriageRoute) -> None:
    with route.evidence.guard("signed_submit_auth_and_replay", {}):
        reader = route.start()
        child, payload = route.reader_result(reader)
        before = len(route.log.read_all())
        with pytest.raises(ResultSubmitError) as invalid:
            route.emit_result(reader, child, payload, credential="wrong-public-fixture-token")
        assert invalid.value.code == "capability_invalid"
        assert len(route.log.read_all()) == before
        route.emit_result(reader, child, payload)
        before = len(route.log.read_all())
        with pytest.raises(ResultSubmitError) as duplicate:
            route.emit_result(reader, child, payload)
        assert duplicate.value.code == "duplicate_submit"
        assert len(route.log.read_all()) == before
        route.complete_verify(route.dispatch("external-issue-triage-verify"))
        route.assert_settled()


@pytest.mark.parametrize("mutation", ["missing_evidence", "wrong_artifact_digest", "foreign_evidence"])
def test_triage_verifier_invalid_result_cannot_close_run(route: TriageRoute, mutation: str) -> None:
    with route.evidence.guard("invalid_verifier_semantic_result", {"mutation": mutation}):
        route.complete_reader(route.start())
        verifier = route.dispatch("external-issue-triage-verify")
        child, payload = route.verifier_result(verifier)
        result = payload["artifact_delivery_result"]
        if mutation == "missing_evidence":
            result["verification_evidence_refs"] = []
        elif mutation == "wrong_artifact_digest":
            result["artifacts"][0]["sha256"] = "0" * 64
        else:
            result["verification_evidence_refs"] = ["artifacts/testkit/foreign-report.json"]
        before = len(route.events("workflow.call.result.admitted"))
        with pytest.raises(ResultSubmitError) as rejected:
            route.emit_result(verifier, child, payload)
        assert rejected.value.code in {"profile_adapter_invalid", "result_not_admitted"}
        assert len(route.events("workflow.call.result.admitted")) == before
        for _ in range(3):
            route.tick()
            route.ingress.poll_once()
        route.assert_no_delivery()


def test_triage_reader_placeholder_output_never_dispatches_verifier(route: TriageRoute) -> None:
    with route.evidence.guard("reader_missing_declared_output", {}):
        reader = route.start()
        child, payload = route.reader_result(reader)
        payload["report"]["outputs"] = {"report": "Replace with the final report."}
        with pytest.raises(ResultSubmitError) as rejected:
            route.emit_result(reader, child, payload)
        assert rejected.value.code in {"profile_adapter_invalid", "result_not_admitted"}
        for _ in range(3):
            route.tick()
            route.ingress.poll_once()
        assert len(route.events("fanout.child.dispatched")) == 1
        assert not route.events("workflow.call.result.admitted")
        route.assert_no_delivery()


@pytest.mark.parametrize("field", ["workflow_run_id", "request_revision"])
def test_triage_worker_cannot_override_signed_identity(route: TriageRoute, field: str) -> None:
    with route.evidence.guard("signed_identity_is_kernel_owned", {"field": field}):
        route.complete_reader(route.start())
        verifier = route.dispatch("external-issue-triage-verify")
        child, payload = route.verifier_result(verifier)
        expected = payload["artifact_delivery_result"][field]
        payload["artifact_delivery_result"][field] = "foreign-run" if field == "workflow_run_id" else expected - 1
        event = route.emit_result(verifier, child, payload)
        descriptor = event.payload["control_result_ref"]
        admitted = route.body(descriptor["ref"], descriptor["sha256"])
        assert admitted[field] == expected
        route.assert_settled()


def test_triage_verifier_without_signed_required_reads_cannot_complete(route: TriageRoute) -> None:
    with route.evidence.guard("missing_verifier_required_reads", {}):
        route.complete_reader(route.start())
        verifier = route.dispatch("external-issue-triage-verify")
        child, payload = route.verifier_result(verifier, read_inputs=False)
        assert child["input_consumption_policy"]["required_reads"]
        before = len(route.events("workflow.call.result.admitted"))
        with pytest.raises(ResultSubmitError) as rejected:
            route.emit_result(verifier, child, payload)
        assert rejected.value.code == "result_not_admitted"
        assert any("read" in issue["code"] for issue in rejected.value.details["issues"])
        assert len(route.events("workflow.call.result.admitted")) == before
        for _ in range(3):
            route.tick()
            route.ingress.poll_once()
        route.assert_no_delivery()


@pytest.mark.parametrize("verdict", ["blocked", "rejected"])
def test_triage_negative_verifier_verdict_never_becomes_success(route: TriageRoute, verdict: str) -> None:
    with route.evidence.guard("negative_verifier_verdict", {"verdict": verdict}):
        route.complete_reader(route.start())
        verifier = route.dispatch("external-issue-triage-verify")
        child, payload = route.verifier_result(verifier)
        result = payload["artifact_delivery_result"]
        result["verdict"] = verdict
        result["recommended_action"] = "hold" if verdict == "blocked" else "replan"
        result["goal_coverage"][0]["status"] = "blocked" if verdict == "blocked" else "open"
        result["open_gap_refs"] = [result["artifacts"][0]["ref"]]
        event = route.emit_result(verifier, child, payload)
        descriptor = event.payload["control_result_ref"]
        assert route.body(descriptor["ref"], descriptor["sha256"])["verdict"] == verdict
        for _ in range(3):
            route.tick()
            route.ingress.poll_once()
        route.assert_no_delivery()
        assert any(row.payload["semantic_verdict"] == verdict for row in route.events("workflow.call.result.admitted"))
