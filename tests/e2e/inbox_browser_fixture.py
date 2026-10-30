"""Isolated real API fixture for Inbox Docker Playwright scenarios.

PYTHONPATH=src:. .venv/bin/python tests/e2e/inbox_browser_fixture.py \
    --scenario plan --root /tmp/zf-inbox-plan-<UTC> --port 8002

Use a fresh root for each scenario: the browser deliberately submits decisions.
The only Task in the Kanban scenario is created inside that temporary root.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import os
from pathlib import Path

import uvicorn

from tests.test_channel_collaboration_closure import seed
from tests.test_task_workflow_plans import _workflow_plan
from zf.core.config.loader import load_config
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.store import TaskStore
from zf.core.workspace import stable_project_id
from zf.core.workspace.onboarding import apply_action
from zf.runtime.control_actions import ControlledActionService
from zf.web.server import create_app

REPO = Path(__file__).resolve().parents[2]
TOKEN = "zf-isolated-inbox-browser"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("plan", "channel", "kanban"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    root = args.root.resolve()
    if root.parent != Path("/tmp") or not root.name.startswith("zf-inbox-") or args.port < 8002:
        parser.error("use a fresh /tmp/zf-inbox-<purpose>-<UTC> and a port >= 8002")
    if root.exists():
        parser.error("fixture root already exists")
    root.mkdir()
    os.environ["ZF_WORKSPACE_HOME"] = str(root / "workspace")
    os.environ["ZF_WEB_ACTION_TOKEN"] = TOKEN
    apply_action("skip")
    config_path = root / "zf.yaml"
    if args.scenario == "channel":
        state, _, writer, _ = seed(root)
        config_path.write_text("version: '1.0'\nproject:\n  name: inbox-browser\n  state_dir: .zf\n")
    else:
        state = root / ".zf"
        state.mkdir()
        writer = EventWriter(EventLog(state / "events.jsonl"))
        if args.scenario == "kanban":
            config_path.write_text((REPO / "zf.yaml").read_text(encoding="utf-8"), encoding="utf-8")
            for name in ("examples", "skills"):
                (root / name).symlink_to(REPO / name, target_is_directory=True)
        else:
            config_path.write_text("version: '1.0'\nproject:\n  name: inbox-browser\n  state_dir: .zf\npreset: minimal\n")
    store = TaskStore(state / "kanban.json")
    with store._locked():
        store._save_raw([])
    (state / "feature_list.json").write_text("[]\n", encoding="utf-8")
    config = load_config(config_path)
    project = stable_project_id(name="inbox-browser", root=root)
    writer.emit("simulation.started", actor="browser-fixture", payload={"scenario": args.scenario})
    if args.scenario == "channel":
        writer.emit("channel.message.posted", actor="web", payload={
            "channel_id": "ch-collaboration", "thread_id": "main", "message_id": "requirement",
            "member_id": "operator", "role": "user",
            "text": "Preserve database requirements and record explicit Owner deferral.", "source": "test",
        })
    elif args.scenario == "plan":
        writer.append(ZfEvent(type="plan.approval.requested", id="evt-plan-inbox", actor="zf-cli", payload={
            "plan_id": "plan-inbox", "stage_id": "plan", "task_count": 1,
        }))
        writer.append(ZfEvent(type="channel.question.opened", id="evt-question-inbox", actor="agent", payload={
            "channel_id": "ch-inbox", "thread_id": "main", "question_id": "q-inbox",
            "kind": "owner_decision", "target_member_id": "owner", "question": "Choose release scope",
        }))
    elif args.scenario == "kanban":
        service = ControlledActionService(state, writer, config=config, project_root=root,
                                          actor="web", source="kanban-agent", surface="web")
        workflow_plan = _workflow_plan()
        workflow_plan["options"][0]["parameters"] = {"backend": "mock", "target_root": str(root)}
        payload = {
            "title": "Assess the workflow planning model",
            "contract": {"behavior": "Compare runtime behavior.", "verification": "Run focused tests.",
                         "verification_tiers": ["runtime"]},
            "workflow_plan": workflow_plan, "project_id": project,
            "conversation_id": f"kanban:{project}", "thread_id": "thread-inbox",
        }
        requested = writer.emit("web.action.requested", actor="web", payload={"action": "create-task"})
        result = service.execute(action="create-task", requested_action="create-task", payload=payload, requested=requested)
        if not result.get("ok") or not result.get("workflow_plan_event_id"):
            raise RuntimeError(f"failed to seed real Task workflow request: {result}")
    app = create_app(state, project_root=root, config=config)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with original_lifespan(application) as lifespan_state:
                yield lifespan_state
        finally:
            writer.emit("simulation.done", actor="browser-fixture", payload={"scenario": args.scenario})

    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
