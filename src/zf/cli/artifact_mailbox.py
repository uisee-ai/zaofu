"""Private obligation and coordination handoffs using kernel-issued role credentials."""

import argparse
import json
import sys

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import resolve_project_context
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.artifact_input_index import load_attempt_source_manifest
from zf.runtime.artifact_read_capability import (
    authenticate_role_from_environment,
    authorize_artifact_read_from_environment,
)
from zf.runtime.assistance_mailbox import (
    advisory_identity,
    advisory_inbox,
    runtime_view,
)
from types import SimpleNamespace

from zf.runtime.workflow_operation import load_workflow_operation
from zf.runtime.durable_mailbox_index import MailboxIndex


def register(sub):
    p = sub.add_parser(
        "mailbox", help="Exchange authorized messages and recover delivery receipts"
    )
    modes = p.add_subparsers(dest="mode", required=True)
    inbox = modes.add_parser(
        "inbox",
        help="List pending authorized message metadata without consuming a delivery",
    )
    selector = inbox.add_mutually_exclusive_group(required=True)
    selector.add_argument("--run", help="Run coordinator credential")
    selector.add_argument("--attempt", help="Current bound worker attempt")
    inbox.add_argument("--limit", type=int, default=20)
    inbox.add_argument("--state-dir")
    common = argparse.ArgumentParser(add_help=False)
    credential = common.add_mutually_exclusive_group(required=True)
    credential.add_argument(
        "--attempt", help="Current operation attempt with the bound role credential"
    )
    credential.add_argument(
        "--advisory",
        help="Dispatched assistance request with the resident role credential",
    )
    common.add_argument("--state-dir")
    send = modes.add_parser(
        "send",
        parents=[common],
        help="Send to an admitted Run obligation or request OA guidance",
    )
    send.add_argument("--request-id", required=True)
    send.add_argument(
        "--message-id",
        help="Sender's stable message identifier; defaults to --request-id",
    )
    send.add_argument(
        "--kind",
        required=True,
        choices=("notice", "question", "guidance", "evidence_notice"),
    )
    send.add_argument(
        "--recipient",
        type=json.loads,
        action="append",
        required=True,
        help="Address as JSON, including mode and the authorized recipient",
    )
    send.add_argument("--text", required=True)
    send.add_argument(
        "--evidence-ref", action="append", default=[], help="Question evidence locator"
    )
    send.add_argument(
        "--tried",
        action="append",
        default=[],
        help="Action already tried for this question",
    )
    send.add_argument(
        "--attachment",
        action="append",
        default=[],
        metavar="SOURCE_ID",
        help="Authorized source attachment",
    )
    pull = modes.add_parser(
        "pull", parents=[common], help="Prepare an exact, replayable batch"
    )
    pull.add_argument("--request-id", required=True)
    pull.add_argument(
        "--message", action="append", help="Exact message key", default=None
    )
    pull.add_argument("--limit", type=int, default=20)
    ack = modes.add_parser(
        "ack", parents=[common], help="Acknowledge a verified delivery"
    )
    ack.add_argument("--delivery", required=True)
    for action in ("claim", "answer", "wait", "handoff", "cancel"):
        qp = modes.add_parser(action, parents=[common])
        qp.add_argument("--message", required=True, help="Exact question message key")
        qp.add_argument("--delivery", required=True)
        qp.add_argument("--request-id", required=True)
        qp.add_argument("--text", required=action == "answer", default="")
        if action == "handoff":
            qp.add_argument(
                "--target",
                type=json.loads,
                required=True,
                help="New authorized obligation address",
            )
        if action == "wait":
            qp.add_argument(
                "--deadline",
                required=True,
                help="Authorized wait end in ISO 8601 format",
            )
    answer = modes.add_parser("read-answer", parents=[common])
    answer.add_argument("--message", required=True)
    p.set_defaults(func=run)


def _scoped_log(runtime, identity):
    events = MailboxIndex(runtime).read(identity)
    return SimpleNamespace(read_all=lambda: events, append=runtime.event_log.append)


def worker_identity(runtime, attempt):
    from zf.runtime.worker_assistance import current_assistance_operation

    row = TaskAttemptStore(runtime.state_dir / "task_attempts.json").get(attempt)
    if row and row.get("operation_id"):
        credential = authenticate_role_from_environment(runtime.state_dir)
        actor = credential["role_instance"]
        operation = current_assistance_operation(
            runtime.state_dir,
            _scoped_log(
                runtime,
                {"workflow_run_id": row["run_id"], "operation_id": row["operation_id"]},
            ),
            {
                "workflow_run_id": row["run_id"],
                "task_id": row["task_id"],
                "operation_id": row["operation_id"],
                "active_attempt_id": attempt,
                "lease_id": row["lease_id"],
            },
        )
        if actor != operation.get("role_instance") or actor != row.get("instance_id"):
            raise ValueError("task attempt does not belong to this credential")
        return {
            "actor": actor,
            "operation_id": row["operation_id"],
            "attempt_id": attempt,
            "lease_id": row["lease_id"],
            "workflow_run_id": row["run_id"],
            "credential_generation": credential["generation"],
        }
    manifest = load_attempt_source_manifest(runtime.state_dir, attempt)
    binding = authorize_artifact_read_from_environment(
        runtime.state_dir,
        manifest=manifest,
        event_log=_scoped_log(
            runtime, {"workflow_run_id": manifest["workflow_run_id"]}
        ),
    )
    return {**binding, "workflow_run_id": manifest["workflow_run_id"]}


def execute(runtime, identity, args):
    from zf.runtime import durable_mailbox as mail
    from zf.runtime.worker_assistance import request_assistance

    if args.mode == "send":
        if any(not isinstance(value, dict) for value in args.recipient):
            raise ValueError("recipient addresses require JSON objects")
        if args.kind == "question" and any(
            r.get("mode") == "coordination" for r in args.recipient
        ):
            if (
                args.recipient != [{"mode": "coordination", "owner": "orchestrator"}]
                or args.attachment
                or (args.message_id and args.message_id != args.request_id)
            ):
                raise ValueError(
                    "OA questions require one owner and use the assistance request identity and evidence locators"
                )
            op = (
                load_workflow_operation(
                    _scoped_log(runtime, identity), identity.get("operation_id", "")
                )
                or {}
            )
            if (
                not op
                or op.get("active_attempt_id") != identity.get("attempt_id")
                or op.get("workflow_run_id") != identity.get("workflow_run_id")
            ):
                raise ValueError("question no longer belongs to this worker attempt")
            return request_assistance(
                state_dir=runtime.state_dir,
                writer=runtime.event_writer,
                config=runtime.config,
                identity={
                    "operation_id": identity["operation_id"],
                    "active_attempt_id": identity["attempt_id"],
                    "workflow_run_id": identity["workflow_run_id"],
                    "lease_id": op["lease_id"],
                    "task_id": op["task_id"],
                },
                actor=identity["actor"],
                owner="orchestrator",
                question=args.text,
                evidence_refs=args.evidence_ref,
                tried=args.tried,
                client_request_id=args.request_id,
            )
        if args.evidence_ref or args.tried:
            raise ValueError(
                "assistance locators and tried actions apply to OA questions only"
            )
        return mail.post(
            runtime,
            identity,
            request_id=args.request_id,
            message_id=args.message_id or args.request_id,
            recipients=args.recipient,
            kind=args.kind,
            text=args.text,
            attachments=args.attachment,
        )
    if args.mode == "pull":
        return mail.prepare(
            runtime,
            identity,
            request_id=args.request_id,
            message_ids=args.message,
            limit=args.limit,
        )
    if args.mode == "ack":
        return mail.acknowledge(runtime, identity, delivery_id=args.delivery)
    if args.mode == "read-answer":
        from zf.runtime.assistance_mailbox import question_answer

        return question_answer(runtime, identity, args.message)
    return mail.question_action(
        runtime,
        identity,
        message_id=args.message,
        action=args.mode,
        request_id=args.request_id,
        delivery_id=args.delivery,
        text=args.text,
        target=getattr(args, "target", None),
        deadline=getattr(args, "deadline", ""),
    )


def run(args):
    try:
        ctx = resolve_project_context(
            explicit_state_dir=args.state_dir, load_config_with_explicit=True
        )
        log = event_log_from_project(ctx.state_dir, config=ctx.config)
        runtime = runtime_view(ctx.state_dir, EventWriter(log), ctx.config)
        if args.mode == "inbox":
            if args.attempt:
                from zf.runtime.durable_mailbox import pending_for_consumer

                result = pending_for_consumer(
                    runtime, worker_identity(runtime, args.attempt), limit=args.limit
                )
            else:
                result = advisory_inbox(runtime, args.run, limit=args.limit)
        else:
            identity = (
                worker_identity(runtime, args.attempt)
                if args.attempt
                else advisory_identity(runtime, args.advisory)
            )
            result = execute(runtime, identity, args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ConfigError, ValueError, OSError, TypeError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
