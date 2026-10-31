"""Prepare, answer, and inspect a Workflow before any execution approval."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zf.core.config.project_context import resolve_project_context
from zf.core.events import ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.events.writer import EventWriter
from zf.core.security.redaction import redact_obj
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.workflow_prepare import prepare_request_revision
from zf.runtime.workflow_requests import load_workflow_request, revise_workflow_request


def register(subparsers: argparse._SubParsersAction) -> None:
    prepare = subparsers.add_parser(
        "prepare", help="Select, clarify and assemble a Workflow for review"
    )
    source = prepare.add_mutually_exclusive_group(required=True)
    source.add_argument("--task")
    source.add_argument("--objective")
    source.add_argument("--request")
    prepare.add_argument("--backend", default="")
    prepare.add_argument("--kind", default="auto")
    prepare.add_argument("--source-root")
    prepare.add_argument("--target-root")
    prepare.add_argument(
        "--answer",
        action="append",
        default=[],
        help="Exact question=answer; repeat for multiple questions",
    )
    prepare.add_argument(
        "--revision", type=int, help="Required when answering an existing Request"
    )
    prepare.add_argument(
        "--enqueue-only",
        action="store_true",
        help="Let the Web/runtime consumer run Synthesis",
    )
    prepare.add_argument("--allow-missing-env", action="store_true")
    prepare.add_argument("--retry-failed", action="store_true", help="Create a new preparation attempt after repairing a failed one")
    prepare.add_argument("--state-dir")
    prepare.add_argument("--format", choices=["md", "json"], default="md")
    prepare.set_defaults(func=run_prepare)
    preview = subparsers.add_parser(
        "preview", help="Read the complete redacted YAML and current proposal"
    )
    preview.add_argument("--request", required=True)
    preview.add_argument("--state-dir")
    preview.add_argument("--format", choices=["md", "json", "yaml"], default="md")
    preview.set_defaults(func=run_preview)


def request_preview(state_dir: Path, request_id: str, *, project_root: Path | None = None) -> dict:
    request = load_workflow_request(state_dir, request_id)
    if not request:
        raise ValueError(f"Workflow Request not found: {request_id}")
    proposal = {}
    artifacts = {}
    if request.get("proposal_ref"):
        from zf.runtime.workflow_proposal import load_workflow_proposal

        proposal = load_workflow_proposal(state_dir, request["proposal_ref"])
        for name in ("effective_config", "config_diff", "short_flow_spec"):
            if proposal.get(f"{name}_ref"):
                artifacts[name] = hydrate_sidecar_ref(
                    state_dir, proposal[f"{name}_ref"]
                ).payload
    from zf.core.events.log import EventLog
    from zf.runtime.workflow_operation import load_workflow_operation

    operation = (
        load_workflow_operation(
            EventLog(state_dir / "events.jsonl"),
            str(request.get("synthesis_operation_id") or ""),
        )
        or {}
    )
    from zf.runtime.workflow_preparation_view import preparation_view
    from zf.runtime.workflow_git_readiness import preparation_git_diagnostics
    preparation = preparation_view(request, operation, git_blockers=(
        preparation_git_diagnostics(project_root) if project_root else []))
    failed = preparation["status"] in {"preparation_failed", "preparation_blocked", "cancelled"}
    return redact_obj(
        {
            "ok": not failed,
            "operation": operation,
            "status": preparation["status"],
            "preparation": preparation,
            "request_id": request_id,
            "request": request,
            "proposal": proposal,
            "artifacts": artifacts,
        }
    )


def _print(result: dict, format: str) -> None:
    if format == "json":
        print(json.dumps(redact_obj(result), ensure_ascii=False, indent=2))
        return
    yaml_text = (
        (result.get("artifacts") or {}).get("effective_config", {}).get("yaml", "")
    )
    if format == "yaml":
        if not yaml_text:
            raise ValueError(
                "No assembled YAML yet; answer pending questions or wait for preparation"
            )
        print(yaml_text, end="")
        return
    request = result.get("request") or result
    print(f"Workflow {result.get('request_id', '')}: {result.get('status', '')}")
    blockers = result.get("blockers") or (result.get("preparation") or {}).get("blockers") or []
    for blocker in blockers:
        print(f"- {blocker.get('message', '')} {blocker.get('fix_it', '')}".strip())
    if not result.get("ok", True):
        if not blockers and result.get("reason"):
            print(result["reason"])
        return
    for question in request.get("open_questions") or []:
        print(f"- {question}")
    if request.get("open_questions"):
        print(
            f"Answer: zf workflow prepare --request {result['request_id']} --revision {request.get('revision')} --answer 'question=answer'"
        )
    if yaml_text:
        print("```yaml\n" + yaml_text + "```")
    print("Review in Workflows; apply config if required, then Approve & Run.")


def run_preview(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=args.state_dir, load_config_with_explicit=True
        )
        result = request_preview(context.state_dir, args.request, project_root=context.project_root)
        _print(result, args.format)
        return 0 if result["ok"] else 1
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1


def run_prepare(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=args.state_dir, load_config_with_explicit=True
        )
        writer = EventWriter(
            event_log_from_project(context.state_dir, config=context.config)
        )
        payload = {
            "kind": args.kind,
            "backend": args.backend,
            "allow_missing_env": args.allow_missing_env,
            "retry_failed": getattr(args, "retry_failed", False),
        }
        for key in ("source_root", "target_root"):
            if getattr(args, key):
                payload[key] = getattr(args, key)
        if args.request:
            current = load_workflow_request(context.state_dir, args.request)
            if not current:
                raise ValueError("Workflow Request not found")
            if args.answer:
                if args.revision is None:
                    raise ValueError("--revision is required with --answer")
                answers = []
                for raw in args.answer:
                    question, sep, answer = raw.partition("=")
                    if (
                        not sep
                        or not answer.strip()
                        or question not in current.get("open_questions", [])
                    ):
                        raise ValueError(
                            "--answer must answer an exact pending question"
                        )
                    answers.append({"question": question, "answer": answer})
                remaining = [
                    q
                    for q in current.get("open_questions", [])
                    if q not in {a["question"] for a in answers}
                ]
                current = revise_workflow_request(
                    context.state_dir,
                    Path(current["workflow_input_manifest_ref"]),
                    actor="zf-cli",
                    expected_revision=args.revision,
                    clarification_answers=answers,
                    open_questions=remaining,
                    confirm=True,
                    writer=writer,
                    **{
                        key: payload[key]
                        for key in ("source_root", "target_root")
                        if key in payload
                    },
                )
            manifest = json.loads(
                Path(current["workflow_input_manifest_ref"]).read_text()
            )
            payload["intake_ref"] = manifest.get("intake_json_ref") or manifest.get(
                "intake_ref"
            )
            result = prepare_request_revision(
                state_dir=context.state_dir,
                project_root=context.project_root,
                config=context.config,
                writer=writer,
                request=current,
                actor="zf-cli",
                payload=payload,
            )
            if result is None:
                from zf.runtime.workflow_delivery import build_flow_submit_preview

                preview = build_flow_submit_preview(
                    config_path=context.project_root / "zf.yaml",
                    intake_path=Path(payload["intake_ref"]),
                    flow_kind=str(current.get("kind") or ""),
                    allow_missing_env=args.allow_missing_env,
                    synthesis_result_ref=current.get("synthesis_ref"),
                )
                result = {
                    "ok": preview["status"] != "STOP",
                    "status": preview["status"],
                    "request_id": args.request,
                }
        else:
            payload.update(task_id=args.task or "", objective=args.objective or "")
            requested = writer.append(
                ZfEvent(
                    type="control.action.requested",
                    actor="zf-cli",
                    payload={"action": "workflow-request", "request": payload},
                )
            )
            result = ControlledActionService(
                context.state_dir,
                writer,
                config=context.config,
                project_root=context.project_root,
                actor="zf-cli",
                source="cli",
                surface="cli",
            ).execute(
                action="workflow-request",
                requested_action="workflow-request",
                payload=payload,
                requested=requested,
            )
        if not result.get("ok"):
            _print(result, args.format)
            return 1
        if result.get("status") == "synthesis_queued" and not args.enqueue_only:
            from zf.runtime.workflow_synthesis_consumer import (
                consume_workflow_synthesis_operations,
            )

            consume_workflow_synthesis_operations(
                state_dir=context.state_dir,
                project_root=context.project_root,
                config=context.config,
                writer=writer,
                request_id=result["request_id"],
            )
        preview = request_preview(context.state_dir, result["request_id"], project_root=context.project_root)
        _print(preview, args.format)
        return 0 if preview["ok"] else 1
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
