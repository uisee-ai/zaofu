"""Default PRD entry, planning admission, candidate and terminal delivery."""

from pathlib import Path
import json

import pytest

from tests.e2e.scripts.four_flow_e2e_support import summarize_flow_run
from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery
from tests.testkit.issue_route import git, isolate_issue_processes
from tests.testkit.prd_route import PrdRoute
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


@pytest.fixture
def prd_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    route = PrdRoute(tmp_path, monkeypatch)
    with route.evidence.guard("standard_prd", {}):
        yield route
        assert not forbidden
        assert git(route.root, "rev-parse", "main") == route.base
        ledger = (route.state / "events.jsonl").read_text()
        assert all(token not in ledger for token in route.credentials.values())


@pytest.mark.parametrize("reopen_at", ["none", "planner_fact", "writer_fact"])
def test_prd_route_official_terminal_delivery(prd_route: PrdRoute, reopen_at: str) -> None:
    route = prd_route
    with route.evidence.guard("standard_prd_terminal", {"reopen_at": reopen_at}):
        writer = route.plan_to_writer(reopen_after_plan=reopen_at == "planner_fact")
        route.complete_writer(writer)
        if reopen_at == "writer_fact":
            route.reopen()
        route.complete_verify(route.dispatch("prd-lanes-verify"))
        discovery = route.dispatch("prd-post-verify-discovery")
        assert not route.events("flow.goal.closed")
        assert not route.events("run.goal.completed")
        route.complete_reader(discovery)
        route.complete_judge(route.dispatch("prd-lanes-final"))
        task_id = writer.payload["task_id"]
        for _ in range(12):
            route.tick()
            if route.events("run.goal.completed") and route.store.get(task_id).status == "done":
                break
        [terminal] = route.events("run.goal.completed")
        [claim] = route.events("run.goal.completion.claimed")
        [closed] = route.events("goal.closure.synthesized")
        assert terminal.origin == closed.origin == "kernel"
        assert terminal.payload["claim_event_id"] == claim.id
        assert terminal.payload["source_event_id"] == closed.id
        assert terminal.payload["delivery_policy"] == "report_and_demo"
        assert terminal.payload["completed_task_ids"] == [task_id]
        assert route.store.get(task_id).status == "done"
        _, commit = route.candidate()
        assert terminal.payload["target_commit"] == terminal.payload["verified_target_commit"] == commit
        [verification] = [row for row in route.log.read_all() if row.id == terminal.payload["verification_event_id"]]
        assert verification.type == "fanout.child.completed"
        assert verification.payload["stage_id"] == "prd-lanes-verify"
        assert verification.payload["target_commit"] == commit
        assert verification.payload["admitted_call_result_ref"] == terminal.payload["verification_admitted_call_result_ref"]
        assert len(route.events("fanout.child.dispatched")) == 7
        assert len(route.events("fanout.synth.dispatched")) == 1
        assert len(route.events("workflow.call.result.admitted")) == 9
        for event in route.events("workflow.call.result.admitted"):
            for key in ("envelope_ref", "control_result_ref"):
                descriptor = event.payload[key]
                route.body(descriptor["ref"], descriptor["sha256"])
        rows = route.log.read_all()
        sequence = ["prd.scan.completed", "task_map.ready", "candidate.ready", "test.passed",
                    "flow.discovery.completed", "flow.goal.closed", "goal.closure.synthesized", "run.goal.completed"]
        positions = []
        for kind in sequence:
            [event] = route.events(kind)
            assert event.origin == "kernel"
            positions.append(next(index for index, row in enumerate(rows) if row.id == event.id))
        assert positions == sorted(positions)
        observation = summarize_flow_run(route.log.read_all(), route.invoke)
        assert observation["admission_status"] == "admitted", observation
        assert observation["terminal_status"] == "completed", observation
        tick_state = TickServiceState()
        for _ in range(12):
            route.tick()
            run_standard_tick_services(route.runtime, state=tick_state, now=1.0)
            delivery = audit_terminal_delivery(route.state)
            if delivery["status"] != "pending":
                break
        assert delivery["status"] == "passed", delivery
        [owner] = [event for event in route.events("owner.visible_message.requested")
                   if event.payload.get("terminal_event_id") == observation["terminal_event_id"]]
        assert owner.payload["run_id"] == terminal.payload["run_id"] == observation["run_id"]
        persisted = {}
        for key in ("dossier_ref", "completion_receipt_ref"):
            ref = owner.payload[key]
            assert ref == delivery["delivery"][key]
            path = (route.state / ref).resolve()
            assert path.is_relative_to(route.state)
            persisted[path] = path.read_bytes()
            document = json.loads(persisted[path])
            assert document["terminal"]["event_id"] == observation["terminal_event_id"]
            assert document["run_id" if key == "dossier_ref" else "workflow_run_id"] == observation["run_id"]
            assert document["source_fingerprint"] == owner.payload[
                "dossier_source_fingerprint" if key == "dossier_ref" else "completion_receipt_fingerprint"
            ]
        settled = route.settled_snapshot()
        route.reopen()
        for _ in range(3):
            route.tick()
            run_standard_tick_services(route.runtime, state=tick_state, now=1.0)
        assert route.settled_snapshot() == settled
        assert {path: path.read_bytes() for path in persisted} == persisted
        assert [event.id for event in route.events("owner.visible_message.requested")
                if event.payload.get("terminal_event_id") == terminal.id] == [owner.id]
        assert len(route.events("flow.goal.closed")) == 1
        assert not route.events("goal.closure.superseded")
        assert not any(event.type.startswith("ship.") for event in route.log.read_all())
        assert audit_terminal_delivery(route.state)["status"] == "passed"


@pytest.mark.parametrize("mode", ["missing_reads", "stale_target"])
def test_prd_verifier_rejects_missing_reads_or_old_commit(prd_route: PrdRoute, mode: str) -> None:
    route = prd_route
    with route.evidence.guard("invalid_prd_verify", {"mode": mode}):
        route.complete_writer(route.plan_to_writer())
        result = route.complete_verify(route.dispatch("prd-lanes-verify"), **{mode: True})
        for _ in range(5):
            route.tick()
        if mode == "missing_reads":
            repairs = route.events("workflow.call.result.repair.requested")
            assert any(issue["code"] == "required_read_missing"
                       for event in repairs for issue in event.payload["issues"])
        else:
            [invalid] = route.events("workflow.call.result.invalid")
            assert invalid.payload["reason"] == "stale_call_result_superseded"
            assert any(issue["code"] == "stale_target_commit" for issue in invalid.payload["issues"])
        assert not any(event.payload.get("source_event_id") == result.id
                       for event in route.events("workflow.call.result.admitted"))
        for kind in ("test.passed", "flow.discovery.requested", "flow.goal.closed", "run.goal.completed"):
            assert not route.events(kind)
        assert all(event.payload["stage_id"] not in {"prd-post-verify-discovery", "prd-lanes-final"}
                   for event in route.events("fanout.child.dispatched"))
