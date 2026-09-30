"""Catalog blocking semantics through transport credentials and the result CLI."""

import json
import subprocess
import sys

from tests.testkit.refactor_strong_route import StrongRefactorRoute
from tests.testkit.tmux_general_worker import observe_worker
from tests.testkit.tmux_issue_worker import IssueWorker
from tests.testkit.tmux_refactor_worker import RefactorWorker


class StrongRefactorWorker(StrongRefactorRoute):
    __init__ = IssueWorker.__init__
    complete = RefactorWorker.complete

    def submit_semantic(self, payload, semantic):
        self.consume(payload)
        scratch = self.state / payload["result_scratch_ref"]
        scratch.write_text(json.dumps(semantic))
        submitted = subprocess.run(
            [sys.executable, "-m", "zf.cli.main", "result", "submit", "--operation",
             payload["operation_id"], "--scratch"], capture_output=True, text=True, timeout=30,
        )
        assert submitted.returncode == 0, submitted.stdout + submitted.stderr
        return json.loads(submitted.stdout)


def main() -> None:
    observe_worker(StrongRefactorWorker)


if __name__ == "__main__":
    main()
