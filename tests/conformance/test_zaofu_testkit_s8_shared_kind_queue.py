"""Four formal requests contend for a single runtime's real admission slot."""

import json

import pytest

from tests.testkit.issue_route import isolate_issue_processes
from tests.testkit.shared_kind_route import SharedKindRoute
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.tick_services import TickServiceState, run_standard_tick_services


@pytest.mark.parametrize("reopen_queued", [False, True])
def test_four_kinds_queue_and_general_terminal_releases_only_first(tmp_path, monkeypatch, reopen_queued):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    route = SharedKindRoute(root, monkeypatch)
    with route.evidence.guard("shared_queue", {"reopen_queued": reopen_queued}):
        first_dispatch = route.start()
        general_id = route.invoke.payload["workflow_run_id"]
        waiting = [route.request_delivery(kind) for kind in ("issue", "prd", "refactor")]
        waiting_ids = [event.payload["workflow_run_id"] for event in waiting]
        assert len({general_id, *waiting_ids}) == 4
        for _ in range(3):
            route.tick()
        projection = build_run_admission_projection(route.log.read_all())
        assert projection.active_run_ids == [general_id]
        assert projection.queued_run_ids == waiting_ids
        assert {event.payload["trace_id"] for event in route.events("fanout.child.dispatched")} == {general_id}
        if reopen_queued:
            route.reopen()
            route.tick()
        route.complete_reader(first_dispatch)
        for stage in ("collect-1", "collect-2", "synthesize"):
            route.complete_reader(route.dispatch(stage))
        route.complete_verify(route.dispatch("verify"))
        ticks = TickServiceState()
        for _ in range(12):
            route.tick()
            run_standard_tick_services(route.runtime, state=ticks, now=1.0)
            projection = build_run_admission_projection(route.log.read_all())
            if projection.active_run_ids == waiting_ids[:1]:
                break
        [terminal] = route.events("run.goal.completed")
        assert terminal.origin == "kernel"
        assert terminal.payload["workflow_run_id"] == general_id
        assert terminal.payload["run_contract_digest"] == route.invoke.payload["run_contract_digest"]
        assert not route.events("run.goal.completion.rejected")
        [owner] = [event for event in route.events("owner.visible_message.requested")
                   if event.payload.get("terminal_event_id") == terminal.id]
        receipts = {}
        for key, identity in (("dossier_ref", "run_id"), ("completion_receipt_ref", "workflow_run_id")):
            path = route.state / owner.payload[key]
            receipts[path] = path.read_bytes()
            body = json.loads(receipts[path])
            assert body[identity] == general_id
            assert body["terminal"]["event_id"] == terminal.id
        assert projection.active_run_ids == waiting_ids[:1]
        assert projection.queued_run_ids == waiting_ids[1:]
        [released] = route.events("run.admission.released")
        assert released.payload["workflow_run_id"] == waiting_ids[0]
        for _ in range(4):
            route.tick()
        assert any(event.payload["trace_id"] == waiting_ids[0]
                   for event in route.events("fanout.child.dispatched"))
        assert not any(event.payload["trace_id"] in waiting_ids[1:]
                       for event in route.events("fanout.child.dispatched"))
        assert not route.events("workflow.invoke.rejected")
        assert not route.events("discriminator.failed")
        counts = {kind: len(route.events(kind)) for kind in (
            "workflow.invoke.requested", "run.admission.admitted", "run.admission.queued",
            "run.admission.released", "fanout.child.dispatched", "run.goal.completed",
        )}
        route.reopen()
        for _ in range(3):
            route.tick()
        assert {kind: len(route.events(kind)) for kind in counts} == counts
        assert {path: path.read_bytes() for path in receipts} == receipts
        projection = build_run_admission_projection(route.log.read_all())
        assert projection.active_run_ids == waiting_ids[:1]
        assert projection.queued_run_ids == waiting_ids[1:]
        assert not forbidden
