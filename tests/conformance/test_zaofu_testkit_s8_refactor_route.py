"""Default Refactor v3 admission, parity barrier and terminal owner handoff."""

import json
from pathlib import Path

import pytest

from tests.e2e.scripts.four_flow_e2e_support import summarize_flow_run
from tests.e2e.scripts.prod_flow_terminal_audit import audit_terminal_delivery
from tests.testkit.issue_route import isolate_issue_processes
from tests.testkit.refactor_route import RefactorRoute
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


@pytest.fixture
def refactor_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    route = RefactorRoute(tmp_path / "target", monkeypatch)
    with route.evidence.guard("official_refactor_case", {}):
        yield route
        assert not forbidden
        assert (route.source_root / "answer.txt").read_text() == "fixed\n"
        assert set(route.source_root.iterdir()) == {route.source_root / "answer.txt"}
        ledger = (route.state / "events.jsonl").read_text()
        assert all(token not in ledger for token in route.credentials.values())


@pytest.mark.parametrize("reopen_at", ["none", "planner_fact", "writer_fact"])
def test_refactor_official_terminal_delivery(refactor_route: RefactorRoute, reopen_at: str) -> None:
    route = refactor_route
    with route.evidence.guard("default_refactor_terminal", {"reopen_at": reopen_at}):
        writer = route.plan_to_writer(reopen_after_plan=reopen_at == "planner_fact")
        route.complete_writer(writer)
        if reopen_at == "writer_fact":
            route.reopen()
        route.complete_verify(route.dispatch("flow-lanes-verify"))
        route.complete_reader(route.dispatch("flow-verify-bridge"))
        parity = route.dispatch("flow-module-parity-scan")
        assert not route.events("module.parity.closed")
        assert not route.events("run.goal.completed")
        assert all(event.payload["stage_id"] != "flow-final-judge"
                   for event in route.events("fanout.child.dispatched"))
        route.complete_reader(parity, extra={"open_p0_p1_gap_count": 0})
        route.complete_judge(route.dispatch("flow-final-judge"))
        for _ in range(12):
            route.tick()
            if route.events("run.goal.completed") and route.store.get(route.delivery_task_id).status == "done":
                break
        [terminal] = route.events("run.goal.completed")
        [claim] = route.events("run.goal.completion.claimed")
        [closed] = route.events("goal.closure.synthesized")
        assert terminal.origin == closed.origin == "kernel"
        assert terminal.payload["claim_event_id"] == claim.id
        assert terminal.payload["source_event_id"] == closed.id
        assert terminal.payload["delivery_policy"] == "report_only"
        assert terminal.payload["completed_task_ids"] == [route.delivery_task_id]
        assert route.store.get(route.delivery_task_id).status == "done"
        _, commit = route.candidate()
        assert terminal.payload["target_commit"] == terminal.payload["verified_target_commit"] == commit
        [verification] = [row for row in route.log.read_all() if row.id == terminal.payload["verification_event_id"]]
        assert verification.type == "fanout.child.completed"
        assert verification.payload["stage_id"] == "flow-lanes-verify"
        assert verification.payload["target_commit"] == commit
        assert verification.payload["admitted_call_result_ref"] == terminal.payload["verification_admitted_call_result_ref"]
        assert len(route.events("workflow.invoke.requested")) == 1
        assert len(route.events("fanout.child.dispatched")) == 9
        assert len(route.events("fanout.synth.dispatched")) == 1
        for event in route.events("workflow.call.result.admitted"):
            for key in ("envelope_ref", "control_result_ref"):
                descriptor = event.payload[key]
                route.body(descriptor["ref"], descriptor["sha256"])
        rows = route.log.read_all()
        sequence = ["zaofu.refactor.review.ready", "zaofu.refactor.plan.ready", "task_map.ready",
                    "candidate.ready", "test.passed", "verify.passed", "module.parity.closed",
                    "goal.closure.synthesized", "run.goal.completed"]
        positions = []
        for kind in sequence:
            [event] = route.events(kind)
            assert event.origin == "kernel"
            positions.append(next(index for index, row in enumerate(rows) if row.id == event.id))
        assert positions == sorted(positions)
        tick_state = TickServiceState()
        for _ in range(12):
            route.tick()
            run_standard_tick_services(route.runtime, state=tick_state, now=1.0)
            delivery = audit_terminal_delivery(route.state)
            if delivery["status"] != "pending":
                break
        assert delivery["status"] == "passed", delivery
        observation = summarize_flow_run(route.log.read_all(), route.invoke)
        assert observation["admission_status"] == "admitted", observation
        assert observation["terminal_status"] == "completed", observation
        [owner] = [event for event in route.events("owner.visible_message.requested")
                   if event.payload.get("terminal_event_id") == terminal.id]
        assert owner.payload["run_id"] == terminal.payload["run_id"] == observation["run_id"]
        persisted = {}
        for key in ("dossier_ref", "completion_receipt_ref"):
            ref = owner.payload[key]
            assert ref == delivery["delivery"][key]
            path = (route.state / ref).resolve()
            assert path.is_relative_to(route.state)
            persisted[path] = path.read_bytes()
            document = json.loads(persisted[path])
            assert document["terminal"]["event_id"] == terminal.id == observation["terminal_event_id"]
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
        assert len(route.events("module.parity.closed")) == 1
        assert not route.events("goal.closure.superseded")
        assert not any(event.type.startswith("ship.") for event in route.log.read_all())
        assert audit_terminal_delivery(route.state)["status"] == "passed"


def test_refactor_open_parity_gap_without_plan_cannot_complete(refactor_route: RefactorRoute) -> None:
    route = refactor_route
    with route.evidence.guard("open_parity_gap", {}):
        route.complete_writer(route.plan_to_writer())
        route.complete_verify(route.dispatch("flow-lanes-verify"))
        route.complete_reader(route.dispatch("flow-verify-bridge"))
        route.complete_reader(route.dispatch("flow-module-parity-scan"), extra={"open_p0_p1_gap_count": 1})
        for _ in range(5):
            route.tick()
        assert route.events("module.parity.blocked")
        for kind in ("module.parity.closed", "goal.closure.synthesized", "run.goal.completed"):
            assert not route.events(kind)
        assert all(event.payload["stage_id"] != "flow-final-judge"
                   for event in route.events("fanout.child.dispatched"))
