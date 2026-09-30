"""Refactor semantics activated by exact live child/synthesis stdin delivery."""

from tests.testkit.refactor_route import RefactorRoute
from tests.testkit.tmux_general_worker import observe_worker
from tests.testkit.tmux_issue_worker import IssueWorker
from tests.testkit.tmux_prd_worker import PrdWorker


class RefactorWorker(RefactorRoute):
    __init__ = IssueWorker.__init__
    publish_result = IssueWorker.publish_result

    def complete(self, dispatch):
        [self.invoke] = [row for row in self.events("workflow.invoke.requested")
                         if row.payload["workflow_run_id"] == dispatch.payload["workflow_run_id"]]
        if dispatch.type == "fanout.synth.dispatched":
            return PrdWorker.complete(self, dispatch)
        stage = dispatch.payload["stage_id"]
        if stage == "flow-scan":
            return self.complete_reader(dispatch, extra={
                "coverage_matrix": [{"area": "core", "status": "covered", "source_ref": "answer.txt"}],
            })
        if stage == "flow-plan":
            return self.complete_reader(dispatch, plan=True)
        if stage == "flow-verify-bridge":
            return self.complete_reader(dispatch)
        if stage == "flow-module-parity-scan":
            return self.complete_reader(dispatch, extra={"open_p0_p1_gap_count": 0})
        return {
            "flow-lanes-impl": self.complete_writer,
            "flow-lanes-verify": self.complete_verify,
            "flow-final-judge": self.complete_judge,
        }[stage](dispatch)


def main() -> None:
    observe_worker(RefactorWorker)


if __name__ == "__main__":
    main()
