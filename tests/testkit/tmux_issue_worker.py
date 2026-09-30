"""Issue light semantics through real tmux stdin and the sanctioned emit CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

from tests.testkit.issue_route import IssueRoute, git
from tests.testkit.tmux_general_worker import observe_worker
from zf.core.events import EventLog


class IssueWorker(IssueRoute):
    def __init__(self) -> None:
        self.state = Path(os.environ["ZF_STATE_DIR"])
        self.root = Path(os.environ["ZF_PROJECT_ROOT"])
        self.role = os.environ["ZF_ROLE_INSTANCE"]
        self.log = EventLog(self.state / "events.jsonl")
        self.command = f"{shlex.quote(sys.executable)} tests/verify_issue.py"
        self.base = git(self.root, "rev-parse", "main")

    def publish_result(self, dispatch, payload):
        assert payload.get("semantic_result_submit_mode", "off") == "off"
        assert payload["role_instance"] == self.role
        kind = "dev.build.done" if "impl_self_check" in payload else payload["canonical_success_event"]
        result = subprocess.run(
            [sys.executable, "-m", "zf.cli.main", "emit", kind,
             "--task", payload.get("task_id", dispatch.task_id), "--actor", self.role,
             "--payload-file", "-"],
            input=json.dumps(payload), capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        match = re.search(r"Emitted: " + re.escape(kind) + r" \((evt-[^)]+)\)", result.stdout)
        assert match, result.stdout + result.stderr
        return {"canonical_event_id": match[1], "canonical_event_type": kind}

    def complete(self, dispatch):
        return {
            "issue-lanes-impl": self.complete_writer,
            "issue-lanes-verify": self.complete_verify,
            "issue-lanes-final": self.complete_judge,
        }[dispatch.payload["stage_id"]](dispatch)


def main() -> None:
    observe_worker(IssueWorker)


if __name__ == "__main__":
    main()
