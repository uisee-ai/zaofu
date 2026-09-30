"""Formal project targets and explicit work scopes stay separate at admission."""

import pytest

from tests.test_control_actions_workflow_request import _execute
from tests.testkit.issue_route import isolate_issue_processes
from tests.testkit.shared_kind_route import SharedKindRoute
from tests.testkit.shared_kind_worker import SharedKindWorker
from zf.runtime.run_admission import build_run_admission_projection


@pytest.mark.parametrize("kind", ["prd", "refactor"])
@pytest.mark.parametrize("scope,blocker", [
    (("answer.txt",), ""),
    ((), "concurrent isolation requires explicit work scope"),
    (("../escape",), "concurrent isolation has invalid work scope"),
    (("docs/general",), "concurrent workdir scope overlaps an active Run"),
])
def test_formal_absolute_project_target_with_relative_scope(
    tmp_path, monkeypatch, kind, scope, blocker,
):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    route = SharedKindRoute(root, monkeypatch, concurrent=True, general_scope=("docs/general",))
    with route.evidence.guard("concurrent_project_target", {"kind": kind, "scope": scope}):
        route.start()
        second = route.request_delivery(kind, scope=scope)
        assert second.payload["source_refs"]["target_root"] == str(root)
        assert second.payload["scope"] == list(scope)
        route.tick()
        run_ids = [event.payload["workflow_run_id"] for event in (route.invoke, second)]
        projection = build_run_admission_projection(route.log.read_all())
        if blocker:
            assert projection.active_run_ids == run_ids[:1]
            assert projection.queued_run_ids == run_ids[1:]
            assert projection.runs[run_ids[1]].blocker == blocker
        else:
            assert projection.active_run_ids == run_ids, projection.runs[run_ids[1]].blocker
            assert projection.queued_run_ids == []
            worker = SharedKindWorker(route, second, kind)
            scan = "prd-scan" if kind == "prd" else "flow-scan"
            worker.dispatch(scan)
        before = {name: [event.id for event in route.events(name)] for name in (
            "run.admission.admitted", "run.admission.queued", "fanout.child.dispatched",
        )}
        route.reopen()
        for _ in range(3):
            route.tick()
        assert before == {name: [event.id for event in route.events(name)] for name in before}
        if blocker:
            assert not any(event.payload.get("trace_id") == run_ids[1]
                           for event in route.events("fanout.child.dispatched"))
        assert not route.events("workflow.invoke.rejected")
        assert not forbidden


def test_formal_issue_writer_keeps_admitted_request_scope(tmp_path, monkeypatch):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    route = SharedKindRoute(root, monkeypatch, concurrent=True, general_scope=("docs/general",))
    with route.evidence.guard("concurrent_writer_scope", {}):
        route.start()
        invoke = route.request_delivery("issue", scope=("answer.txt",))
        worker = SharedKindWorker(route, invoke, "issue")
        dispatch = worker.continue_to_writer()
        task = route.runtime.task_store.get(dispatch.payload["task_id"])
        assert task.contract.scope == ["answer.txt"]
        child = worker.child(dispatch)
        snapshot = worker.body(child["contract_snapshot_ref"], child["contract_snapshot_digest"])
        assert snapshot["allowed_paths"] == ["answer.txt"]
        assert not forbidden


@pytest.mark.parametrize("kind", ["prd", "refactor"])
def test_formal_request_rejects_target_root_outside_project(tmp_path, monkeypatch, kind):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    other = tmp_path / "other-project"
    other.mkdir()
    route = SharedKindRoute(root, monkeypatch, concurrent=True, general_scope=("docs/general",))
    with route.evidence.guard("concurrent_project_identity", {"kind": kind}):
        route.start()
        if kind == "refactor":
            before = [event.id for event in route.events("workflow.invoke.requested")]
            proposal = _execute(route.service, "workflow-request", {
                "kind": kind, "objective": "Deliver answer.txt within the declared target",
                "backend": "codex", "allow_missing_env": True, "scope": ["answer.txt"],
                "source_root": str(route.source_root), "target_root": str(other),
            })
            assert not proposal["ok"]
            assert proposal["status"] == "clarification_required"
            assert any(item["kind"] == "workflow_root_binding_conflict"
                       for item in proposal["blockers"])
            assert [event.id for event in route.events("workflow.invoke.requested")] == before
            assert not forbidden
            return
        second = route.request_delivery(kind, scope=("answer.txt",), target_root=str(other))
        route.tick()
        run_id = second.payload["workflow_run_id"]
        projection = build_run_admission_projection(route.log.read_all())
        assert projection.queued_run_ids == [run_id]
        assert projection.runs[run_id].blocker == "concurrent target_root is outside project root"
        assert not forbidden
