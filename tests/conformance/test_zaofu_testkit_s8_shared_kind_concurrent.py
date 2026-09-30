"""Concurrent admission stays fail-closed when formal requests lack isolation scope."""

import pytest

from tests.testkit.issue_route import isolate_issue_processes
from tests.testkit.shared_kind_route import SharedKindRoute
from zf.runtime.run_admission import build_run_admission_projection


def test_same_config_different_requests_reach_scope_gate_without_dispatch(tmp_path, monkeypatch):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    route = SharedKindRoute(root, monkeypatch, concurrent=True)
    with route.evidence.guard("concurrent_missing_scope", {}):
        route.start()
        waiting = route.request_delivery("issue")
        snapshots = []
        for invoke in (route.invoke, waiting):
            ref = invoke.payload["effective_config_ref"]
            snapshots.append(route.body(ref["ref"], ref["sha256"]))
        assert snapshots[0]["config"] == snapshots[1]["config"]
        assert route.invoke.payload["effective_config_digest"] != waiting.payload["effective_config_digest"]
        route.tick()
        projection = build_run_admission_projection(route.log.read_all())
        assert projection.active_run_ids == [route.invoke.payload["workflow_run_id"]]
        assert projection.queued_run_ids == [waiting.payload["workflow_run_id"]]
        [queued] = route.events("run.admission.queued")
        assert queued.payload["workflow_run_id"] == waiting.payload["workflow_run_id"]
        assert queued.payload["reason"] == (
            "concurrent isolation requires explicit work scope for active Run "
            + route.invoke.payload["workflow_run_id"]
        )
        assert not any(
            event.payload.get("trace_id") == waiting.payload["workflow_run_id"]
            for event in route.events("fanout.child.dispatched")
        )
        assert not route.events("workflow.invoke.rejected")
        assert not forbidden


@pytest.mark.parametrize("scope,blocker", [
    (("answer.txt",), ""),
    ((), "concurrent isolation requires explicit work scope"),
    (("docs/general",), "concurrent workdir scope overlaps an active Run"),
    (("docs/general/child",), "concurrent workdir scope overlaps an active Run"),
    (("../escape",), "concurrent isolation has invalid work scope"),
])
def test_formal_scoped_requests_admit_two_runs_and_rebuild_without_duplicates(
    tmp_path, monkeypatch, scope, blocker,
):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    route = SharedKindRoute(root, monkeypatch, concurrent=True, general_scope=("docs/general",))
    with route.evidence.guard("concurrent_scoped_admission", {}):
        route.start()
        second = route.request_delivery("issue", scope=scope)
        assert route.invoke.payload.get("scope") == ["docs/general"]
        assert second.payload.get("scope") == list(scope)
        route.tick()
        expected = [event.payload["workflow_run_id"] for event in (route.invoke, second)]
        if blocker:
            [queued] = route.events("run.admission.queued")
            assert queued.payload["reason"] == blocker
            before = [event.id for event in route.events("run.admission.queued")]
            route.reopen()
            for _ in range(3):
                route.tick()
            assert [event.id for event in route.events("run.admission.queued")] == before
            projection = build_run_admission_projection(route.log.read_all())
            assert projection.active_run_ids == expected[:1]
            assert projection.queued_run_ids == expected[1:]
            assert not any(event.payload.get("trace_id") == expected[1]
                           for event in route.events("fanout.child.dispatched"))
            assert not forbidden
            return
        projection = build_run_admission_projection(route.log.read_all())
        assert projection.active_run_ids == expected
        assert projection.queued_run_ids == []
        admitted = route.events("run.admission.admitted")
        assert [event.payload["workflow_run_id"] for event in admitted] == expected
        assert all(event.payload["policy_mode"] == "concurrent" for event in admitted)
        assert all(event.payload["max_active_runs"] == 2 for event in admitted)
        for _ in range(12):
            dispatched = route.events("fanout.child.dispatched")
            if {event.payload["trace_id"] for event in dispatched} == set(expected):
                break
            route.tick()
        assert {event.payload["trace_id"] for event in dispatched} == set(expected)
        third = route.request_delivery("prd", scope=("docs/prd",))
        route.tick()
        [queued] = route.events("run.admission.queued")
        assert queued.payload["workflow_run_id"] == third.payload["workflow_run_id"]
        assert queued.payload["reason"] == "project active Run capacity reached"
        before = {
            kind: [event.id for event in route.events(kind)]
            for kind in ("run.admission.admitted", "run.admission.queued", "fanout.child.dispatched")
        }
        route.reopen()
        for _ in range(3):
            route.tick()
        assert before == {kind: [event.id for event in route.events(kind)] for kind in before}
        assert build_run_admission_projection(route.log.read_all()).active_run_ids == expected
        assert not route.events("workflow.invoke.rejected")
        assert not forbidden
