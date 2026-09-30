"""PRD scripted semantics after real child/synthesis stdin delivery."""

from __future__ import annotations

import json
import subprocess
import sys

from tests.testkit.prd_route import PrdRoute
from tests.testkit.tmux_general_worker import observe_worker
from tests.testkit.tmux_issue_worker import IssueWorker


class PrdWorker(PrdRoute):
    __init__ = IssueWorker.__init__
    publish_result = IssueWorker.publish_result

    def complete(self, dispatch):
        [self.invoke] = [row for row in self.events("workflow.invoke.requested")
                         if row.payload["workflow_run_id"] == dispatch.payload["workflow_run_id"]]
        if dispatch.type == "fanout.synth.dispatched":
            payload = dispatch.payload
            assert payload["output_profile_id"] == "plan-synth"
            self.consume(payload)
            scratch = self.state / payload["result_scratch_ref"]
            result = json.loads(scratch.read_text())
            result.update(status="completed", recommendation="approve",
                          summary="The admitted plan covers the fixture PRD.")
            result["report"].update(status="passed", recommendation="approve", summary=result["summary"])
            scratch.write_text(json.dumps(result))
            submitted = subprocess.run(
                [sys.executable, "-m", "zf.cli.main", "result", "submit", "--operation",
                 payload["operation_id"], "--scratch"], capture_output=True, text=True, timeout=30,
            )
            assert submitted.returncode == 0, submitted.stdout + submitted.stderr
            return json.loads(submitted.stdout)
        stage = dispatch.payload["stage_id"]
        if stage in {"prd-scan", "prd-plan", "prd-post-verify-discovery"}:
            return self.complete_reader(dispatch, plan=stage == "prd-plan")
        return {
            "prd-lanes-impl": self.complete_writer,
            "prd-lanes-verify": self.complete_verify,
            "prd-lanes-final": self.complete_judge,
        }[stage](dispatch)


def main() -> None:
    observe_worker(PrdWorker)


if __name__ == "__main__":
    main()
