"""Bridge startup/shutdown intents; Session mutations use controlled actions."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from issue_core import JsonIssueSessionStore, SessionStatus
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.runtime.control_actions import ControlledActionService
from zf.runtime import self_issue_session_execution as execution


class FeishuIssueLifecycle:
    def __init__(self) -> None:
        self.contexts: dict[Path, Any] = {}
        execution.open_owner("feishu")

    def add_context(self, context: Any) -> None:
        if context is not None:
            self.contexts[Path(context.state_dir).resolve()] = context

    def _action(self, context: Any, session_id: str, action: str) -> dict[str, Any]:
        writer = EventWriter(event_log_from_project(context.state_dir, config=context.config))
        requested = writer.emit(
            "feishu.issue.command.received", actor="feishu-lifecycle", correlation_id=session_id,
            payload={"schema_version": "feishu-issue-command.v1", "command": action,
                     "action": action, "session_id": session_id, "source": "host_lifecycle"},
        )
        return ControlledActionService(
            context.state_dir, writer, config=context.config, project_root=context.project_root,
            actor="feishu-lifecycle", source="feishu-lifecycle", surface="feishu",
        ).execute(action=action, requested_action=f"bridge lifecycle: {action}",
                  payload={"session_id": session_id}, requested=requested)

    def start(self) -> None:
        for context in list(self.contexts.values()):
            store = JsonIssueSessionStore(context.state_dir / "self-issues/portable-sessions")
            for path in store.root.glob("*.session.json"):
                sid = path.name.removesuffix(".session.json")
                try:
                    session = store.load_session(sid)
                    working = store.load_working_set(sid)
                    if working.workflow.get("execution_owner") != "feishu":
                        continue
                    if session.status not in {SessionStatus.CREATED, SessionStatus.INVESTIGATING, SessionStatus.PAUSED}:
                        continue
                    if session.status == SessionStatus.PAUSED and not working.workflow.get("resume_after_restart"):
                        continue
                    result = self._action(context, sid, "self-issue-session-recover")
                    if not result.get("ok"):
                        raise ValueError(result.get("reason") or result.get("status"))
                except Exception as exc:
                    print(f"[bridge] self-issue recovery failed {sid}: {exc!r}", file=sys.stderr, flush=True)

    def stop_accepting(self) -> None:
        execution.stop_owner("feishu")

    def shutdown(self, *, timeout: float = 10.0) -> list[str]:
        runs = execution.stop_owner("feishu")
        for run in runs:
            context = self.contexts.get(run.host.state_dir.resolve())
            try:
                if context is None:
                    raise ValueError("missing project context for active Issue Session")
                result = self._action(context, run.session_id, "self-issue-session-pause")
                if not result.get("ok"):
                    raise ValueError(result.get("reason") or result.get("status"))
            except Exception as exc:
                print(f"[bridge] self-issue shutdown failed {run.session_id}: {exc!r}", file=sys.stderr, flush=True)
        pending = execution.join_runs(runs, timeout=timeout)
        if pending:
            print(f"[bridge] self-issue workers still exiting: {pending}", file=sys.stderr, flush=True)
        return pending
