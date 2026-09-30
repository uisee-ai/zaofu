"""General route reaches a Kernel terminal without manual completion helpers."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tests.e2e.scripts.four_flow_e2e_support import summarize_flow_run
from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery
from tests.testkit.general_route import GeneralRoute
from tests.testkit.issue_route import git, isolate_issue_processes
from zf.runtime.result_submit import ResultSubmitError
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


@pytest.fixture
def general_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    route = GeneralRoute(tmp_path, monkeypatch)
    try:
        yield route
    finally:
        assert not forbidden
        assert git(tmp_path, "rev-parse", "HEAD") == route.base
        assert not git(tmp_path, "diff", "HEAD", "--", "README.md")
        ledger = (route.state / "events.jsonl").read_text()
        assert all(token not in ledger for token in route.credentials.values())


@pytest.mark.parametrize("reopen_at", ["none", "reader_fact", "verify_fact", "terminal_replay"])
def test_general_route_terminal_delivery(general_route: GeneralRoute, reopen_at: str) -> None:
    route = general_route
    with route.evidence.guard("five_stage_terminal_delivery", {"reopen_at": reopen_at}):
        route.complete_reader(route.start())
        if reopen_at == "reader_fact":
            route.reopen()
        route.complete_reader(route.dispatch("collect-1"))
        for _ in range(4):
            route.tick()
            if route.events("collect-1.completed"):
                break
        assert route.events("collect-1.completed")
        assert not any(row.payload["stage_id"] == "synthesize" for row in route.events("fanout.child.dispatched"))
        route.complete_reader(route.dispatch("collect-2"))
        route.complete_reader(route.dispatch("synthesize"))
        assert not route.events("run.goal.completed")
        route.complete_verify(route.dispatch("verify"))
        if reopen_at == "verify_fact":
            route.reopen()
        for _ in range(12):
            route.tick()
            if route.events("run.goal.completed"):
                break
        [terminal] = route.events("run.goal.completed")
        [verified] = route.events("artifact.delivery.verified")
        [claim] = route.events("run.goal.completion.claimed")
        assert terminal.origin == "kernel"
        assert verified.origin == "kernel"
        assert terminal.payload["verification_event_id"] == verified.id
        assert terminal.payload["verification_admitted_call_result_ref"] == verified.payload["admitted_call_result_ref"]
        assert terminal.payload["claim_event_id"] == claim.id
        assert len(route.events("fanout.child.dispatched")) == 5
        assert len(route.events("workflow.invoke.requested")) == 1
        admissions = route.events("workflow.call.result.admitted")
        for event in admissions:
            for key in ("envelope_ref", "control_result_ref"):
                ref = event.payload[key]
                route.body(ref["ref"], ref["sha256"])
        assert not route.events("candidate.ready")  # Artifact delivery is not a code candidate.
        run_standard_tick_services(route.runtime, state=TickServiceState(), now=1.0)
        observation = summarize_flow_run(route.log.read_all(), route.invoke)
        assert observation["admission_status"] == "admitted", observation
        assert observation["terminal_status"] == "completed", observation
        delivery = audit_terminal_delivery(route.state)
        assert delivery["status"] == "passed", delivery
        assert delivery["terminal"]["run_id"] == observation["run_id"]
        assert delivery["terminal"]["event_id"] == terminal.id == observation["terminal_event_id"]
        [owner] = [row for row in route.events("owner.visible_message.requested")
                   if row.payload.get("terminal_event_id") == terminal.id]
        persisted = {}
        for key in ("dossier_ref", "completion_receipt_ref"):
            ref = owner.payload[key]
            assert ref == delivery["delivery"][key]
            path = (route.state / ref).resolve()
            assert path.is_relative_to(route.state)
            persisted[path] = path.read_bytes()
            document = json.loads(persisted[path])
            assert document["terminal"]["event_id"] == terminal.id
            assert document["run_id" if key == "dossier_ref" else "workflow_run_id"] == observation["run_id"]
            assert document["source_fingerprint"] == owner.payload[
                "dossier_source_fingerprint" if key == "dossier_ref" else "completion_receipt_fingerprint"
            ]
        receipt = json.loads(persisted[route.state / owner.payload["completion_receipt_ref"]])
        [artifact] = receipt["artifact_delivery"]["required_artifacts"]
        artifact_bytes = (route.state / artifact["ref"]).read_bytes()
        assert hashlib.sha256(artifact_bytes).hexdigest() == artifact["sha256"]
        assert artifact["source_ref"] == "synthesize.report"
        kinds = {"workflow.invoke.requested", "fanout.child.dispatched", "workflow.call.result.admitted",
                 "artifact.delivery.verified", "run.goal.completion.claimed", "run.goal.completed", "owner.visible_message.requested"}
        before = [row.to_json() for row in route.log.read_all() if row.type in kinds]
        if reopen_at == "terminal_replay":
            route.reopen()
        for _ in range(3):
            route.tick()
            run_standard_tick_services(route.runtime, state=TickServiceState(), now=1.0)
        assert [row.to_json() for row in route.log.read_all() if row.type in kinds] == before
        assert {path: path.read_bytes() for path in persisted} == persisted
        assert audit_terminal_delivery(route.state)["status"] == "passed"


@pytest.mark.parametrize("projection", ["not_projected", "newer_attempt"])
def test_general_reads_exact_dispatch_before_manifest_projection(
    general_route: GeneralRoute, monkeypatch: pytest.MonkeyPatch, projection: str,
) -> None:
    route = general_route
    dispatch = route.start()
    original = deepcopy(dispatch.payload)
    path = route.state / "fanouts" / dispatch.payload["fanout_id"] / "manifest.json"
    manifest = json.loads(path.read_text())
    child = next(row for row in manifest["children"]
                 if row["child_id"] == dispatch.payload["child_id"])
    if projection == "not_projected":
        child.pop("attempt_source_manifest_ref")
        child.pop("attempt_source_manifest_digest")
    else:
        child["attempt_source_manifest_ref"] = "artifacts/wrong-attempt.json"
        child["payload"]["operation_id"] = "wrong-operation"
    read_text = Path.read_text

    def projected_read(current: Path, *args, **kwargs):
        return json.dumps(manifest) if current == path else read_text(current, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", projected_read)
    received, payload = route.result(dispatch)
    assert received["role_instance"] == dispatch.payload["role_instance"]
    assert payload["operation_id"] == original["payload"]["operation_id"]
    assert payload["input_consumption_status"] == "satisfied"
    assert dispatch.payload == original


def test_general_verifier_missing_reads_cannot_complete(general_route: GeneralRoute) -> None:
    route = general_route
    with route.evidence.guard("verifier_missing_reads", {}):
        route.complete_reader(route.start())
        for stage in ("collect-1", "collect-2", "synthesize"):
            route.complete_reader(route.dispatch(stage))
        verifier = route.dispatch("verify")
        before = len(route.events("workflow.call.result.admitted"))
        with pytest.raises(ResultSubmitError) as rejected:
            route.complete_verify(verifier, read_inputs=False)
        assert rejected.value.code == "result_not_admitted"
        assert any("read" in item["code"] for item in rejected.value.details["issues"])
        assert len(route.events("workflow.call.result.admitted")) == before
        for _ in range(3):
            route.tick()
            run_standard_tick_services(route.runtime, state=TickServiceState(), now=1.0)
        assert not route.events("artifact.delivery.verified")
        assert not route.events("run.goal.completed")
        assert not route.events("owner.visible_message.requested")
