"""Standalone host for the portable continuous Issue Agent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .agent import ContinuousIssueAgent
from .provider import SubprocessJsonProvider
from .provider_agent import ToolCallingIssueAgent
from .session import JsonIssueSessionStore
from .tools import ReadOnlyInvestigationTools
from .worker import IssueSessionWorker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m issue_core")
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--report", help="create a new Issue Session from this raw report")
    parser.add_argument("--session-id")
    parser.add_argument("--answer")
    parser.add_argument("--cancel", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--allowed-path", action="append", default=["."])
    parser.add_argument("--target", default="")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--provider-command", nargs="+", help="external JSON provider command")
    args = parser.parse_args(argv)

    store = JsonIssueSessionStore(args.store)
    if args.report:
        session = store.create(args.report, target_binding={"target": args.target} if args.target else {})
    elif args.session_id:
        session = store.load_session(args.session_id)
    else:
        parser.error("one of --report or --session-id is required")

    tools = ReadOnlyInvestigationTools(args.root, allowed_paths=tuple(args.allowed_path))
    runner = IssueSessionWorker(store, max_turns=3)
    agent = (
        ToolCallingIssueAgent(SubprocessJsonProvider(args.provider_command))
        if args.provider_command else ContinuousIssueAgent()
    )
    if args.cancel:
        cancelled = runner.runner.cancel(session.session_id)
        payload = {
            "session_id": session.session_id,
            "status": cancelled.status.value,
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.answer:
        result = runner.runner.answer_user(session.session_id, args.answer, agent, tools)
    elif session.status.value == "ready_for_confirmation" and args.confirm:
        result = None
    else:
        result = runner.run(session.session_id, agent, tools).last_result
    if result is None:
        payload = {"session_id": session.session_id, "status": "cancelled_or_no_result"}
    else:
        payload = {
            "session_id": session.session_id,
            "status": result.status,
            "working_set": result.working_set.to_dict(),
            "question": result.question,
            "reason": result.reason,
        }
        if args.confirm and result.status == "ready":
            completed = runner.runner.confirm(session.session_id, report_ref=result.working_set.digest)
            payload["session_status"] = completed.status.value
            payload["final_report_ref"] = completed.final_report_ref
            payload["publication_snapshot_ref"] = completed.publication_snapshot_ref
    if args.confirm and result is None:
        completed = runner.runner.confirm(session.session_id)
        payload = {
            "session_id": session.session_id,
            "status": completed.status.value,
            "final_report_ref": completed.final_report_ref,
            "publication_snapshot_ref": completed.publication_snapshot_ref,
        }
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
