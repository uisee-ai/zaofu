"""Four queued requests use the same runtime through every terminal release."""

import json

from tests.testkit.issue_route import isolate_issue_processes
from tests.testkit.shared_kind_route import SharedKindRoute
from tests.testkit.shared_kind_worker import SharedKindWorker
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


def test_four_shared_kinds_deliver_in_fifo_order(tmp_path, monkeypatch):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    route = SharedKindRoute(root, monkeypatch)
    with route.evidence.guard("four_kind_serial_delivery", {}):
        first = route.start()
        waiting = [(kind, route.request_delivery(kind)) for kind in ("issue", "prd", "refactor")]
        invokes = [route.invoke, *(invoke for _, invoke in waiting)]
        ids = [event.payload["workflow_run_id"] for event in invokes]
        route.tick()
        projection = build_run_admission_projection(route.log.read_all())
        assert projection.active_run_ids == ids[:1]
        assert projection.queued_run_ids == ids[1:]
        route.complete_reader(first)
        for stage in ("collect-1", "collect-2", "synthesize"):
            route.complete_reader(route.dispatch(stage))
        route.complete_verify(route.dispatch("verify"))
        for _ in range(12):
            route.tick()
            if route.events("run.goal.completed"):
                break
        assert [event.payload["run_id"] for event in route.events("run.goal.completed")] == ids[:1]
        receipts = {}
        ticks = TickServiceState()
        for index, (kind, invoke) in enumerate(waiting, start=1):
            worker = SharedKindWorker(route, invoke, kind)
            worker.finish()
            run_standard_tick_services(route.runtime, state=ticks, now=1.0)
            assert [event.payload["run_id"] for event in route.events("run.goal.completed")] == ids[:index + 1]
            [terminal] = worker.events("run.goal.completed")
            [ready] = worker.events("candidate.ready")
            assert terminal.payload["candidate_ref"] == ready.payload["candidate_ref"]
            assert terminal.payload["target_commit"] == ready.payload["candidate_head_commit"]
            assert all(worker.store.get(task_id).status == "done" for task_id in terminal.payload["completed_task_ids"])
            for _ in range(4):
                route.tick()
            projection = build_run_admission_projection(route.log.read_all())
            assert projection.active_run_ids == ids[index + 1:index + 2]
            assert projection.queued_run_ids == ids[index + 2:]
            route.reopen()
        run_standard_tick_services(route.runtime, state=ticks, now=2.0)
        for terminal in route.events("run.goal.completed"):
            [owner] = [event for event in route.events("owner.visible_message.requested")
                       if event.payload.get("terminal_event_id") == terminal.id]
            for key, identity in (("dossier_ref", "run_id"), ("completion_receipt_ref", "workflow_run_id")):
                path = route.state / owner.payload[key]
                receipts[path] = path.read_bytes()
                body = json.loads(receipts[path])
                assert body[identity] == terminal.payload["run_id"]
                assert body["terminal"]["event_id"] == terminal.id
        assert len(receipts) == 8
        for dispatch in route.events("fanout.child.dispatched"):
            operation_id = dispatch.payload["operation_id"]
            for kind in ("workflow.operation.requested", "workflow.operation.settled"):
                [operation] = [event for event in route.events(kind)
                               if event.payload["operation_id"] == operation_id]
                assert operation.payload["workflow_run_id"] == dispatch.payload["trace_id"]
        assert not route.events("workflow.invoke.rejected")
        assert not route.events("run.goal.completion.rejected")
        assert not route.events("discriminator.failed")
        counts = {kind: len(route.events(kind)) for kind in (
            "workflow.invoke.requested", "fanout.child.dispatched", "run.admission.admitted",
            "run.admission.released", "candidate.ready", "run.goal.completed", "owner.visible_message.requested",
        )}
        route.reopen()
        for _ in range(3):
            route.tick()
            run_standard_tick_services(route.runtime, state=ticks, now=2.0)
        assert {kind: len(route.events(kind)) for kind in counts} == counts
        assert {path: path.read_bytes() for path in receipts} == receipts
        assert not forbidden
