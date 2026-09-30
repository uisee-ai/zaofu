"""A signed planner result cannot widen a formally admitted concurrent scope."""

import pytest

from tests.testkit.issue_route import isolate_issue_processes
from tests.testkit.shared_kind_route import SharedKindRoute
from tests.testkit.shared_kind_worker import SharedKindWorker
from zf.runtime.run_admission import build_run_admission_projection
from zf.runtime.sidecar_refs import write_sidecar_json


class EscapingPlanWorker(SharedKindWorker):
    def plan_result(self):
        plan = super().plan_result()
        task = plan["task_map"]["tasks"][0]
        task["allowed_paths"].append("docs/general")
        task["scope"].append("docs/general")
        # Both untrusted planner representations agree before signed submission.
        write_sidecar_json(
            self.state, plan["task_map_ref"], plan["task_map"],
            kind="task-map", schema_version="task-map.v1", created_by="planner",
        )
        return plan


@pytest.mark.parametrize("kind", ["prd", "refactor"])
def test_formal_concurrent_planner_cannot_expand_admitted_scope(tmp_path, monkeypatch, kind):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    route = SharedKindRoute(root, monkeypatch, concurrent=True, general_scope=("docs/general",))
    with route.evidence.guard("concurrent_plan_scope", {"kind": kind}):
        route.start()
        invoke = route.request_delivery(kind, scope=("answer.txt",))
        worker = EscapingPlanWorker(route, invoke, kind)
        scan_id = "prd-scan" if kind == "prd" else "refactor-flow-scan"
        scan = next(stage for stage in route.config.workflow.stages if stage.id == scan_id)
        for role in scan.roles:
            worker.complete_reader(worker.dispatch(scan_id, role), extra={
                "coverage_matrix": [{"area": "core", "status": "covered", "source_ref": "answer.txt"}],
            })
        worker.complete_reader(worker.dispatch("prd-plan" if kind == "prd" else "flow-plan"), plan=True)
        worker.complete_critic()
        impl = "prd-lanes-impl" if kind == "prd" else "refactor-flow-lanes-impl"
        for _ in range(8):
            route.tick()
            if worker.events("fanout.cancelled"):
                break
        assert not any(event.payload["stage_id"] == impl
                       for event in worker.events("fanout.child.dispatched"))
        assert any("outside admitted request scope" in event.payload.get("reason", "")
                   for event in worker.events("fanout.cancelled"))
        assert not worker.events("candidate.ready")
        route.reopen()
        for _ in range(3):
            route.tick()
        assert not any(event.payload["stage_id"] == impl
                       for event in worker.events("fanout.child.dispatched"))
        assert not forbidden


@pytest.mark.parametrize("kind", ["prd", "refactor"])
def test_formal_concurrent_scoped_code_run_reaches_terminal(tmp_path, monkeypatch, kind):
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    root = tmp_path / "project"
    root.mkdir()
    route = SharedKindRoute(root, monkeypatch, concurrent=True, general_scope=("docs/general",))
    with route.evidence.guard("concurrent_scoped_code_terminal", {"kind": kind}):
        route.start()
        invoke = route.request_delivery(kind, scope=("answer.txt",))
        worker = SharedKindWorker(route, invoke, kind)
        worker.finish()
        [terminal] = worker.events("run.goal.completed")
        assert terminal.payload["run_id"] == invoke.payload["workflow_run_id"]
        assert route.runtime.task_store.get(worker.delivery_task_id).contract.scope == ["answer.txt"]
        assert build_run_admission_projection(route.log.read_all()).active_run_ids == [
            route.invoke.payload["workflow_run_id"],
        ]
        route.reopen()
        for _ in range(3):
            route.tick()
        assert [event.id for event in worker.events("run.goal.completed")] == [terminal.id]
        assert not forbidden
