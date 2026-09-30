"""zf issue — issue/bug 第三入口 CLI(B11,doc 92 §4)。

ingest: issue-candidate md(frontmatter 机器面)→ TaskContract 入
kanban(verification = repro 红→绿)。admission 同 gate:根路径只许
assembly 类持有;缺 repro/allowed_paths fail-closed。
validate: 只校验不写状态。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import yaml

from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.core.config.project_context import resolve_project_context
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.self_issue_session_host import SelfIssueSessionHost


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "issue", help="Validate or ingest an issue/bug candidate",
    )
    sub = parser.add_subparsers(dest="issue_cmd", required=True)
    report = sub.add_parser("report", help="Run a continuous Issue Agent from a free-form report")
    report.add_argument("description", nargs="?", default="", help="Optional title seed")
    report.add_argument("--target-project", default="")
    report.add_argument("--state-dir", default=None)
    report.add_argument("--attachment", action="append", default=[], metavar="PATH")
    report.add_argument(
        "--legacy-intake", action="store_true",
        help="Use the deprecated eight-question intake explicitly (compatibility only)",
    )
    report.add_argument(
        "--legacy-draft", action="store_true",
        help="Use the legacy Draft flow explicitly (compatibility only)",
    )
    report.add_argument(
        "--non-interactive", action="store_true",
        help="Return the Intake JSON instead of prompting",
    )
    report.set_defaults(func=_run_report)
    answer = sub.add_parser(
        "answer", help="Submit a Self-Issue Intake answer object from JSON",
    )
    answer.add_argument("intake_id")
    answer.add_argument("--answers-file", required=True)
    answer.add_argument("--state-dir", default=None)
    answer.set_defaults(func=_run_answer)
    session_answer = sub.add_parser("session-answer", help="Answer a Portable Issue Session question")
    session_answer.add_argument("session_id")
    session_answer.add_argument("answer")
    session_answer.add_argument("--state-dir", default=None)
    session_answer.set_defaults(func=_run_session_action)
    session_confirm = sub.add_parser("session-confirm", help="Confirm a Portable Issue Report")
    session_confirm.add_argument("session_id")
    session_confirm.add_argument("--confirmed-by", default="operator")
    session_confirm.add_argument("--provider", default="")
    session_confirm.add_argument("--target", default="")
    session_confirm.add_argument("--state-dir", default=None)
    session_confirm.set_defaults(func=_run_session_action)
    session_cancel = sub.add_parser("session-cancel", help="Cancel a Portable Issue Session")
    session_cancel.add_argument("session_id")
    session_cancel.add_argument("--state-dir", default=None)
    session_cancel.set_defaults(func=_run_session_action)
    session_get = sub.add_parser("session-get", help="Read a Portable Issue Session")
    session_get.add_argument("session_id")
    session_get.add_argument("--state-dir", default=None)
    session_get.set_defaults(func=_run_session_action)
    preview = sub.add_parser(
        "preview", help="Create an immutable provider publication preview",
    )
    preview.add_argument("draft_id")
    preview.add_argument(
        "--provider", dest="publication_mode",
        choices=("gitlab", "github", "both"), default="gitlab",
    )
    preview.add_argument("--state-dir", default=None)
    preview.set_defaults(func=_run_publication_action)
    confirm = sub.add_parser("confirm", help="Confirm an exact publication batch")
    confirm.add_argument("batch_id")
    confirm.add_argument("--payload-digest", required=True)
    confirm.add_argument("--state-dir", default=None)
    confirm.set_defaults(func=_run_publication_action)
    publish = sub.add_parser("publish", help="Publish a confirmed provider batch")
    publish.add_argument("batch_id")
    publish.add_argument("--confirmation-id", required=True)
    publish.add_argument("--state-dir", default=None)
    publish.set_defaults(func=_run_publication_action)
    for name, help_text in (
        ("validate", "Validate a candidate without changing state"),
        ("ingest", "Ingest a candidate as a Kanban TaskContract"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("path")
        cmd.add_argument("--state-dir", default=None)
        cmd.set_defaults(func=_run)


def _run_report(args: argparse.Namespace) -> int:
    try:
        ctx = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    writer = EventWriter(event_log_from_project(ctx.state_dir, config=ctx.config))
    payload = {
        "description": str(args.description or ""),
        "direct_report": not bool(args.legacy_intake or args.legacy_draft),
        "portable_session": not bool(args.legacy_intake or args.legacy_draft),
        "reporter_context": {
            "discovered_by": "user", "reported_by": "user",
            "collected_by": "kernel", "assessed_by": "orchestrator", "role": "user",
        },
        "target_binding": {
            "provider": "gitlab",
            "project": str(args.target_project or ""),
        },
    }
    requested = writer.emit(
        "control.action.requested", actor="operator", payload={
            "action": "self-issue-capture", "request": redact_obj(payload),
        },
    )
    result = ControlledActionService(
        ctx.state_dir, writer, config=ctx.config,
        project_root=ctx.project_root, actor="operator", source="cli", surface="cli",
    ).execute(
        action="self-issue-capture", requested_action="zf issue report",
        payload=payload, requested=requested,
    )
    # CLI --non-interactive is commonly used by scripts that expect the first
    # durable decision, while the Web surface intentionally returns immediately
    # and streams progress.  Wait briefly for the background Session only on
    # this CLI surface; never block the Web/API request on model work.
    if (
        result.get("ok")
        and bool(args.non_interactive)
        and result.get("status") == "investigating"
        and result.get("session_id")
    ):
        host = SelfIssueSessionHost(
            ctx.state_dir,
            ctx.project_root,
            policy=ctx.config.self_issue,
        )
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            time.sleep(0.05)
            current = host.get(str(result["session_id"]))
            if current.get("status") != "investigating":
                result = {**result, **current}
                if result.get("status") == "waiting_for_user":
                    # Preserve the historical CLI vocabulary while the
                    # portable Session uses an explicit lifecycle status.
                    result["status"] = "ask_user"
                break
    if (
        result.get("ok")
        and result.get("status") == "intake_collecting"
        and not bool(args.non_interactive)
        and sys.stdin.isatty()
    ):
        result = _interactive_intake_flow(
            result=result,
            ctx=ctx,
            writer=writer,
            attachment_paths=[Path(value) for value in args.attachment],
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def _run_session_action(args: argparse.Namespace) -> int:
    try:
        ctx = resolve_project_context(explicit_state_dir=getattr(args, "state_dir", None))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    action = {
        "session-answer": "self-issue-session-answer",
        "session-confirm": "self-issue-session-confirm",
        "session-cancel": "self-issue-session-cancel",
        "session-get": "self-issue-session-get",
    }[str(args.issue_cmd)]
    payload = {"session_id": str(args.session_id)}
    if action == "self-issue-session-answer":
        payload["answer"] = str(args.answer)
    elif action == "self-issue-session-confirm":
        payload.update({
            "confirmed_by": str(args.confirmed_by),
            "provider": str(args.provider),
            "target": str(args.target),
        })
    writer = EventWriter(event_log_from_project(ctx.state_dir, config=ctx.config))
    result = _execute_self_issue_action(
        ctx=ctx,
        writer=writer,
        action=action,
        payload=payload,
        requested_action=f"zf issue {args.issue_cmd}",
    )
    if result.get("status") == "investigating" and result.get("session_id"):
        host = SelfIssueSessionHost(
            ctx.state_dir,
            ctx.project_root,
            policy=ctx.config.self_issue,
        )
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            time.sleep(0.05)
            current = host.get(str(result["session_id"]))
            if current.get("status") != "investigating":
                result = {**result, **current}
                break
    if result.get("status") == "waiting_for_user":
        result["status"] = "ask_user"
    elif result.get("status") == "ready_for_confirmation":
        result["status"] = "ready"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def _run_answer(args: argparse.Namespace) -> int:
    try:
        ctx = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        raw = json.loads(Path(args.answers_file).read_text(encoding="utf-8"))
        answers = raw.get("answers") if isinstance(raw, dict) and "answers" in raw else raw
        if not isinstance(answers, dict):
            raise ValueError("answers file must contain a question-id keyed JSON object")
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    writer = EventWriter(event_log_from_project(ctx.state_dir, config=ctx.config))
    result = _execute_self_issue_action(
        ctx=ctx,
        writer=writer,
        action="self-issue-intake-submit",
        payload={
            "intake_id": str(args.intake_id),
            "answers": answers,
        },
        requested_action="zf issue answer",
    )
    if result.get("ok") and result.get("start_evidence"):
        result = _request_cli_evidence(result=result, ctx=ctx, writer=writer)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def _run_publication_action(args: argparse.Namespace) -> int:
    try:
        ctx = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    action = {
        "preview": "self-issue-preview",
        "confirm": "self-issue-confirm",
        "publish": "self-issue-publish",
    }[str(args.issue_cmd)]
    payload = {
        key: value for key, value in {
            "draft_id": getattr(args, "draft_id", ""),
            "batch_id": getattr(args, "batch_id", ""),
            "publication_mode": getattr(args, "publication_mode", ""),
            "payload_digest": getattr(args, "payload_digest", ""),
            "confirmation_id": getattr(args, "confirmation_id", ""),
        }.items() if value
    }
    writer = EventWriter(event_log_from_project(ctx.state_dir, config=ctx.config))
    result = _execute_self_issue_action(
        ctx=ctx,
        writer=writer,
        action=action,
        payload=payload,
        requested_action=f"zf issue {args.issue_cmd}",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def _interactive_intake_flow(*, result, ctx, writer, attachment_paths: list[Path]):
    intake = result.get("intake") if isinstance(result.get("intake"), dict) else {}
    questions = intake.get("questions") if isinstance(intake.get("questions"), list) else []
    answers = dict(intake.get("answers") or {})
    index = 0
    while index < len(questions):
        question = questions[index]
        question_id = str(question.get("id") or "")
        print(f"\n[{index + 1}/{len(questions)}] {question.get('title')}{' *' if question.get('required') else ''}")
        if question.get("help_text"):
            print(question["help_text"])
        if question_id == "environment":
            current = answers.get(question_id) if isinstance(answers.get(question_id), dict) else {}
            os_name = input(f"OS [{current.get('os', '')}]: ").strip() or str(current.get("os") or "")
            version = input(f"Version [{current.get('version', '')}]: ").strip() or str(current.get("version") or "")
            answers[question_id] = {"os": os_name, "version": version}
        elif question_id == "attachments_context":
            answers[question_id] = input("Attachment context (optional): ").strip()
        else:
            current = str(answers.get(question_id) or "")
            value = input(f"{question.get('placeholder')} [{current}]: ").strip() or current
            if question.get("required") and not value:
                print("This question can not be empty")
                continue
            if value == ":back" and index:
                index -= 1
                continue
            answers[question_id] = value
        index += 1
    for path in attachment_paths:
        content_type = _cli_content_type(path)
        added = _execute_self_issue_action(
            ctx=ctx, writer=writer, action="self-issue-intake-attachment-add",
            payload={
                "intake_id": str(intake.get("intake_id") or ""),
                "filename": path.name, "content_type": content_type,
                "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                "video_disclosure_confirmed": content_type.startswith("video/"),
            }, requested_action="zf issue attachment",
        )
        if not added.get("ok"):
            return added
    submitted = _execute_self_issue_action(
        ctx=ctx, writer=writer, action="self-issue-intake-submit",
        payload={"intake_id": str(intake.get("intake_id") or ""), "answers": answers},
        requested_action="zf issue intake submit",
    )
    if submitted.get("ok") and submitted.get("start_evidence"):
        return _request_cli_evidence(result=submitted, ctx=ctx, writer=writer)
    return submitted


def _request_cli_evidence(*, result, ctx, writer):
    draft = result.get("draft") if isinstance(result.get("draft"), dict) else {}
    start_result = _execute_self_issue_action(
        ctx=ctx,
        writer=writer,
        action="self-issue-evidence-start",
        payload={
            "draft_id": str(draft.get("draft_id") or ""),
            "revision": int(draft.get("revision") or 0),
        },
        requested_action="zf issue evidence start",
    )
    return start_result


def _cli_content_type(path: Path) -> str:
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".mp4": "video/mp4",
        ".webm": "video/webm", ".txt": "text/plain", ".log": "text/plain",
        ".json": "application/json",
    }.get(path.suffix.lower(), "application/octet-stream")


def _execute_self_issue_action(*, ctx, writer, action: str, payload: dict, requested_action: str):
    requested = writer.emit(
        "control.action.requested",
        actor="operator",
        payload={"action": action, "request": redact_obj(payload)},
    )
    return ControlledActionService(
        ctx.state_dir,
        writer,
        config=ctx.config,
        project_root=ctx.project_root,
        actor="operator",
        source="cli",
        surface="cli",
    ).execute(
        action=action,
        requested_action=requested_action,
        payload=payload,
        requested=requested,
    )


def _extract_frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("candidate is missing YAML frontmatter (issue-candidate.v1)")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("frontmatter is not closed")
    data = yaml.safe_load(text[4:end + 1])
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a mapping")
    return data


def _validate(fm: dict) -> list[str]:
    """fail-closed 校验(doc 92 §4):返回错误行,空=通过。"""
    errors: list[str] = []
    if str(fm.get("schema") or "") != "issue-candidate.v1":
        errors.append("schema must be issue-candidate.v1")
    for key in ("bug_id", "dedupe_key", "title"):
        if not str(fm.get(key) or "").strip():
            errors.append(f"{key} is required")
    if not str(fm.get("repro_command") or "").strip():
        errors.append("repro_command is required; prose reproduction steps are not acceptance evidence")
    allowed = [str(p) for p in fm.get("allowed_paths") or [] if str(p).strip()]
    if not allowed:
        errors.append("allowed_paths is required for admission write-scope checks")
    owner_class = str(fm.get("root_owner_class") or "none")
    for path in allowed:
        if "/" not in path.strip("/") and owner_class != "assembly":
            errors.append(
                f"root path {path!r} requires root_owner_class=assembly"
            )
    return errors


def _run(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"error: candidate file does not exist: {path}", file=sys.stderr)
        return 2
    try:
        fm = _extract_frontmatter(path)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    errors = _validate(fm)
    if errors:
        for line in errors:
            print(f"[X] {line}", file=sys.stderr)
        return 1
    bug_id = str(fm["bug_id"])
    print(f"issue {bug_id}: {fm['title']}")
    print(f"  repro: {fm['repro_command']}")
    print(f"  scope: {fm['allowed_paths']}")
    if args.issue_cmd == "validate":
        print("[OK] validation passed")
        return 0

    ctx = resolve_project_context(
        explicit_state_dir=getattr(args, "state_dir", None),
    )
    state_dir = ctx.state_dir
    if not state_dir.exists():
        print(f"error: state directory does not exist: {state_dir}", file=sys.stderr)
        return 2
    task_store = TaskStore(state_dir / "kanban.json")
    if task_store.get(bug_id) is not None:
        print(f"[OK] idempotent: task {bug_id} already exists; ingest skipped")
        return 0
    contract = TaskContract(
        behavior=str(fm.get("title") or ""),
        scope=[str(p) for p in fm.get("allowed_paths") or []],
        verification=str(fm.get("repro_command") or ""),
        acceptance=(
            f"reproduction changes from failing to passing: {fm.get('repro_command')}; "
            f"expected: {fm.get('expected') or 'n/a'}"
        ),
        source_ref=str(path),
        source_key=str(fm.get("dedupe_key") or ""),
    )
    task_store.add(Task(
        id=bug_id,
        title=str(fm.get("title") or bug_id),
        status="ready",
        contract=contract,
    ))
    writer = EventWriter(event_log_from_project(state_dir, config=ctx.config))
    writer.append(ZfEvent(
        type="task.created",
        actor="operator",
        task_id=bug_id,
        payload={
            "source_kind": str(fm.get("source_kind") or ""),
            "dedupe_key": str(fm.get("dedupe_key") or ""),
            "candidate_ref": str(path),
            "affinity_tag": str(fm.get("affinity_tag") or ""),
            "via": "zf issue ingest",
        },
    ))
    print(f"[OK] ingested -> Kanban task {bug_id} (verification=repro)")
    return 0
