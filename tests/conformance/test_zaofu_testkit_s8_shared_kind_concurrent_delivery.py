"""Two formally admitted Runs interleave signed results and retain delivery identity."""

import json

import pytest

from tests.testkit.issue_route import isolate_issue_processes
from tests.testkit.shared_kind_route import SharedKindRoute
from tests.testkit.shared_kind_worker import SharedKindWorker
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


@pytest.mark.parametrize("first_terminal", ["general", "issue"])
def test_concurrent_signed_results_keep_terminal_and_receipts_run_scoped(
    tmp_path, monkeypatch, first_terminal,
):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    route = SharedKindRoute(root, monkeypatch, concurrent=True, general_scope=("docs/general",))
    with route.evidence.guard("concurrent_delivery", {"first_terminal": first_terminal}):
        scope = route.start()
        issue_invoke = route.request_delivery("issue", scope=("answer.txt",))
        worker = SharedKindWorker(route, issue_invoke, "issue")
        writer = worker.continue_to_writer()
        invokes = {"general": route.invoke, "issue": issue_invoke}
        ids = {kind: event.payload["workflow_run_id"] for kind, event in invokes.items()}
        assert build_run_admission_projection(route.log.read_all()).active_run_ids == list(ids.values())
        assert scope.payload["trace_id"] == ids["general"]
        assert writer.payload["trace_id"] == ids["issue"]

        route.complete_reader(scope)
        worker.complete_writer(writer)
        route.complete_reader(route.dispatch("collect-1"))
        worker.complete_verify(worker.dispatch("issue-lanes-verify"))
        route.reopen()
        route.complete_reader(route.dispatch("collect-2"))
        route.complete_reader(route.dispatch("synthesize"))
        general_verify = route.dispatch("verify")
        issue_judge = worker.dispatch("issue-lanes-final")
        assert not route.events("run.goal.completed")
        assert build_run_admission_projection(route.log.read_all()).active_run_ids == list(ids.values())
        ticks = TickServiceState()
        receipts = {}
        order = [first_terminal, "issue" if first_terminal == "general" else "general"]
        for index, kind in enumerate(order):
            if kind == "general":
                route.complete_verify(general_verify)
            else:
                worker.complete_judge(issue_judge)
            completed = [ids[name] for name in order[:index + 1]]
            for _ in range(12):
                route.tick()
                run_standard_tick_services(route.runtime, state=ticks, now=1.0)
                owners = [event for event in route.events("owner.visible_message.requested")
                          if event.payload.get("run_id") in completed]
                terminals = route.events("run.goal.completed")
                tasks_done = all(route.runtime.task_store.get(task).status == "done"
                                 for terminal in terminals for task in terminal.payload["completed_task_ids"])
                if len(owners) == len(completed) and tasks_done:
                    break
            terminals = route.events("run.goal.completed")
            assert [event.payload["run_id"] for event in terminals] == completed
            projection = build_run_admission_projection(route.log.read_all())
            assert projection.active_run_ids == [run for run in ids.values() if run not in completed]
            assert projection.queued_run_ids == []
            terminal = terminals[-1]
            assert terminal.origin == "kernel"
            assert terminal.payload["run_contract_digest"] == invokes[kind].payload["run_contract_digest"]
            assert all(route.runtime.task_store.get(task).status == "done"
                       for task in terminal.payload["completed_task_ids"])
            if kind == "issue":
                [candidate] = worker.events("candidate.ready")
                assert terminal.payload["candidate_ref"] == candidate.payload["candidate_ref"]
                assert terminal.payload["target_commit"] == candidate.payload["candidate_head_commit"]
            [owner] = [event for event in owners if event.payload["terminal_event_id"] == terminal.id]
            for key, identity in (("dossier_ref", "run_id"), ("completion_receipt_ref", "workflow_run_id")):
                path = route.state / owner.payload[key]
                receipts[path] = path.read_bytes()
                body = json.loads(receipts[path])
                assert body[identity] == ids[kind]
                assert body["terminal"]["event_id"] == terminal.id
            route.reopen()

        assert len(receipts) == 4
        for dispatch in route.events("fanout.child.dispatched"):
            for kind in ("workflow.operation.requested", "workflow.operation.settled"):
                [operation] = [event for event in route.events(kind)
                               if event.payload["operation_id"] == dispatch.payload["operation_id"]]
                assert operation.payload["workflow_run_id"] == dispatch.payload["trace_id"]
        assert not route.events("workflow.invoke.rejected")
        assert not route.events("run.goal.completion.rejected")
        assert not route.events("discriminator.failed")
        before = {kind: [event.id for event in route.events(kind)] for kind in (
            "run.admission.admitted", "fanout.child.dispatched", "run.goal.completed",
            "candidate.ready", "owner.visible_message.requested",
        )}
        for _ in range(3):
            route.tick()
            run_standard_tick_services(route.runtime, state=ticks, now=2.0)
        assert before == {kind: [event.id for event in route.events(kind)] for kind in before}
        assert {path: path.read_bytes() for path in receipts} == receipts
        assert not forbidden
