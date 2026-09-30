"""Official Refactor entry under catalog blocking/acceptance/ship policies."""

import json
from pathlib import Path

import pytest

from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery
from tests.testkit.issue_route import git, isolate_issue_processes
from tests.testkit.refactor_strong_route import StrongRefactorRoute
from zf.runtime.result_submit import ResultSubmitError
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


@pytest.fixture
def strong_refactor(tmp_path, monkeypatch):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    route = StrongRefactorRoute(tmp_path / "target", monkeypatch)
    with route.evidence.guard("strong_refactor", {}):
        yield route
        assert not forbidden
        assert (route.source_root / "answer.txt").read_text() == "fixed\n"
        assert set(route.source_root.iterdir()) == {route.source_root / "answer.txt"}
        ledger = (route.state / "events.jsonl").read_text()
        assert all(token not in ledger for token in route.credentials.values())


@pytest.mark.parametrize("reopen_at", ["none", "writer_fact", "verify_fact"])
def test_refactor_catalog_strong_policy_terminal_delivery(strong_refactor, reopen_at):
    route = strong_refactor
    route.complete_writer(route.plan_to_writer())
    if reopen_at == "writer_fact":
        route.reopen()
    route.complete_verify(route.dispatch("flow-lanes-verify"))
    if reopen_at == "verify_fact":
        route.reopen()
    _finish_and_audit(route)


def _finish_and_audit(route: StrongRefactorRoute) -> None:
    route.complete_reader(route.dispatch("flow-verify-bridge"))
    route.complete_reader(route.dispatch("flow-module-parity-scan"), extra={"open_p0_p1_gap_count": 0})
    route.complete_judge(route.dispatch("flow-final-judge"))
    for _ in range(12):
        route.tick()
        if route.events("run.goal.completed") and route.store.get(route.delivery_task_id).status == "done":
            break
    assert route.events("run.goal.completed"), [
        (e.type, e.payload) for e in route.log.read_all()
        if any(word in e.type for word in ("failed", "blocked", "delivery", "completion"))
    ]
    [terminal] = route.events("run.goal.completed")
    [delivery] = route.events("run.delivery.settled")
    [ship] = route.events("ship.completed")
    [requested] = route.events("run.delivery.requested")
    [package_event] = route.events("plan.artifact_package.admitted")
    assert package_event.payload["mode"] == "blocking"
    assert route.store.get(route.delivery_task_id).status == "done"
    payload = terminal.payload
    assert payload["delivery_policy"] == "ship_candidate"
    assert payload["delivery_status"] == "settled"
    assert payload["delivery_phase"] == "ship_delivered"
    assert payload["delivery_event_id"] == delivery.id
    assert ship.causation_id == delivery.causation_id == requested.id
    assert payload["run_id"] == delivery.payload["run_id"] == route.invoke.payload["workflow_run_id"]
    assert ship.payload["source_commit"] == delivery.payload["target_commit"] == payload["verified_target_commit"]
    assert ship.payload["target_branch"] == "main"
    assert ship.payload["original_head"] == route.base
    landed = git(route.root, "rev-parse", "main")
    assert landed == ship.payload["final_commit"] != route.base
    assert git(route.root, "rev-parse", ship.payload["final_tag"]) == landed
    assert git(route.root, "show", f"{landed}:answer.txt") == "fixed"
    assert git(route.root, "merge-base", "--is-ancestor", ship.payload["source_commit"], landed) == ""
    assert payload["product_acceptance_required"] is True
    assert payload["product_acceptance_verdict"] == "passed"
    spec = route.body(payload["product_acceptance_spec_ref"], payload["product_acceptance_spec_digest"])
    report = route.body(payload["product_acceptance_report_ref"], payload["product_acceptance_report_digest"])
    assert spec["workflow_run_id"] == report["workflow_run_id"] == payload["run_id"]
    assert spec["task_map_generation"] == report["task_map_generation"] == payload["task_map_generation"]
    assert report["target_commit"] == payload["target_commit"]
    assert report["candidate_ref"] == payload["candidate_ref"]
    assert report["product_acceptance_spec_digest"] == payload["product_acceptance_spec_digest"]
    [journey] = report["journey_results"]
    [assertion] = journey["assertion_results"]
    [probe_ref] = assertion["evidence_refs"]
    probe = route.body(probe_ref["ref"], probe_ref["sha256"])
    assert probe["target_commit"] == report["target_commit"]
    assert probe["command"] == route.command and probe["exit_code"] == 0
    assert probe["stdout"] == "issue regression passed\n"
    admitted = route.events("workflow.call.result.admitted")
    assert len(admitted) == 11
    signed_operations = set()
    for event in admitted:
        for key in ("envelope_ref", "control_result_ref"):
            descriptor = event.payload[key]
            route.body(descriptor["ref"], descriptor["sha256"])
        [canonical] = [row for row in route.log.read_all() if row.id == event.payload["source_event_id"]]
        if canonical.type == "fanout.aggregate.completed":
            assert canonical.origin == "kernel"
            assert canonical.payload["stage_id"] == "flow-scan"
        else:
            assert canonical.payload["semantic_submit_admission_event_id"] == event.id
            signed_operations.add(event.payload["operation_id"])
    assert len(signed_operations) == 10
    tick_state = TickServiceState()
    for _ in range(12):
        route.tick()
        run_standard_tick_services(route.runtime, state=tick_state, now=1.0)
        audit = audit_terminal_delivery(route.state)
        if audit["status"] != "pending":
            break
    assert audit["status"] == "passed", audit
    [owner] = [event for event in route.events("owner.visible_message.requested")
               if event.payload.get("terminal_event_id") == terminal.id]
    persisted: dict[Path, bytes] = {}
    for key in ("dossier_ref", "completion_receipt_ref"):
        path = (route.state / owner.payload[key]).resolve()
        assert path.is_relative_to(route.state)
        persisted[path] = path.read_bytes()
        document = json.loads(persisted[path])
        assert document["terminal"]["event_id"] == terminal.id
        assert document["run_id" if key == "dossier_ref" else "workflow_run_id"] == payload["run_id"]
    kinds = {"fanout.child.dispatched", "workflow.call.result.admitted", "run.goal.completed",
             "run.delivery.requested", "run.delivery.settled", "ship.started", "ship.completed",
             "owner.visible_message.requested", "candidate.ready"}
    settled_ids = [event.id for event in route.log.read_all() if event.type in kinds]
    route.reopen()
    for _ in range(3):
        route.tick()
        run_standard_tick_services(route.runtime, state=tick_state, now=1.0)
    assert [event.id for event in route.log.read_all() if event.type in kinds] == settled_ids
    assert {path: path.read_bytes() for path in persisted} == persisted
    assert git(route.root, "rev-parse", "main") == landed
    assert audit_terminal_delivery(route.state)["status"] == "passed"


@pytest.mark.parametrize("fault", ["missing_report", "stale_target"])
def test_strong_refactor_acceptance_rejection_then_valid_retry(strong_refactor, fault):
    route = strong_refactor
    route.complete_writer(route.plan_to_writer())
    verify = route.dispatch("flow-lanes-verify")
    admitted_before = [event.id for event in route.events("workflow.call.result.admitted")]
    route.acceptance_fault = fault
    with pytest.raises(ResultSubmitError) as rejected:
        route.complete_verify(verify)
    assert rejected.value.code == "result_not_admitted"
    assert any(issue["code"] == "product_acceptance_invalid" for issue in rejected.value.details["issues"])
    assert [event.id for event in route.events("workflow.call.result.admitted")] == admitted_before
    for kind in ("verify.passed", "run.delivery.requested", "ship.started", "run.goal.completed"):
        assert not route.events(kind)
    assert git(route.root, "rev-parse", "main") == route.base
    route.acceptance_fault = ""
    route.complete_verify(verify)
    _finish_and_audit(route)


def test_strong_refactor_missing_acceptance_spec_stops_before_writer(strong_refactor):
    route = strong_refactor
    route.acceptance_fault = "missing_spec"
    with pytest.raises(ResultSubmitError) as rejected:
        route.plan_to_writer()
    assert rejected.value.code == "plan_candidate_not_admitted"
    assert "product_acceptance_spec" in json.dumps(rejected.value.details)
    assert not route.events("plan.artifact_package.admitted")
    assert all(row.payload["stage_id"] != "flow-lanes-impl" for row in route.events("fanout.child.dispatched"))
    assert not route.events("candidate.ready")
    assert not route.events("run.goal.completed")
