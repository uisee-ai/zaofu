"""Run-scoped scripted worker view over one shared route, not another runtime."""

from tests.testkit.issue_route import IssueRoute
from tests.testkit.prd_route import PrdRoute
from tests.testkit.refactor_route import RefactorRoute
from zf.core.events import ZfEvent


class SharedKindWorker(RefactorRoute):
    def __init__(self, shared, invoke: ZfEvent, kind: str) -> None:
        self.shared = shared
        self.invoke = invoke
        self.flow_kind = kind
        self.delivery_task_id = f"TASK-SHARED-{kind.upper()}"
        self.artifact_namespace = f"testkit-shared-{kind}"

    def __getattr__(self, name):
        if name == "store":
            return self.shared.runtime.task_store
        return getattr(self.shared, name)

    def events(self, kind: str) -> list[ZfEvent]:
        run_id = self.invoke.payload["workflow_run_id"]
        candidates = {
            (event.payload["pdd_id"], event.payload["candidate_head_commit"])
            for event in self.shared.events("candidate.ready")
            if event.payload.get("workflow_run_id") == run_id
        }
        return [event for event in self.shared.events(kind) if run_id in {
            event.payload.get("workflow_run_id"), event.payload.get("run_id"),
            event.payload.get("trace_id"), event.correlation_id,
        } or (kind.startswith("candidate.") and
              (event.payload.get("pdd_id"), event.payload.get("commit")) in candidates)]

    def tick(self) -> None:
        self.shared.tick()

    def reopen(self) -> None:
        self.shared.reopen()

    def dispatch(self, stage_id: str, role: str = "") -> ZfEvent:
        if self.flow_kind == "refactor" and stage_id.startswith("flow-"):
            stage_id = f"refactor-{stage_id}"
        return PrdRoute.dispatch(self, stage_id, role)

    def plan_result(self) -> dict:
        if self.flow_kind == "refactor":
            return RefactorRoute.plan_result(self)
        return PrdRoute.plan_result(self)

    def continue_to_writer(self) -> ZfEvent:
        if self.flow_kind == "issue":
            return self.dispatch("issue-lanes-impl")
        scan_id = "prd-scan" if self.flow_kind == "prd" else "refactor-flow-scan"
        scan = next(stage for stage in self.config.workflow.stages if stage.id == scan_id)
        for role in scan.roles:
            self.complete_reader(self.dispatch(scan_id, role), extra={
                "coverage_matrix": [{"area": "core", "status": "covered", "source_ref": "answer.txt"}],
            })
        self.complete_reader(self.dispatch("prd-plan" if self.flow_kind == "prd" else "flow-plan"), plan=True)
        self.complete_critic()
        return self.dispatch("prd-lanes-impl" if self.flow_kind == "prd" else "flow-lanes-impl")

    def finish(self) -> None:
        self.complete_writer(self.continue_to_writer())
        prefix = {"issue": "issue-lanes", "prd": "prd-lanes", "refactor": "flow-lanes"}[self.flow_kind]
        self.complete_verify(self.dispatch(f"{prefix}-verify"))
        if self.flow_kind == "prd":
            self.complete_reader(self.dispatch("prd-post-verify-discovery"))
        elif self.flow_kind == "refactor":
            self.complete_reader(self.dispatch("flow-verify-bridge"))
            self.complete_reader(self.dispatch("flow-module-parity-scan"), extra={"open_p0_p1_gap_count": 0})
        judge = "flow-final-judge" if self.flow_kind == "refactor" else f"{prefix}-final"
        self.complete_judge(self.dispatch(judge))
        for _ in range(12):
            self.tick()
            terminals = self.events("run.goal.completed")
            if terminals and all(self.store.get(task_id).status == "done"
                                 for task_id in terminals[0].payload["completed_task_ids"]):
                break
        [terminal] = self.events("run.goal.completed")
        assert terminal.origin == "kernel"
        assert terminal.payload["target_commit"] == self.candidate()[1]
        assert not self.events("run.goal.completion.rejected")

    def publish_result(self, dispatch: ZfEvent, payload: dict) -> ZfEvent:
        return IssueRoute.publish_result(self, dispatch, payload)
