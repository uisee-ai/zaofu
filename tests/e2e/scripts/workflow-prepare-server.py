"""Isolated real HTTP/UI fixture; only Provider and runtime launch are mocked.

PYTHONPATH=src:. .venv/bin/python tests/e2e/scripts/workflow-prepare-server.py /tmp/zf-workflow-prepare-<UTC> 8002
"""

import os
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import uvicorn

from tests.test_web_workflow_proposals import _write_config
from tests.e2e.test_intake_semantic_handoff_mock_e2e import candidate
from zf.core.config.loader import load_config
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.workspace.onboarding import apply_action as onboarding_action
from zf.runtime.workflow_requests import (
    hydrate_workflow_requirement,
    load_workflow_request,
)
from zf.runtime.workflow_synthesis import run_workflow_synthesis
from zf.web.server import create_app


def synthesize(**kwargs):
    current = load_workflow_request(kwargs["state_dir"], kwargs["request_id"])
    requirement = hydrate_workflow_requirement(kwargs["state_dir"], current)
    result = candidate(current)
    if not requirement.get("clarification_answers"):
        result["open_questions"] = ["Which session behavior should be preserved?"]
    return run_workflow_synthesis(**{**kwargs, "candidate_result": result})


def mocked_launch(result, **kwargs):
    return {**result, "runtime_start": {"status": "running", "fixture": True}}


def preview_turn(self, **kwargs):
    """Only the Provider is deterministic; revision, diff and apply use real services."""
    contract = kwargs.get("context", {}).get("workflow_preview_review")
    if not contract:
        raise AssertionError("This fixture only permits Workflow Preview chat")
    reply = {"answer": "Deterministic browser fixture: only the selected field changes."}
    if contract.get("read_only"):
        return SimpleNamespace(ok=True, reply=json.dumps({"answer": "当前 Run 使用冻结配置；这里可以讨论，修改需新的审核提案。", "changes": []}), incomplete=False)
    if contract["mode"] == "chat":
        reply["changes"] = []
        if contract["message"] != "Why is this needed?":
            field = contract["field"] or "config.0.lanes"
            reply["changes"] = [{"field": field, "replacement": 2 if field.endswith(".lanes") else "Active and empty sessions"}]
    if contract["mode"] == "revise":
        reply["replacement"] = (
            2 if contract["field"].endswith(".lanes") else "Active and empty sessions"
        )
    return SimpleNamespace(ok=True, reply=json.dumps(reply), incomplete=False)


if __name__ == "__main__":
    root = Path(sys.argv[1]).resolve()
    if not str(root).startswith("/tmp/zf-") or root.exists():
        raise SystemExit("Use a new /tmp/zf-<purpose>-<UTC> directory")
    root.mkdir()
    _write_config(root / "zf.yaml")
    state = root / ".zf"
    state.mkdir()
    (state / "events.jsonl").touch()
    (state / "kanban.json").write_text("[]")
    os.environ["ZF_WEB_ACTION_TOKEN"] = "workflow-prepare-fixture-token"
    os.environ["ZF_SKIP_PROJECT_REGISTER"] = "1"
    os.environ["ZF_WORKSPACE_HOME"] = str(root / "workspace")
    onboarding_action("skip")
    config = load_config(root / "zf.yaml")
    app = create_app(state, config=config, project_root=root)

    def simulation_done():
        EventWriter(event_log_from_project(state, config=config)).emit(
            "simulation.done",
            actor="workflow-prepare-fixture",
            payload={"root": str(root)},
        )

    app.router.add_event_handler("shutdown", simulation_done)
    with (
        patch("zf.runtime.workflow_synthesis.run_workflow_synthesis", synthesize),
        patch("zf.web.headless_agent.KanbanHeadlessAgent.run_turn", preview_turn),
        patch(
            "zf.runtime.workflow_delivery.ensure_submitted_workflow_runtime",
            mocked_launch,
        ),
    ):
        uvicorn.run(app, host="0.0.0.0", port=int(sys.argv[2]))
