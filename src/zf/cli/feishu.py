"""zf feishu — CLI-first bridge for Feishu messages."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import ProjectContext, resolve_project_context
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.events.log import EventLog
from zf.core.workspace import stable_project_id
from zf.integrations.feishu.approval import ApprovalStore
from zf.integrations.feishu.client_ports import BitableClient
from zf.integrations.feishu.callback_security import (
    identity_auth_levels,
    resolve_identity,
    verify_feishu_signature,
)
from zf.integrations.feishu.callback_token import verify_action
from zf.integrations.feishu.mock_clients import (
    MockFeishuBitableClient,
    MockFeishuDocumentClient,
)
from zf.integrations.feishu.lark_cli import (
    LarkCliBitableClient,
    LarkCliDocumentClient,
)
from zf.integrations.feishu.controls import ControlHandler
from zf.integrations.feishu.channel_result_card import (
    push_channel_result_cards_once,
)
from zf.integrations.feishu.channel_progress_card import (
    CHANNEL_PROGRESS_COMMANDS,
    handle_channel_progress_action,
    push_channel_progress_cards_once,
)
from zf.integrations.feishu.delivery_card import push_delivery_cards_once
from zf.integrations.feishu.gateway import (
    AuthLevel,
    CommandGateway,
    FeishuCommandEnvelope,
)
from zf.integrations.feishu.plan_approval_card import push_plan_approval_cards_once
from zf.integrations.feishu.kanban_proposal_card import (
    push_kanban_proposal_cards_once,
)
from zf.integrations.feishu.kanban_plan_card import (
    KANBAN_PLAN_COMMANDS,
    PLAN_FORM_OPTION_ID,
    form_answers_from_values,
    parse_plan_answer_target,
    push_kanban_plan_cards_once,
)
from zf.integrations.feishu.replan_approval_card import push_replan_cards_once
from zf.integrations.feishu.run_manager_card import push_run_manager_cards_once
from zf.integrations.feishu.issue_adapter import (
    ISSUE_COMMANDS,
    ISSUE_MUTATION_COMMANDS,
    handle_issue_command,
    push_issue_cards_once,
    push_self_issue_cards_once,
)
from zf.integrations.feishu.stream_card import push_stream_card_once
from zf.integrations.feishu.projection import ProjectionRouter, RoutingConfig
from zf.integrations.feishu.production import production_config_issues
from zf.integrations.feishu.queries import QueryExecutor
from zf.integrations.feishu.storage import IdempotencyStore, OffsetStore
from zf.integrations.feishu.sync import (
    FeishuSyncLedger,
    sync_automation_bitable,
    sync_automation_document,
    sync_kanban_bitable,
)
from zf.integrations.feishu.targets import (
    automation_insight_view_layout_specs,
    automation_insight_view_specs,
    automation_insight_field_specs,
    kanban_field_specs,
    kanban_view_layout_specs,
    kanban_view_specs,
    parse_feishu_bitable_ref,
    parse_feishu_document_id,
    update_env_file,
)
from zf.integrations.feishu.transport import (
    FeishuHttpTransport,
    FeishuMessage,
    FeishuTransport,
    FeishuTransportError,
    MockFeishuTransport,
)
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.kanban_proposals import PROPOSAL_EVENT_TYPES
from zf.runtime.owner_visible_delivery import deliver_owner_visible_messages_once


QUERY_COMMANDS = {"status", "tasks", "task", "cost", "blockers", "handoff"}
CONTROL_COMMANDS = {"pause", "resume", "retry", "cancel", "note"}
CONTROLLED_ACTION_COMMANDS = {"create", "update", "request-fanout"}
APPROVAL_COMMANDS = {"approve", "deny"}
PLAN_APPROVAL_COMMANDS = {"plan-approve", "plan-reject"}
AGENT_CANCEL_COMMANDS = {"agent-cancel"}
HUMAN_DECISION_COMMANDS = {
    "human-decision-approve",
    "human-decision-diagnose",
    "human-decision-explain",
    "human-decision-halt",
    "human-decision-reject",
}
CHANNEL_QUESTION_COMMANDS = {
    "channel-question-adopt",
    "channel-question-oos",
}
KANBAN_PROPOSAL_COMMANDS = {
    "kanban-proposal-approve",
    "kanban-proposal-dismiss",
}
# 2026-07-17 card-quality L3: owner-visible alert cards gain a real
# acknowledge button (attention-ack:<attention_id>) instead of the dead
# "回复「重试」" text prompt nothing consumed.
ATTENTION_ACK_COMMANDS = {"attention-ack"}
# feishu-A2: commands whose buttons carry a signed action token. These are the
# mutation buttons rendered into pushed cards (plan approve/reject, interrupt).
SIGNED_ACTION_COMMANDS = (
    PLAN_APPROVAL_COMMANDS
    | AGENT_CANCEL_COMMANDS
    | HUMAN_DECISION_COMMANDS
    | CHANNEL_QUESTION_COMMANDS
    | KANBAN_PLAN_COMMANDS
    | KANBAN_PROPOSAL_COMMANDS
    | CHANNEL_PROGRESS_COMMANDS
    | ATTENTION_ACK_COMMANDS
)
ATTENTION_COMMANDS = {"attention"}
ISSUE_MUTATION_SIGNED_COMMANDS = ISSUE_MUTATION_COMMANDS | {
    "self-issue-session-answer", "self-issue-session-confirm",
    "self-issue-session-cancel", "self-issue-session-retry",
    "self-issue-session-pause", "self-issue-session-reanalyze",
    "self-issue-session-materialize",
    "self-issue-github-device-start", "self-issue-github-device-poll",
    "self-issue-save", "self-issue-intake-save", "self-issue-submit",
    "self-issue-intake-submit", "self-issue-dismiss", "self-issue-intake-dismiss",
    "self-issue-preview", "self-issue-confirm", "self-issue-publish",
    "self-issue-recover", "self-issue-resolve-unknown",
    "self-issue-attachment-add", "self-issue-attachment-remove",
    "self-issue-intake-attachment-add", "self-issue-intake-attachment-remove",
    "self-issue-evidence-start", "self-issue-evidence-interrupt",
    "self-issue-evidence-resume", "self-issue-evidence-apply",
    "self-issue-evidence-fail", "self-issue-limited-continue",
    "self-issue-attachment-preview", "self-issue-attachment-confirm",
    "self-issue-attachment-prepare", "self-issue-attachment-resolve-unknown",
}
ISSUE_WRITE_COMMANDS = ISSUE_MUTATION_SIGNED_COMMANDS | {
    "self-issue-new", "self-issue-intake-start",
}
SIGNED_ACTION_COMMANDS = SIGNED_ACTION_COMMANDS | ISSUE_MUTATION_SIGNED_COMMANDS


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("feishu", help="Handle Feishu bridge commands")
    parser.set_defaults(func=_run_root)
    feishu_subparsers = parser.add_subparsers(dest="feishu_command")

    handle = feishu_subparsers.add_parser(
        "handle",
        help="Handle one Feishu webhook/message fixture",
    )
    handle.add_argument(
        "--event-json",
        default="-",
        help="Path to event JSON, '-' for stdin, or an inline JSON object",
    )
    handle.add_argument(
        "--state-dir",
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    handle.add_argument(
        "--user-level",
        action="append",
        default=[],
        metavar="USER=LEVEL",
        help="Authorize a Feishu user as viewer, operator, or approver",
    )
    handle.add_argument(
        "--no-idempotency",
        action="store_true",
        help="Disable durable idempotency for local debugging",
    )
    handle.set_defaults(func=run_handle)

    push = feishu_subparsers.add_parser(
        "push",
        help="Push event projections to Feishu channels",
    )
    push.add_argument("--state-dir", default=None)
    push.add_argument("--channel", action="append", default=[], metavar="ROLE=RECEIVE_ID")
    push.add_argument("--to", default="", help="Send all channel roles to this receive_id")
    push.add_argument("--receive-id-type", default="chat_id")
    push.add_argument("--transport", choices=["mock", "real"], default="mock")
    push.add_argument("--watch", action="store_true")
    push.add_argument("--once", action="store_true")
    push.add_argument("--interval", type=float, default=2.0)
    push.add_argument("--from-beginning", action="store_true")
    push.add_argument(
        "--owner-visible-max-attempts",
        type=int,
        default=1,
        help="Max Feishu attempts per owner.visible_message.requested message",
    )
    push.set_defaults(func=run_push)

    consume = feishu_subparsers.add_parser(
        "consume",
        help="Ingest live Feishu events via lark-cli (WS long-conn, no webhook)",
    )
    consume.add_argument(
        "event_key",
        nargs="?",
        default="im.message.receive_v1",
        help="lark-cli EventKey (e.g. im.message.receive_v1 or card.action.trigger)",
    )
    consume.add_argument("--state-dir", default=None)
    consume.add_argument(
        "--as-identity", default="bot", help="lark-cli --as (bot|user|auto)"
    )
    consume.set_defaults(func=_run_consume)

    bridge = feishu_subparsers.add_parser(
        "bridge",
        help="One-shot turnkey reply: inbound message to channel to real agent reply",
    )
    bridge.add_argument("--event-json", default="-",
                        help="Inbound event JSON (inline object or file path)")
    bridge.add_argument("--issue-merge", action="store_true", help="Use the Settings Issue-only connection")
    bridge.add_argument("--watch", action="store_true",
                        help="Run the in-process WS long-connection (always-on "
                             "bridge) instead of a one-shot reply")
    bridge.add_argument("--debounce-ms", type=int, default=600,
                        help="Per-chat debounce window for --watch (default 600)")
    bridge.add_argument("--state-dir", default=None)
    bridge.add_argument(
        "--workspace",
        default="",
        help="Use the workspace project-group binding index for --watch",
    )
    bridge.add_argument(
        "--all-workspaces",
        action="store_true",
        help=(
            "Route a single provider App WS across every registered workspace "
            "project-group binding"
        ),
    )
    bridge.add_argument(
        "--app-id",
        default="",
        help="Expected Feishu App ID for routed --watch (must match env)",
    )
    bridge.add_argument(
        "--projection-interval",
        type=float,
        default=1.0,
        help="Bridge projection tick interval in seconds",
    )
    bridge.set_defaults(func=_run_bridge)

    from zf.cli import feishu_project_group
    feishu_project_group.register(feishu_subparsers)

    serve = feishu_subparsers.add_parser(
        "serve",
        help="Run a small webhook server that wraps zf feishu handle",
    )
    serve.add_argument("--state-dir", default=None)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--user-level", action="append", default=[], metavar="USER=LEVEL")
    serve.add_argument("--no-idempotency", action="store_true")
    serve.set_defaults(func=run_serve)

    send_test = feishu_subparsers.add_parser(
        "send-test",
        help="Validate local Feishu bridge configuration without real API calls",
    )
    send_test.add_argument(
        "--state-dir",
        default=None,
        help="Path to runtime state dir (default: project.state_dir from zf.yaml, else .zf)",
    )
    send_test.add_argument("--transport", choices=["mock", "real"], default="mock")
    send_test.add_argument("--to", default="", help="Receive ID to send a test message to")
    send_test.add_argument("--receive-id-type", default="chat_id")
    send_test.add_argument("--message", default="ZaoFu Feishu bridge test")
    send_test.set_defaults(func=run_send_test)

    live_smoke = feishu_subparsers.add_parser(
        "live-smoke",
        help="Run a cleanup-safe smoke against the real Feishu API",
    )
    live_smoke.add_argument("--state-dir", default=None)
    live_smoke.add_argument("--to", required=True, help="Target chat_id")
    live_smoke.add_argument(
        "--purpose",
        choices=["kanban_agent", "run_manager", "default"],
        default="kanban_agent",
    )
    live_smoke.add_argument(
        "--receive-id-type",
        choices=["chat_id"],
        default="chat_id",
    )
    live_smoke.add_argument(
        "--message",
        default="ZaoFu real transport verification",
    )
    live_smoke.add_argument(
        "--confirm-real-api",
        action="store_true",
        help="Confirm that this command may call Feishu and create a temporary card",
    )
    live_smoke.add_argument(
        "--keep-message",
        action="store_true",
        help="Keep the temporary card instead of recalling it",
    )
    live_smoke.add_argument("--format", choices=["json", "text"], default="json")
    live_smoke.set_defaults(func=_run_live_smoke)

    operate = feishu_subparsers.add_parser(
        "operate",
        help="Run the headless Feishu operator agent over pending /zf ask messages",
    )
    operate.add_argument("--state-dir", default=None)
    operate.add_argument("--transport", choices=["mock", "real"], default="mock")
    operate.add_argument(
        "--backend",
        choices=["codex-headless", "claude-headless"],
        default="codex-headless",
    )
    operate.add_argument("--from-beginning", action="store_true")
    operate.set_defaults(func=run_operate)

    sync_automations = feishu_subparsers.add_parser(
        "sync-automations",
        help="Publish Automation daily/weekly/project reports to a Feishu document",
    )
    sync_automations.add_argument("--state-dir", default=None)
    sync_automations.add_argument("--project-id", default="")
    sync_automations.add_argument("--project-name", default="")
    sync_automations.add_argument("--document-id", default="")
    sync_automations.add_argument(
        "--document-url",
        default="",
        help="Feishu Docx URL; the doc token is extracted automatically",
    )
    sync_automations.add_argument(
        "--automation",
        action="append",
        default=[],
        choices=["daily-brief", "weekly-review", "project-monitor"],
    )
    _add_projection_backend_args(sync_automations)
    sync_automations.add_argument("--dry-run", action="store_true")
    sync_automations.set_defaults(func=run_sync_automations)

    sync_automation_table = feishu_subparsers.add_parser(
        "sync-automation-insights-table",
        help="Mirror Automation summaries and insights to a Feishu Bitable table",
    )
    sync_automation_table.add_argument("--state-dir", default=None)
    sync_automation_table.add_argument("--project-id", default="")
    sync_automation_table.add_argument("--project-name", default="")
    sync_automation_table.add_argument("--app-token", default="")
    sync_automation_table.add_argument("--table-id", default="")
    sync_automation_table.add_argument("--bitable-url", default="")
    sync_automation_table.add_argument(
        "--automation",
        action="append",
        default=[],
        choices=["daily-brief", "weekly-review", "project-monitor"],
    )
    _add_projection_backend_args(sync_automation_table)
    sync_automation_table.add_argument("--table-name", default="Automation Insights")
    sync_automation_table.add_argument("--base-name", default="")
    sync_automation_table.add_argument("--folder-token", default="")
    sync_automation_table.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="KEY=FEISHU_FIELD",
        help="Override a Bitable field name, e.g. severity=Severity",
    )
    sync_automation_table.add_argument(
        "--no-create-table",
        action="store_true",
        help="Fail when the Automation Insights table ID is missing",
    )
    sync_automation_table.add_argument(
        "--no-ensure-views",
        action="store_true",
        help="Skip creating Feishu Automation Insights overview views",
    )
    sync_automation_table.add_argument(
        "--no-ensure-layouts",
        action="store_true",
        help="Skip configuring Feishu Automation Insights view layout properties",
    )
    sync_automation_table.add_argument(
        "--no-recreate-missing",
        action="store_true",
        help="Fail instead of recreating the table when the Feishu target was deleted",
    )
    sync_automation_table.add_argument("--dry-run", action="store_true")
    sync_automation_table.set_defaults(func=run_sync_automation_insights_table)

    sync_kanban = feishu_subparsers.add_parser(
        "sync-kanban-table",
        help="Mirror the current Kanban projection to a Feishu Bitable table",
    )
    sync_kanban.add_argument("--state-dir", default=None)
    sync_kanban.add_argument("--project-id", default="")
    sync_kanban.add_argument("--project-name", default="")
    sync_kanban.add_argument("--app-token", default="")
    sync_kanban.add_argument("--table-id", default="")
    sync_kanban.add_argument(
        "--bitable-url",
        default="",
        help="Feishu Bitable/Base URL; app token and table query are extracted automatically",
    )
    _add_projection_backend_args(sync_kanban)
    sync_kanban.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="KEY=FEISHU_FIELD",
        help="Override a Bitable field name, e.g. task_id=TaskID",
    )
    sync_kanban.add_argument(
        "--include-archive-days",
        type=int,
        default=30,
        help="Include terminal task archives from the last N days (default: 30)",
    )
    sync_kanban.add_argument(
        "--active-only",
        action="store_true",
        help="Mirror only active kanban.json rows",
    )
    sync_kanban.add_argument(
        "--folder-token",
        default="",
        help="Optional Feishu folder token used when recreating a deleted Kanban table",
    )
    sync_kanban.add_argument(
        "--base-name",
        default="",
        help="Base name used when recreating a deleted Kanban table",
    )
    sync_kanban.add_argument(
        "--table-name",
        default="Kanban",
        help="Table name used when recreating a deleted Kanban table",
    )
    sync_kanban.add_argument(
        "--no-recreate-missing",
        action="store_true",
        help="Fail instead of recreating the Feishu Kanban target when it was deleted",
    )
    sync_kanban.add_argument(
        "--no-ensure-views",
        action="store_true",
        help="Skip creating Feishu Grid/Kanban views and Kanban grouping fields",
    )
    sync_kanban.add_argument(
        "--no-ensure-layouts",
        action="store_true",
        help="Skip configuring Feishu Grid/Kanban view layout properties",
    )
    sync_kanban.add_argument("--dry-run", action="store_true")
    sync_kanban.set_defaults(func=run_sync_kanban_table)

    from zf.cli.feishu_issue_projection import register as register_issue_projection

    register_issue_projection(feishu_subparsers)

    project_kanban = feishu_subparsers.add_parser(
        "project-kanban",
        help="Project task.status_changed events into Feishu Base",
    )
    project_kanban.add_argument("--state-dir", default=None)
    project_kanban.add_argument(
        "--backend",
        choices=["lark-cli"],
        default="",
    )
    project_kanban.add_argument("--app-token", default="")
    project_kanban.add_argument("--table-id", default="")
    project_kanban.add_argument(
        "--create-target-if-missing",
        action="store_true",
        help="Create a project-scoped Base/Table when no target is configured",
    )
    project_kanban.add_argument("--folder-token", default="")
    project_kanban.add_argument("--base-name", default="")
    project_kanban.add_argument("--table-name", default="")
    project_kanban.add_argument("--timezone", default="")
    project_mode = project_kanban.add_mutually_exclusive_group()
    project_mode.add_argument("--once", action="store_true")
    project_mode.add_argument("--watch", action="store_true")
    project_kanban.add_argument("--reconcile", action="store_true")
    project_kanban.add_argument("--poll-interval-seconds", type=float, default=0.0)
    project_kanban.set_defaults(func=_run_project_kanban)

    init_targets = feishu_subparsers.add_parser(
        "init-targets",
        help="Create Feishu Docx/Base/Table targets for Automation and Kanban sync",
    )
    init_targets.add_argument("--state-dir", default=None)
    init_targets.add_argument("--project-id", default="")
    init_targets.add_argument("--project-name", default="")
    init_targets.add_argument("--document-title", default="")
    init_targets.add_argument("--base-name", default="")
    init_targets.add_argument("--table-name", default="Kanban")
    init_targets.add_argument("--automation-table-name", default="Automation Insights")
    init_targets.add_argument(
        "--folder-token",
        default="",
        help="Optional Feishu folder token for the created Docx/Base",
    )
    init_targets.add_argument(
        "--timezone",
        default="Asia/Shanghai",
        help="Bitable timezone used when creating the Base",
    )
    _add_projection_backend_args(init_targets, default_backend="lark-cli")
    init_targets.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="KEY=FEISHU_FIELD",
        help="Override a Bitable field name before creating missing fields",
    )
    init_targets.add_argument(
        "--write-env",
        action="store_true",
        help="Write created target IDs into the project .env",
    )
    init_targets.add_argument(
        "--env-path",
        default="",
        help="Override .env path for --write-env (default: project_root/.env)",
    )
    init_targets.add_argument(
        "--overwrite-env",
        action="store_true",
        help="Replace existing target keys when writing .env",
    )
    init_targets.add_argument("--dry-run", action="store_true")
    init_targets.set_defaults(func=run_init_targets)

    cron = feishu_subparsers.add_parser(
        "cron-template",
        help="Print crontab entries for daily Automation and hourly Kanban sync",
    )
    cron.add_argument("--state-dir", default=None)
    cron.add_argument("--command", default="uv run zf")
    cron.add_argument("--daily-time", default="09:00", help="HH:MM in the cron host timezone")
    cron.add_argument("--hourly-minute", type=int, default=5)
    cron.add_argument(
        "--backend",
        choices=["lark-cli"],
        default="lark-cli",
        help="Projection backend rendered into generated cron commands",
    )
    cron.set_defaults(func=run_cron_template)


def _run_root(args: argparse.Namespace) -> int:
    print(
        "Usage: zf feishu "
        "{handle,push,serve,send-test,live-smoke,sync-automations,"
        "sync-automation-insights-table,sync-kanban-table,sync-issue-table,project-kanban,"
        "init-targets,cron-template} ...",
        file=sys.stderr,
    )
    return 1


def _add_projection_backend_args(
    parser: argparse.ArgumentParser,
    *,
    default_backend: str = "mock",
) -> None:
    parser.add_argument(
        "--backend",
        choices=["mock", "lark-cli"],
        default="",
        help=f"Docx/Base projection backend (default: {default_backend})",
    )
    parser.add_argument(
        "--transport",
        choices=["mock", "real"],
        default="",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(projection_backend_default=default_backend)


def _run_consume(args: argparse.Namespace) -> int:
    # Lazy import: the consume runner lives in a sibling module (cli/feishu.py
    # is already oversized; new inbound-transport behavior goes beside it).
    from zf.cli.feishu_consume import run_consume

    return run_consume(args)


def _run_bridge(args: argparse.Namespace) -> int:
    from zf.cli.feishu_consume import run_bridge

    return run_bridge(args)


def _run_project_kanban(args: argparse.Namespace) -> int:
    from zf.cli.feishu_projection import run_project_kanban

    return run_project_kanban(args)


def _run_live_smoke(args: argparse.Namespace) -> int:
    from zf.cli.feishu_live_smoke import run_live_smoke

    return run_live_smoke(args)


def run_handle(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        data = _load_event_json(args.event_json)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"Error: invalid Feishu event JSON: {e}", file=sys.stderr)
        return 1

    try:
        user_levels = _parse_user_levels(args.user_level)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    result = _handle_event_data(
        data,
        context=context,
        user_levels=user_levels,
        no_idempotency=getattr(args, "no_idempotency", False),
    )
    print(result.get("message") or "")
    return 0


def run_push(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    _load_project_env(context.project_root)
    if str(getattr(args, "transport", "mock") or "mock") == "real":
        production_issues = production_config_issues(
            context.config, require_enabled=True,
        )
        if production_issues:
            print(
                "Error: Feishu real transport production preflight failed: "
                + "; ".join(production_issues),
                file=sys.stderr,
            )
            return 2
    channels, receive_id_types = _parse_channel_targets(
        args.channel,
        default_to=getattr(args, "to", ""),
        default_receive_id_type=str(getattr(args, "receive_id_type", "chat_id") or "chat_id"),
    )
    if not channels:
        print("Error: provide --channel role=receive_id or --to receive_id", file=sys.stderr)
        return 1
    try:
        transport = _build_transport(args.transport)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    offset_store = OffsetStore(context.state_dir / "integrations" / "feishu" / "offset.json")
    if getattr(args, "from_beginning", False):
        offset_store.write(0)

    def tick() -> int:
        event_log = EventLog(context.state_dir / "events.jsonl")
        events, new_offset = event_log.read_from_offset(offset_store.read())
        router = ProjectionRouter(
            transport,
            RoutingConfig(
                channels=channels,
                receive_id_type=str(getattr(args, "receive_id_type", "chat_id") or "chat_id"),
                receive_id_types=receive_id_types,
            ),
            context.state_dir,
        )
        pushed = sum(1 for event in events if router.route_event(event))
        issue_cards_sent = issue_cards_updated = 0
        self_issue_cards_sent = self_issue_cards_updated = 0
        issue_target = str(getattr(args, "to", "") or "") or (
            next(iter(channels.values())) if channels else ""
        )
        if issue_target:
            try:
                _identity_for_issue = _feishu_identity(context.config)
                _issue_secret = (
                    os.environ.get(_identity_for_issue.action_token_secret_env, "").encode() or None
                    if _identity_for_issue is not None and _identity_for_issue.enabled
                    else None
                )
                issue_result = push_issue_cards_once(
                    context.state_dir,
                    transport,
                    receive_id=issue_target,
                    receive_id_type=str(getattr(args, "receive_id_type", "chat_id") or "chat_id"),
                    project_root=context.project_root,
                    config=context.config,
                    web_base_url=os.environ.get("ZF_WEB_BASE_URL", ""),
                    action_secret=_issue_secret,
                    action_ttl_seconds=(
                        int(getattr(_identity_for_issue, "action_token_ttl_seconds", 86400) or 86400)
                        if _identity_for_issue is not None else 86400
                    ),
                )
                issue_cards_sent = len(issue_result.get("sent", []))
                issue_cards_updated = len(issue_result.get("updated", []))
                self_issue_result = push_self_issue_cards_once(
                    context.state_dir,
                    transport,
                    receive_id=issue_target,
                    receive_id_type=str(getattr(args, "receive_id_type", "chat_id") or "chat_id"),
                    web_base_url=os.environ.get("ZF_WEB_BASE_URL", ""),
                    action_secret=_issue_secret,
                    action_ttl_seconds=(
                        int(getattr(_identity_for_issue, "action_token_ttl_seconds", 86400) or 86400)
                        if _identity_for_issue is not None else 86400
                    ),
                )
                self_issue_cards_sent = len(self_issue_result.get("sent", []))
                self_issue_cards_updated = len(self_issue_result.get("updated", []))
            except Exception as exc:
                print(f"issue-card push failed (workflow unaffected): {exc}", file=sys.stderr)
        owner_log = event_log_from_project(context.state_dir, config=context.config)
        try:
            owner_delivery = deliver_owner_visible_messages_once(
                event_log=owner_log,
                writer=EventWriter(owner_log),
                transport=transport,
                routing=RoutingConfig(
                    channels=channels,
                    receive_id_type=str(getattr(args, "receive_id_type", "chat_id") or "chat_id"),
                    receive_id_types=receive_id_types,
                ),
                max_attempts=max(int(getattr(args, "owner_visible_max_attempts", 1) or 1), 1),
            )
        finally:
            owner_log.close()
        # Plan-approval sidecar: push Plan Ready cards + verdict updates to the
        # operator target. Feishu errors must NOT break the tick or the gate —
        # the card is a notification; Kanban/CLI remain the fallback surface.
        plan_target = str(getattr(args, "to", "") or "") or (
            next(iter(channels.values())) if channels else ""
        )
        # feishu-A2: load the action-token secret so inline buttons get signed.
        # Absent secret → unsigned buttons (compat); verification side decides
        # whether to require signing (require_signed_actions).
        _identity = _feishu_identity(context.config)
        _action_secret = (
            os.environ.get(_identity.action_token_secret_env, "").encode() or None
            if _identity is not None and _identity.enabled
            else None
        )
        _action_ttl = (
            _identity.action_token_ttl_seconds if _identity is not None else 86400
        )
        plan_sent = plan_updated = 0
        if plan_target:
            try:
                plan_result = push_plan_approval_cards_once(
                    context.state_dir,
                    transport,
                    receive_id=plan_target,
                    receive_id_type=str(
                        getattr(args, "receive_id_type", "chat_id") or "chat_id"
                    ),
                    web_base_url=os.environ.get("ZF_WEB_BASE_URL", ""),
                    action_secret=_action_secret,
                    action_ttl_seconds=_action_ttl,
                )
                plan_sent = len(plan_result.get("sent", []))
                plan_updated = len(plan_result.get("updated", []))
            except Exception as exc:  # feishu unreachable → gate unaffected
                print(
                    f"plan-approval push failed (gate unaffected): {exc}",
                    file=sys.stderr,
                )
        # Replan owner-decision cards (报告场景 13): notify + deep-link to Web.
        # Same fallback rule — feishu errors never break the tick or the decision.
        replan_sent = replan_updated = 0
        if plan_target:
            try:
                replan_result = push_replan_cards_once(
                    context.state_dir,
                    transport,
                    receive_id=plan_target,
                    receive_id_type=str(
                        getattr(args, "receive_id_type", "chat_id") or "chat_id"
                    ),
                    web_base_url=os.environ.get("ZF_WEB_BASE_URL", ""),
                )
                replan_sent = len(replan_result.get("sent", []))
                replan_updated = len(replan_result.get("updated", []))
            except Exception as exc:  # feishu unreachable → decision unaffected
                print(
                    f"replan push failed (decision unaffected): {exc}",
                    file=sys.stderr,
                )
        channel_result_sent = 0
        try:
            channel_result = push_channel_result_cards_once(
                context.state_dir,
                transport,
                receive_id_type=str(
                    getattr(args, "receive_id_type", "chat_id") or "chat_id"
                ),
            )
            channel_result_sent = len(channel_result.get("sent", []))
        except Exception as exc:
            print(
                f"channel result push failed (receipt unaffected): {exc}",
                file=sys.stderr,
            )
        channel_progress_sent = channel_progress_updated = 0
        try:
            channel_progress = push_channel_progress_cards_once(
                context.state_dir,
                transport,
                action_secret=_action_secret,
                action_ttl_seconds=_action_ttl,
            )
            channel_progress_sent = len(channel_progress.get("sent", []))
            channel_progress_updated = len(channel_progress.get("updated", []))
        except Exception as exc:
            print(
                f"channel progress push failed (workflow unaffected): {exc}",
                file=sys.stderr,
            )
        # Streaming Q&A cards (feishu-stream P0-1): fold a reply's part.delta into
        # one typewriter card per request. Same fallback rule; §5.1 — deltas drive
        # the card only, never events.jsonl.
        stream_sent = stream_updated = 0
        stream_result: dict[str, Any] = {}
        if plan_target:
            try:
                stream_result = push_stream_card_once(
                    context.state_dir,
                    transport,
                    receive_id=plan_target,
                    receive_id_type=str(
                        getattr(args, "receive_id_type", "chat_id") or "chat_id"
                    ),
                )
                stream_sent = len(stream_result.get("sent", []))
                stream_updated = len(stream_result.get("updated", []))
            except Exception as exc:  # feishu unreachable → reply unaffected
                print(
                    f"stream push failed (reply unaffected): {exc}",
                    file=sys.stderr,
                )
        # Delivery is the readable fallback for replies that did not stream.
        # Stream projection runs first and returns stable request ids so both
        # projectors cannot emit a second card for the same answer.
        delivery_sent = delivery_updated = 0
        if plan_target:
            try:
                delivery_result = push_delivery_cards_once(
                    context.state_dir,
                    transport,
                    receive_id=plan_target,
                    receive_id_type=str(
                        getattr(args, "receive_id_type", "chat_id") or "chat_id"
                    ),
                    action_secret=_action_secret,
                    action_ttl_seconds=_action_ttl,
                    skip_request_ids=set(
                        stream_result.get("visible_request_ids", [])
                    ),
                )
                delivery_sent = len(delivery_result.get("sent", []))
                delivery_updated = len(delivery_result.get("updated", []))
            except Exception as exc:  # feishu unreachable → channel unaffected
                print(
                    f"delivery push failed (channel unaffected): {exc}",
                    file=sys.stderr,
                )
        # Run Manager cards: one live status card plus one human-decision card
        # per pending escalation. Same fallback rule; cards are projection-only,
        # button callbacks emit human.escalation.acknowledged through this CLI.
        run_manager_status_sent = run_manager_status_updated = 0
        run_manager_escalation_sent = run_manager_escalation_updated = 0
        if plan_target:
            try:
                run_manager_result = push_run_manager_cards_once(
                    context.state_dir,
                    transport,
                    receive_id=plan_target,
                    receive_id_type=str(
                        getattr(args, "receive_id_type", "chat_id") or "chat_id"
                    ),
                    action_secret=_action_secret,
                    action_ttl_seconds=_action_ttl,
                )
                run_manager_status_sent = int(
                    bool(run_manager_result.get("status_sent"))
                )
                run_manager_status_updated = int(
                    bool(run_manager_result.get("status_updated"))
                )
                run_manager_escalation_sent = len(
                    run_manager_result.get("escalation_sent", [])
                )
                run_manager_escalation_updated = len(
                    run_manager_result.get("escalation_updated", [])
                )
            except Exception as exc:  # feishu unreachable → run unaffected
                print(
                    f"run-manager push failed (run unaffected): {exc}",
                    file=sys.stderr,
                )
        # Channel discussion open-question cards (doc 122 §8): the owner-as-
        # oracle pager. Projection-only; button callbacks emit
        # channel.question.resolved through this CLI.
        question_sent = question_updated = 0
        if plan_target:
            try:
                from zf.integrations.feishu.channel_question_card import (
                    push_channel_question_cards_once,
                )

                question_result = push_channel_question_cards_once(
                    context.state_dir,
                    transport,
                    receive_id=plan_target,
                    receive_id_type=str(
                        getattr(args, "receive_id_type", "chat_id") or "chat_id"
                    ),
                    action_secret=_action_secret,
                    action_ttl_seconds=_action_ttl,
                )
                question_sent = len(question_result.get("sent", []))
                question_updated = len(question_result.get("updated", []))
            except Exception as exc:  # feishu unreachable → discussion unaffected
                print(
                    f"question-card push failed (discussion unaffected): {exc}",
                    file=sys.stderr,
                )
        proposal_sent = proposal_updated = 0
        kanban_plan_sent = kanban_plan_updated = 0
        if plan_target:
            try:
                kanban_plan_result = push_kanban_plan_cards_once(
                    context.state_dir,
                    transport,
                    receive_id=plan_target,
                    receive_id_type=str(
                        getattr(args, "receive_id_type", "chat_id") or "chat_id"
                    ),
                    action_secret=_action_secret,
                    action_ttl_seconds=_action_ttl,
                )
                kanban_plan_sent = len(kanban_plan_result.get("sent", []))
                kanban_plan_updated = len(kanban_plan_result.get("updated", []))
            except Exception as exc:
                print(
                    f"kanban Plan card push failed (conversation unaffected): {exc}",
                    file=sys.stderr,
                )
            try:
                proposal_result = push_kanban_proposal_cards_once(
                    context.state_dir,
                    transport,
                    receive_id=plan_target,
                    receive_id_type=str(
                        getattr(args, "receive_id_type", "chat_id") or "chat_id"
                    ),
                    action_secret=_action_secret,
                    action_ttl_seconds=_action_ttl,
                )
                proposal_sent = len(proposal_result.get("sent", []))
                proposal_updated = len(proposal_result.get("updated", []))
            except Exception as exc:
                print(
                    f"kanban proposal card push failed (proposal unaffected): {exc}",
                    file=sys.stderr,
                )
        offset_store.write(new_offset)
        print(
            f"Pushed {pushed} Feishu message(s); "
            f"owner_visible_delivered={owner_delivery.delivered} "
            f"owner_visible_failed={owner_delivery.failed}; "
            f"issue_cards_sent={issue_cards_sent} issue_cards_updated={issue_cards_updated}; "
            f"self_issue_cards_sent={self_issue_cards_sent} self_issue_cards_updated={self_issue_cards_updated}; "
            f"plan_cards_sent={plan_sent} plan_cards_updated={plan_updated}; "
            f"replan_cards_sent={replan_sent} replan_cards_updated={replan_updated}; "
            f"delivery_cards_sent={delivery_sent} delivery_cards_updated={delivery_updated}; "
            f"channel_result_cards_sent={channel_result_sent}; "
            f"channel_progress_cards_sent={channel_progress_sent} "
            f"channel_progress_cards_updated={channel_progress_updated}; "
            f"stream_cards_sent={stream_sent} stream_cards_updated={stream_updated}; "
            f"run_manager_status_sent={run_manager_status_sent} "
            f"run_manager_status_updated={run_manager_status_updated}; "
            f"run_manager_escalation_sent={run_manager_escalation_sent} "
            f"run_manager_escalation_updated={run_manager_escalation_updated}; "
            f"kanban_plan_cards_sent={kanban_plan_sent} "
            f"kanban_plan_cards_updated={kanban_plan_updated}; "
            f"kanban_proposal_cards_sent={proposal_sent} "
            f"kanban_proposal_cards_updated={proposal_updated}; "
            f"offset={new_offset}"
        )
        return pushed

    if not getattr(args, "watch", False) or getattr(args, "once", False):
        tick()
        return 0

    try:
        while True:
            tick()
            time.sleep(max(0.1, float(getattr(args, "interval", 2.0))))
    except KeyboardInterrupt:
        print("Stopped Feishu push watcher.")
        return 0


@dataclass(frozen=True)
class FeishuOperatorRequest:
    """A pending Feishu /zf ask message routed to the operator agent."""

    message: str
    chat_id: str
    user_id: str
    message_id: str
    event_id: str


def feishu_operator_requests(events: list[Any]) -> list[FeishuOperatorRequest]:
    """Filter events for user.message routed to the Feishu operator agent.

    ``zf feishu handle`` (the /zf ask path) emits
    ``user.message(source=feishu, target=feishu-operator-agent)``; this returns
    only those, in order, as structured requests.
    """
    requests: list[FeishuOperatorRequest] = []
    for event in events:
        if getattr(event, "type", "") != "user.message":
            continue
        payload = getattr(event, "payload", None) or {}
        if payload.get("source") != "feishu":
            continue
        if payload.get("target") != "feishu-operator-agent":
            continue
        message = str(payload.get("message") or "").strip()
        if not message:
            continue
        requests.append(FeishuOperatorRequest(
            message=message,
            chat_id=str(payload.get("chat_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            message_id=str(payload.get("message_id") or ""),
            event_id=str(getattr(event, "id", "") or ""),
        ))
    return requests


def build_feishu_operator_system_prompt() -> str:
    """Operator-agent system prompt: read state via the zf CLI, propose not write."""
    return (
        "You are ZaoFu's Feishu Operator Agent. Your role is operator and "
        "triage, not direct coding.\n"
        "Read runtime state with deterministic zf CLI commands in the current "
        "working directory, for example:\n"
        "  zf status\n"
        "  zf kanban --board\n"
        "  zf events --last 40\n"
        "  zf cost\n"
        "  zf handoff --format md\n"
        "Constraints; prefer read-only operations:\n"
        "- Never write .zf/kanban.json, events.jsonl, or runtime truth directly.\n"
        "- For changes, produce only an action_proposal. The orchestrator "
        "applies it through the controlled action service after approval.\n"
        "- Do not bypass gates, review, or verification, and do not control "
        "worker panes directly.\n"
        "- Reply in concise Chinese by default. State the conclusion first, "
        "then cite key evidence such as task IDs and events.\n"
    )


def _operator_timeout_s() -> float:
    return float(os.environ.get("ZF_FEISHU_OPERATOR_TIMEOUT_S", "120"))


def _resolve_operator_backend(name: str) -> Any:
    from zf.web.headless_agent import ClaudeHeadlessBackend, CodexHeadlessBackend

    factories = {
        "claude-headless": ClaudeHeadlessBackend,
        "codex-headless": CodexHeadlessBackend,
    }
    factory = factories.get(name or "codex-headless")
    if factory is None:
        raise ConfigError(
            f"Unknown operator backend {name!r}; expected claude-headless or codex-headless",
        )
    backend = factory()
    if not backend.available():
        raise ConfigError(f"{name} command is unavailable")
    return backend


def run_operate(
    args: argparse.Namespace,
    *,
    backend: Any | None = None,
    transport: FeishuTransport | None = None,
) -> int:
    """Drive the headless Feishu operator agent over pending /zf ask messages.

    Drains ``user.message(target=feishu-operator-agent)`` since the last
    operator offset, runs one headless turn per message (the agent reads state
    via the ``zf`` CLI in the project root), and replies to the originating
    Feishu chat. Reuses the channel headless backends + Feishu transport.
    """
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    _load_project_env(context.project_root)

    if transport is None:
        try:
            transport = _build_transport(getattr(args, "transport", "mock"))
        except ConfigError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if backend is None:
        try:
            backend = _resolve_operator_backend(getattr(args, "backend", "codex-headless"))
        except ConfigError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    offset_store = OffsetStore(
        context.state_dir / "integrations" / "feishu" / "operate-offset.json",
    )
    if getattr(args, "from_beginning", False):
        offset_store.write(0)

    event_log = EventLog(context.state_dir / "events.jsonl")
    events, new_offset = event_log.read_from_offset(offset_store.read())
    requests = feishu_operator_requests(events)

    writer = EventWriter(event_log_from_project(context.state_dir, config=context.config))
    system_prompt = build_feishu_operator_system_prompt()
    handled = 0
    for request in requests:
        handled += 1
        try:
            result = backend.run_turn(
                prompt=request.message,
                cwd=context.project_root,
                system_prompt=system_prompt,
                thread_id=f"feishu:{request.chat_id}",
                provider_session_id="",
                on_session_id=lambda _session_id: None,
                on_message=None,
                timeout_s=_operator_timeout_s(),
            )
        except Exception as exc:  # provider/runtime failure — record, keep draining
            _emit_operator_failed(writer, request, reason=str(exc))
            continue
        reply = str(getattr(result, "reply", "") or "").strip()
        if not bool(getattr(result, "ok", False)) or not reply:
            reason = str(
                getattr(result, "error", "")
                or getattr(result, "status", "")
                or "headless provider failed",
            )
            _emit_operator_failed(writer, request, reason=reason)
            continue
        transport.send_message(FeishuMessage(chat_id=request.chat_id, content=reply))
        writer.append(ZfEvent(
            type="feishu.notification.sent",
            actor="feishu-operator-agent",
            payload={
                "kind": "operator_reply",
                "chat_id": request.chat_id,
                "user_id": request.user_id,
                "message_id": request.message_id,
                "source_event_id": request.event_id,
                "backend": str(getattr(result, "backend", "") or ""),
                "provider_session_id": str(getattr(result, "provider_session_id", "") or ""),
            },
        ))
    offset_store.write(new_offset)
    print(f"Feishu operator handled {handled} message(s); offset={new_offset}")
    return 0


def _emit_operator_failed(
    writer: EventWriter,
    request: FeishuOperatorRequest,
    *,
    reason: str,
) -> None:
    writer.append(ZfEvent(
        type="feishu.notification.failed",
        actor="feishu-operator-agent",
        payload={
            "kind": "operator_reply",
            "chat_id": request.chat_id,
            "message_id": request.message_id,
            "source_event_id": request.event_id,
            "reason": reason,
        },
    ))


def run_serve(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        user_levels = _parse_user_levels(args.user_level)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    production_issues = production_config_issues(
        context.config,
        require_enabled=True,
        require_callback_signature=True,
    )
    if production_issues:
        print(
            "Error: Feishu webhook production preflight failed: "
            + "; ".join(production_issues),
            file=sys.stderr,
        )
        return 2

    no_idempotency = bool(getattr(args, "no_idempotency", False))
    identity = _feishu_identity(context.config)
    verify_token = (
        os.environ.get(identity.verification_token_env, "")
        if identity is not None and identity.enabled
        else ""
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length)
            # feishu-B: verify the request signature before parsing when an
            # identity map is enabled. Forged/replayed callbacks are rejected
            # and audited; no event reaches the mutation path.
            if identity is not None and identity.enabled:
                ok, reason = verify_feishu_signature(
                    timestamp=self.headers.get("X-Lark-Request-Timestamp", ""),
                    nonce=self.headers.get("X-Lark-Request-Nonce", ""),
                    token=verify_token,
                    body=raw,
                    signature=self.headers.get("X-Lark-Signature", ""),
                    now=time.time(),
                    max_age_seconds=identity.replay_window_seconds,
                )
                if not ok:
                    _audit_signature_invalid(context, reason=reason)
                    _send_json(self, 401, {"ok": False, "reason": reason})
                    return
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                _send_json(self, 400, {"ok": False, "reason": "invalid JSON"})
                return
            if isinstance(data, dict) and "challenge" in data:
                _send_json(self, 200, {"challenge": data.get("challenge")})
                return
            result = _handle_event_data(
                data,
                context=context,
                user_levels=user_levels,
                no_idempotency=no_idempotency,
            )
            _send_json(self, 200, result)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer((args.host, int(args.port)), Handler)
    print(f"Feishu webhook server listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopped Feishu webhook server.")
    finally:
        server.server_close()
    return 0


def _feishu_identity(config: object | None):
    """Best-effort reach config.integrations.feishu_identity (may be absent)."""
    integrations = getattr(config, "integrations", None)
    return getattr(integrations, "feishu_identity", None)


def _audit_callback_rejected(
    context: ProjectContext,
    envelope: FeishuCommandEnvelope,
    *,
    reason: str,
) -> None:
    """Write an audit event for a denied inbound callback (no mutation)."""
    try:
        writer = EventWriter(
            event_log_from_project(context.state_dir, config=context.config)
        )
        writer.emit(
            "callback.rejected",
            actor=f"feishu:{envelope.user_id or 'unknown'}",
            payload={
                "reason": reason,
                "command": envelope.command,
                "user_id": envelope.user_id,
                "chat_id": envelope.chat_id,
                "source": envelope.source,
            },
        )
    except Exception:
        # Audit is best-effort; the denial itself already stands.
        pass


def _audit_identity_binding_requested(
    context: ProjectContext,
    envelope: FeishuCommandEnvelope,
    *,
    reason: str,
) -> None:
    """Record a safe follow-up for unmapped Feishu principals.

    This is intentionally not an authorization path. It gives operators and
    dashboards a deterministic artifact to review while the callback remains
    fail-closed.
    """
    try:
        writer = EventWriter(
            event_log_from_project(context.state_dir, config=context.config)
        )
        writer.emit(
            "identity.binding.requested",
            actor=f"feishu:{envelope.user_id or 'unknown'}",
            payload={
                "schema_version": "identity-binding-request.v1",
                "provider": "feishu",
                "principal_id": envelope.user_id,
                "chat_id": envelope.chat_id,
                "command": envelope.command,
                "source": envelope.source,
                "reason": reason,
                "status": "pending",
                "suggested_action": "add integrations.feishu_identity.users entry in zf.yaml",
            },
        )
    except Exception:
        pass


def _audit_signature_invalid(
    context: ProjectContext, *, reason: str
) -> None:
    """Write an audit event for a callback that failed signature verification."""
    try:
        writer = EventWriter(
            event_log_from_project(context.state_dir, config=context.config)
        )
        writer.emit(
            "callback.signature.invalid",
            actor="feishu:unverified",
            payload={"reason": reason, "surface": "feishu-webhook"},
        )
    except Exception:
        pass


def _issue_write_route_allowed(event, context) -> bool:
    """Require an exact configured Feishu project chat for Issue mutations."""
    integrations = getattr(context.config, "integrations", None)
    routing = getattr(integrations, "feishu_routing", None)
    if not isinstance(routing, dict) or not routing:
        # Compatibility fixtures and disabled local integrations have no route
        # table; real transport preflight refuses those configurations.
        return True
    chat_id = str(getattr(event, "chat_id", "") or "").strip()
    payload = getattr(event, "payload", {}) or {}
    app_id = str(payload.get("app_id") or "").strip()
    bot_open_id = str(payload.get("bot_open_id") or "").strip()
    candidates = [chat_id]
    if app_id:
        candidates.append(f"{app_id}:{chat_id}")
    if bot_open_id:
        candidates.extend((f"{chat_id}#{bot_open_id}", f"{chat_id}@{bot_open_id}"))
    return any(key in routing for key in candidates if key and key != "*")


def _verify_signed_action(
    event,
    envelope: FeishuCommandEnvelope,
    context: ProjectContext,
) -> tuple[bool, str]:
    """feishu-A2: verify a signed action token for a mutation button.

    Compat: with require_signed_actions=False, an unsigned click is accepted
    (in-flight cards keep working) but a *present* token is still verified so a
    bad token is never honored. With require_signed_actions=True, a valid token
    is mandatory. Returns (ok, reason); reason is an audit code on failure.
    """
    identity = _feishu_identity(context.config)
    if identity is None or not identity.enabled:
        return True, "no_identity"
    token = str(
        getattr(envelope, "action_token", "")
        or getattr(event, "payload", {}).get("action_token")
        or ""
    )
    secret = os.environ.get(identity.action_token_secret_env, "").encode()
    require = bool(identity.require_signed_actions)
    if not (secret and token):
        if require:
            return False, "token_required"
        return True, "unsigned_compat"
    store = IdempotencyStore(
        context.state_dir / "integrations" / "feishu" / "token_nonce.jsonl",
    )
    target = (
        ":".join(envelope.args)
        if envelope.command in ISSUE_MUTATION_SIGNED_COMMANDS
        and envelope.command != "self-issue-session-answer"
        else (envelope.args[0] if envelope.args else "")
    )
    return verify_action(
        token,
        secrets_by_version={"1": secret},
        expect_action=envelope.command,
        expect_target=target,
        expect_chat_id=envelope.chat_id,
        now=time.time(),
        consume_nonce=lambda n: not store.check_and_record(
            n, command=envelope.command, user_id=envelope.user_id,
            chat_id=envelope.chat_id, source="token",
        ),
    )


def _handle_event_data(
    data: dict,
    *,
    context: ProjectContext,
    user_levels: dict[str, AuthLevel],
    no_idempotency: bool = False,
    transport: FeishuTransport | None = None,
) -> dict:
    event = MockFeishuTransport().parse_webhook(data)
    if event is None:
        return {"ok": True, "status": "ignored", "message": "Ignored: unsupported Feishu event payload"}

    # feishu-B trust model: when an identity map is configured it is the sole
    # source of permissions (control-plane resident). Operator-supplied
    # --user-level is ignored so callbacks cannot self-assert a level, and any
    # unmapped principal stays VIEWER → fail-closed on every mutation.
    identity = _feishu_identity(context.config)
    if identity is not None and identity.enabled:
        user_levels = identity_auth_levels(identity)

    gateway = CommandGateway(user_levels=user_levels)
    envelope = gateway.parse(event)
    if envelope is None:
        return {"ok": True, "status": "ignored", "message": "Ignored: unsupported Feishu command"}

    from zf.integrations.feishu.issue_connection import issue_scope_rejection
    rejection = issue_scope_rejection(context.config, event, envelope.command)
    if rejection:
        return rejection

    if not gateway.is_authorized(envelope):
        reject_reason = "identity.unmapped"
        if identity is not None and identity.enabled:
            if resolve_identity(identity, envelope.user_id or "") is not None:
                reject_reason = "identity.insufficient_permission"
        _audit_callback_rejected(context, envelope, reason=reject_reason)
        if reject_reason == "identity.unmapped":
            _audit_identity_binding_requested(
                context,
                envelope,
                reason=reject_reason,
            )
        message = (
            f"Rejected: user {envelope.user_id or 'unknown'} "
            f"is not authorized for /zf {envelope.command}"
        )
        return {"ok": False, "status": "rejected", "message": message}

    if envelope.command in ISSUE_WRITE_COMMANDS and not _issue_write_route_allowed(event, context):
        _audit_callback_rejected(context, envelope, reason="route.unmapped_or_wildcard")
        return {
            "ok": False,
            "status": "rejected",
            "message": "Rejected: Issue write requires an exact Feishu project chat route",
        }

    idempotency_store = None
    if not no_idempotency:
        idempotency_store = IdempotencyStore(
            context.state_dir / "integrations" / "feishu" / "idempotency.jsonl",
        )
        if idempotency_store.contains(envelope.idempotency_key):
            message = f"Duplicate: {envelope.idempotency_key}"
            return {"ok": True, "status": "duplicate", "message": message}

    # A Feishu form can be submitted once with missing fields and then fixed in
    # place. Validate its presentation values before consuming the signed nonce;
    # only a syntactically complete Plan answer becomes a one-shot callback.
    form_preflight = _preflight_kanban_plan_form(
        envelope,
        context.state_dir,
        config=context.config,
    )
    if form_preflight is not None:
        return form_preflight
    if envelope.command == "self-issue-session-answer" and not (
        str(envelope.form_values.get("answer") or "").strip()
        or " ".join(envelope.args[1:]).strip()
        or envelope.attachments
        or envelope.attachment_message_id
    ):
        return {"ok": False, "status": "invalid_payload", "message": "answer is required"}

    # feishu-A2: mutation buttons must carry a valid signed action token bound to
    # (action, target, chat, expiry, nonce). Composes with the identity gate
    # above — both must pass. The nonce store also makes a signed click single-use.
    # Exact duplicate callbacks are returned above before consuming the nonce
    # again; new callbacks with a replayed token still fail closed here.
    # The legacy client cannot render form containers, so Self-Issue answer
    # JSON is accepted as a text command. It remains constrained by the
    # operator identity gate and exact project-chat route above; all other
    # mutation text commands still require a signed action token.
    text_intake_save = (
        os.environ.get("ZF_FEISHU_ALLOW_TEXT_INTAKE_SAVE", "").lower()
        in {"1", "true", "yes", "on"}
        and
        envelope.source == "text"
        and envelope.command in {
            "self-issue-save", "self-issue-intake-save",
            "self-issue-submit", "self-issue-intake-submit",
        }
        and len(envelope.args) > (2 if "save" in envelope.command else 1)
    )
    if text_intake_save and "save" in envelope.command:
        try:
            text_intake_save = isinstance(json.loads(str(envelope.args[2])), dict)
        except (TypeError, ValueError):
            text_intake_save = False
    if envelope.command in SIGNED_ACTION_COMMANDS and not text_intake_save:
        token_ok, token_reason = _verify_signed_action(event, envelope, context)
        if not token_ok:
            _audit_callback_rejected(
                context, envelope, reason=f"token.{token_reason}"
            )
            return {
                "ok": False,
                "status": "rejected",
                "message": f"Rejected: invalid action token ({token_reason})",
            }

    if not no_idempotency:
        assert idempotency_store is not None
        duplicate = idempotency_store.check_and_record(
            envelope.idempotency_key,
            command=envelope.command,
            user_id=envelope.user_id,
            chat_id=envelope.chat_id,
            source=envelope.source,
        )
        if duplicate:
            message = f"Duplicate: {envelope.idempotency_key}"
            return {"ok": True, "status": "duplicate", "message": message}

    context.state_dir.mkdir(parents=True, exist_ok=True)

    if envelope.command in QUERY_COMMANDS:
        message = QueryExecutor(context.state_dir).execute(envelope)
        return {"ok": True, "status": "completed", "message": message}

    if envelope.command in ISSUE_COMMANDS:
        return handle_issue_command(
            envelope,
            state_dir=context.state_dir,
            config=context.config,
            project_root=context.project_root,
            transport=transport,
        )

    if envelope.command in CONTROL_COMMANDS:
        message = ControlHandler(context.state_dir).execute(envelope)
        return {"ok": True, "status": "completed", "message": message}

    if envelope.command == "ask":
        return _handle_ask_result(envelope, context.state_dir, config=context.config)

    if envelope.command in CONTROLLED_ACTION_COMMANDS:
        return _handle_controlled_action_result(
            envelope,
            context.state_dir,
            config=context.config,
        )

    if envelope.command in ATTENTION_COMMANDS:
        return _handle_attention_action_result(
            envelope,
            context.state_dir,
            config=context.config,
        )

    if envelope.command in PLAN_APPROVAL_COMMANDS:
        return _handle_plan_approval_result(
            envelope,
            context.state_dir,
            config=context.config,
        )

    if envelope.command in AGENT_CANCEL_COMMANDS:
        return _handle_agent_cancel_result(
            envelope,
            context.state_dir,
            config=context.config,
        )

    if envelope.command in ATTENTION_ACK_COMMANDS:
        return _handle_attention_ack_result(
            envelope,
            context.state_dir,
            config=context.config,
        )

    if envelope.command in HUMAN_DECISION_COMMANDS:
        return _handle_human_decision_result(
            envelope,
            context.state_dir,
            config=context.config,
        )

    if envelope.command in CHANNEL_QUESTION_COMMANDS:
        return _handle_channel_question_result(
            envelope,
            context.state_dir,
            config=context.config,
        )
    if envelope.command in KANBAN_PLAN_COMMANDS:
        return _handle_kanban_plan_result(
            envelope,
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
        )
    if envelope.command in KANBAN_PROPOSAL_COMMANDS:
        return _handle_kanban_proposal_result(
            envelope,
            context.state_dir,
            config=context.config,
            project_root=context.project_root,
        )
    if envelope.command in CHANNEL_PROGRESS_COMMANDS:
        target = envelope.args[0] if envelope.args else ""
        return handle_channel_progress_action(
            command=envelope.command,
            target=target,
            context=context,
            user_id=envelope.user_id,
            chat_id=envelope.chat_id,
            message_id=envelope.message_id,
        )

    if envelope.command in APPROVAL_COMMANDS:
        return _handle_approval_result(
            envelope,
            context.state_dir,
            config=context.config,
        )

    message = f"Accepted but not implemented: /zf {envelope.command}"
    return {"ok": True, "status": "not_implemented", "message": message}


def run_send_test(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    idempotency_path = (
        context.state_dir / "integrations" / "feishu" / "idempotency.jsonl"
    )
    _load_project_env(context.project_root)
    print("Feishu bridge local config OK")
    print(f"state_dir: {context.state_dir}")
    print(f"idempotency: {idempotency_path}")
    print(f"transport: {args.transport}")
    if args.to:
        try:
            transport = _build_transport(args.transport)
            transport.send_message(FeishuMessage(
                chat_id=args.to,
                content=args.message,
                receive_id_type=args.receive_id_type,
            ))
        except (ConfigError, FeishuTransportError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print(f"sent test message to {args.receive_id_type}:{args.to}")
    return 0


def run_sync_automations(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        backend = _resolve_projection_backend(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    _load_project_env(context.project_root)
    project_id, project_name = _project_identity(context, args)
    document_id = _resolve_document_id(args)
    if not document_id and not getattr(args, "dry_run", False):
        print(
            "Error: provide --document-id/--document-url or FEISHU_AUTOMATION_DOCUMENT_ID",
            file=sys.stderr,
        )
        return 1

    try:
        result = sync_automation_document(
            state_dir=context.state_dir,
            project_id=project_id,
            project_name=project_name,
            document_id=document_id or "dry-run",
            client=_build_document_client(backend),
            ledger=FeishuSyncLedger.for_state_dir(context.state_dir),
            writer=None if args.dry_run else EventWriter(event_log_from_project(
                context.state_dir,
                config=context.config,
            )),
            automation_ids=list(args.automation) or None,
            dry_run=bool(args.dry_run),
        )
    except (ValueError, FeishuTransportError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if result.get("dry_run"):
        print(result["markdown"], end="")
        return 0
    print(
        "Synced Automation reports to Feishu document "
        f"{document_id}: {int(result.get('blocks') or 0)} block(s)"
    )
    return 0


def run_sync_automation_insights_table(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        field_map = _parse_field_map(args.field)
        backend = _resolve_projection_backend(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    _load_project_env(context.project_root)
    project_id, project_name = _project_identity(context, args)
    app_token, table_id = _resolve_automation_bitable_target(args)
    if not app_token and not getattr(args, "dry_run", False):
        print(
            "Error: provide --bitable-url/--app-token or "
            "FEISHU_AUTOMATION_BITABLE_APP_TOKEN/FEISHU_BITABLE_APP_TOKEN",
            file=sys.stderr,
        )
        return 1

    try:
        client = _build_bitable_client(backend)
    except (ConfigError, FeishuTransportError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if (
        app_token
        and not table_id
        and not getattr(args, "dry_run", False)
        and not getattr(args, "no_create_table", False)
    ):
        try:
            app_token, table_id, message = _create_automation_insight_table_target(
                args,
                context=context,
                client=client,
                app_token=app_token,
                project_name=project_name,
                field_map=field_map,
            )
            print(message)
        except (ValueError, FeishuTransportError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    if not table_id and not getattr(args, "dry_run", False):
        print(
            "Error: provide --table-id or FEISHU_AUTOMATION_BITABLE_TABLE_ID "
            "(or allow table creation)",
            file=sys.stderr,
        )
        return 1

    try:
        schema: dict[str, Any] = {"created": [], "existing": []}
        views: dict[str, Any] = {"created": [], "existing": []}
        layouts: dict[str, Any] = {"configured": [], "skipped": []}
        layout_warning = ""
        if not getattr(args, "dry_run", False):
            if getattr(args, "no_ensure_views", False):
                schema = client.ensure_fields(
                    app_token,
                    table_id,
                    automation_insight_field_specs(field_map),
                )
            else:
                schema, views, layouts, layout_warning = _ensure_automation_insight_table_shape(
                    args,
                    client=client,
                    app_token=app_token,
                    table_id=table_id,
                    field_map=field_map,
                )
        result = sync_automation_bitable(
            state_dir=context.state_dir,
            project_id=project_id,
            project_name=project_name,
            app_token=app_token or "dry-run-app",
            table_id=table_id or "dry-run-table",
            client=client,
            ledger=FeishuSyncLedger.for_state_dir(context.state_dir),
            writer=None if args.dry_run else EventWriter(event_log_from_project(
                context.state_dir,
                config=context.config,
            )),
            field_map=field_map,
            automation_ids=list(args.automation) or None,
            dry_run=bool(args.dry_run),
        )
        result["schema"] = schema
        result["views"] = views
        result["layouts"] = layouts
        result["layout_warning"] = layout_warning
    except (ValueError, FeishuTransportError) as e:
        if (
            isinstance(e, FeishuTransportError)
            and not getattr(args, "dry_run", False)
            and not getattr(args, "no_recreate_missing", False)
            and _is_deleted_feishu_resource_error(e)
        ):
            try:
                app_token, table_id, recreate_message = _create_automation_insight_table_target(
                    args,
                    context=context,
                    client=client,
                    app_token=app_token,
                    project_name=project_name,
                    field_map=field_map,
                )
                result = sync_automation_bitable(
                    state_dir=context.state_dir,
                    project_id=project_id,
                    project_name=project_name,
                    app_token=app_token,
                    table_id=table_id,
                    client=client,
                    ledger=FeishuSyncLedger.for_state_dir(context.state_dir),
                    writer=EventWriter(event_log_from_project(
                        context.state_dir,
                        config=context.config,
                    )),
                    field_map=field_map,
                    automation_ids=list(args.automation) or None,
                    dry_run=False,
                )
                result["target_recreated"] = True
                result["recreate_message"] = recreate_message
                result.setdefault("schema", {})
                result.setdefault("views", {})
                result.setdefault("layouts", {})
                result.setdefault("layout_warning", "")
            except (ValueError, FeishuTransportError) as recreate_error:
                print(f"Error: {recreate_error}", file=sys.stderr)
                return 1
        else:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if result.get("dry_run"):
        print(result["markdown"], end="")
        return 0
    if result.get("target_recreated"):
        print(str(result.get("recreate_message") or "Recreated Automation Insights table"))
    print(
        "Synced Automation insights to Feishu Bitable "
        f"{table_id}: rows={result['rows']} created={result['created']} "
        f"recreated={result.get('recreated', 0)} updated={result['updated']} "
        f"stale_updated={result.get('stale_updated', 0)}"
    )
    schema = result.get("schema") if isinstance(result.get("schema"), dict) else {}
    views = result.get("views") if isinstance(result.get("views"), dict) else {}
    layouts = result.get("layouts") if isinstance(result.get("layouts"), dict) else {}
    if schema or views or layouts:
        print(
            "Automation Base views: "
            f"fields_created={len(schema.get('created') or [])} "
            f"views_created={len(views.get('created') or [])} "
            f"views_configured={len(layouts.get('configured') or [])}"
        )
    if result.get("layout_warning"):
        print(f"Automation Base layout warning: {result['layout_warning']}", file=sys.stderr)
    return 0


def run_sync_kanban_table(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        field_map = _parse_field_map(args.field)
        backend = _resolve_projection_backend(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    _load_project_env(context.project_root)
    project_id, project_name = _project_identity(context, args)
    app_token, table_id = _resolve_bitable_target(args)
    if not (app_token and table_id) and not getattr(args, "dry_run", False):
        print(
            "Error: provide --bitable-url or --app-token/--table-id "
            "or FEISHU_BITABLE_APP_TOKEN/FEISHU_BITABLE_TABLE_ID",
            file=sys.stderr,
        )
        return 1
    include_archive_days = (
        None if args.active_only else max(0, int(args.include_archive_days))
    )
    try:
        client = _build_bitable_client(backend)
    except (ConfigError, FeishuTransportError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    try:
        schema: dict[str, Any] = {"created": [], "existing": []}
        views: dict[str, Any] = {"created": [], "existing": []}
        layouts: dict[str, Any] = {"configured": [], "skipped": []}
        if not getattr(args, "dry_run", False) and not getattr(args, "no_ensure_views", False):
            schema = client.ensure_fields(app_token, table_id, kanban_field_specs(field_map))
            views = client.ensure_views(app_token, table_id, kanban_view_specs())
            if not getattr(args, "no_ensure_layouts", False):
                layouts = client.ensure_view_layouts(
                    app_token,
                    table_id,
                    kanban_view_layout_specs(field_map),
                )
        result = sync_kanban_bitable(
            state_dir=context.state_dir,
            project_id=project_id,
            project_name=project_name,
            app_token=app_token or "dry-run-app",
            table_id=table_id or "dry-run-table",
            client=client,
            ledger=FeishuSyncLedger.for_state_dir(context.state_dir),
            writer=None if args.dry_run else EventWriter(event_log_from_project(
                context.state_dir,
                config=context.config,
            )),
            field_map=field_map,
            include_archive_days=include_archive_days,
            dry_run=bool(args.dry_run),
        )
        result["schema"] = schema
        result["views"] = views
        result["layouts"] = layouts
    except (ValueError, FeishuTransportError) as e:
        if (
            isinstance(e, FeishuTransportError)
            and not getattr(args, "dry_run", False)
            and not getattr(args, "no_recreate_missing", False)
            and _is_deleted_feishu_resource_error(e)
        ):
            try:
                app_token, table_id, recreate_message = _recreate_kanban_bitable_target(
                    args,
                    context=context,
                    client=client,
                    project_name=project_name,
                    field_map=field_map,
                )
                result = sync_kanban_bitable(
                    state_dir=context.state_dir,
                    project_id=project_id,
                    project_name=project_name,
                    app_token=app_token,
                    table_id=table_id,
                    client=client,
                    ledger=FeishuSyncLedger.for_state_dir(context.state_dir),
                    writer=EventWriter(event_log_from_project(
                        context.state_dir,
                        config=context.config,
                    )),
                    field_map=field_map,
                    include_archive_days=include_archive_days,
                    dry_run=False,
                )
                result["target_recreated"] = True
                result["recreate_message"] = recreate_message
                result.setdefault("schema", {})
                result.setdefault("views", {})
                result.setdefault("layouts", {})
            except (ConfigError, ValueError, FeishuTransportError) as recreate_error:
                print(f"Error: {recreate_error}", file=sys.stderr)
                return 1
        else:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    if result.get("dry_run"):
        print(result["markdown"], end="")
        return 0
    if result.get("target_recreated"):
        print(str(result.get("recreate_message") or "Recreated Feishu Kanban target"))
    print(
        "Synced Kanban table to Feishu Bitable "
        f"{table_id}: rows={result['rows']} created={result['created']} "
        f"recreated={result.get('recreated', 0)} updated={result['updated']}"
    )
    schema = result.get("schema") if isinstance(result.get("schema"), dict) else {}
    views = result.get("views") if isinstance(result.get("views"), dict) else {}
    layouts = result.get("layouts") if isinstance(result.get("layouts"), dict) else {}
    if schema or views or layouts:
        print(
            "Kanban Base layout: "
            f"fields_created={len(schema.get('created') or [])} "
            f"views_created={len(views.get('created') or [])} "
            f"views_configured={len(layouts.get('configured') or [])}"
        )
    return 0


def run_init_targets(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        field_map = _parse_field_map(args.field)
        backend = _resolve_projection_backend(args)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    _load_project_env(context.project_root)
    project_id, project_name = _project_identity(context, args)
    document_title = (
        str(getattr(args, "document_title", "") or "").strip()
        or f"ZaoFu Automation Reports - {project_name}"
    )
    base_name = (
        str(getattr(args, "base_name", "") or "").strip()
        or f"ZaoFu Kanban - {project_name}"
    )
    table_name = str(getattr(args, "table_name", "") or "").strip() or "Kanban"
    automation_table_name = (
        str(getattr(args, "automation_table_name", "") or "").strip()
        or "Automation Insights"
    )
    folder_token = (
        str(getattr(args, "folder_token", "") or "").strip()
        or os.environ.get("FEISHU_FOLDER_TOKEN", "").strip()
    )
    field_specs = kanban_field_specs(field_map)

    if getattr(args, "dry_run", False):
        print("# Feishu init-targets dry-run")
        print(f"project_id: {project_id}")
        print(f"project_name: {project_name}")
        print(f"document_title: {document_title}")
        print(f"base_name: {base_name}")
        print(f"table_name: {table_name}")
        print(f"automation_table_name: {automation_table_name}")
        if folder_token:
            print(f"folder_token: {folder_token}")
        print("fields:")
        for spec in field_specs:
            print(f"- {spec['field_name']}")
        if getattr(args, "write_env", False):
            print("# --write-env is not applied during dry-run")
        return 0

    try:
        document_client = _build_document_client(backend)
        bitable_client = _build_bitable_client(backend)
        document = document_client.create_document(
            title=document_title,
            folder_token=folder_token,
            content=_initial_document_content(project_id, project_name),
        )
        document_id = _document_id_from_result(document)
        base = bitable_client.create_base(
            name=base_name,
            folder_token=folder_token,
            time_zone=str(getattr(args, "timezone", "") or ""),
        )
        app_token = _app_token_from_result(base)
        table = bitable_client.create_table(app_token, name=table_name)
        table_id = _table_id_from_result(table)
        schema = bitable_client.ensure_fields(app_token, table_id, field_specs)
        views = bitable_client.ensure_views(app_token, table_id, kanban_view_specs())
        layouts = bitable_client.ensure_view_layouts(
            app_token,
            table_id,
            kanban_view_layout_specs(field_map),
        )
        automation_table = bitable_client.create_table(
            app_token,
            name=automation_table_name,
        )
        automation_table_id = _table_id_from_result(automation_table)
        (
            automation_schema,
            automation_views,
            automation_layouts,
            automation_layout_warning,
        ) = _ensure_automation_insight_table_shape(
            args,
            client=bitable_client,
            app_token=app_token,
            table_id=automation_table_id,
            field_map=field_map,
        )
    except (ConfigError, ValueError, FeishuTransportError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    env_values = {
        "FEISHU_AUTOMATION_DOCUMENT_ID": document_id,
        "FEISHU_AUTOMATION_BITABLE_APP_TOKEN": app_token,
        "FEISHU_AUTOMATION_BITABLE_TABLE_ID": automation_table_id,
        "FEISHU_BITABLE_APP_TOKEN": app_token,
        "FEISHU_BITABLE_TABLE_ID": table_id,
    }
    print("Created Feishu sync targets")
    print(f"document_id: {document_id}")
    if document.get("url"):
        print(f"document_url: {document['url']}")
    print(f"bitable_app_token: {app_token}")
    print(f"bitable_table_id: {table_id}")
    print(f"automation_bitable_table_id: {automation_table_id}")
    if base.get("url"):
        print(f"bitable_url: {base['url']}?table={table_id}")
    print(
        "fields: "
        f"created={len(schema.get('created') or [])} "
        f"existing={len(schema.get('existing') or [])}"
    )
    print(
        "views: "
        f"created={len(views.get('created') or [])} "
        f"existing={len(views.get('existing') or [])} "
        f"configured={len(layouts.get('configured') or [])}"
    )
    print(
        "automation_views: "
        f"fields_created={len(automation_schema.get('created') or [])} "
        f"views_created={len(automation_views.get('created') or [])} "
        f"views_configured={len(automation_layouts.get('configured') or [])}"
    )
    if automation_layout_warning:
        print(f"automation_layout_warning: {automation_layout_warning}")
    print("")
    for key, value in env_values.items():
        print(f"{key}={value}")

    if getattr(args, "write_env", False):
        env_arg = str(getattr(args, "env_path", "") or "").strip()
        env_path = Path(env_arg) if env_arg else context.project_root / ".env"
        if not env_path.is_absolute():
            env_path = context.project_root / env_path
        result = update_env_file(
            env_path,
            env_values,
            overwrite=bool(getattr(args, "overwrite_env", False)),
        )
        print("")
        print(f".env: {result.path}")
        if result.written:
            print(f"written: {', '.join(sorted(result.written))}")
        if result.updated:
            print(f"updated: {', '.join(sorted(result.updated))}")
        if result.skipped:
            print(f"skipped existing: {', '.join(sorted(result.skipped))}")
    return 0


def run_cron_template(args: argparse.Namespace) -> int:
    try:
        context = resolve_project_context(
            explicit_state_dir=getattr(args, "state_dir", None),
        )
        hour, minute = _parse_hhmm(args.daily_time)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    hourly_minute = max(0, min(59, int(args.hourly_minute)))
    root = shlex.quote(str(context.project_root))
    command = str(args.command).strip() or "uv run zf"
    log_dir = shlex.quote(str(context.state_dir / "logs"))
    state_flag = f"--state-dir {shlex.quote(str(context.state_dir))}"
    backend_flag = f"--backend {shlex.quote(str(args.backend))}"
    print("# Daily Automation -> Feishu Docx")
    print(
        f"{minute} {hour} * * * cd {root} && mkdir -p {log_dir} && "
        f"{command} feishu sync-automations {state_flag} {backend_flag} "
        "--document-id \"$FEISHU_AUTOMATION_DOCUMENT_ID\" "
        f">> {log_dir}/feishu-automation-sync.log 2>&1"
    )
    print("")
    print("# Daily Automation Insights -> Feishu Bitable")
    print(
        f"{minute} {hour} * * * cd {root} && mkdir -p {log_dir} && "
        f"{command} feishu sync-automation-insights-table {state_flag} {backend_flag} "
        f">> {log_dir}/feishu-automation-insights-sync.log 2>&1"
    )
    print("")
    print("# Hourly Kanban -> Feishu Bitable")
    print(
        f"{hourly_minute} * * * * cd {root} && mkdir -p {log_dir} && "
        f"{command} feishu sync-kanban-table {state_flag} {backend_flag} "
        "--app-token \"$FEISHU_BITABLE_APP_TOKEN\" "
        "--table-id \"$FEISHU_BITABLE_TABLE_ID\" "
        f">> {log_dir}/feishu-kanban-sync.log 2>&1"
    )
    return 0


def _load_event_json(value: str) -> dict[str, Any]:
    if value == "-":
        text = sys.stdin.read()
    else:
        candidate = Path(value)
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8")
        else:
            text = value
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("top-level JSON must be an object")
    return data


# doc 78 O-7 fix: single canonical dotenv loader (was duplicated here +
# in web.py). Alias keeps the existing call sites unchanged.
from zf.core.config.project_context import (  # noqa: E402
    load_project_env as _load_project_env,
)


def _parse_user_levels(items: list[str]) -> dict[str, AuthLevel]:
    levels: dict[str, AuthLevel] = {}
    for item in items:
        if "=" not in item:
            raise ConfigError(f"Invalid --user-level {item!r}; expected USER=LEVEL")
        user_id, raw_level = item.split("=", 1)
        try:
            levels[user_id] = AuthLevel(raw_level)
        except ValueError as e:
            allowed = ", ".join(level.value for level in AuthLevel)
            raise ConfigError(
                f"Invalid auth level {raw_level!r}; expected one of: {allowed}",
            ) from e
    return levels


def _handle_ask_result(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
) -> dict:
    if not envelope.args:
        return {"ok": False, "status": "invalid_payload", "message": "Usage: /zf ask <message>"}

    message = " ".join(envelope.args)
    log = event_log_from_project(state_dir, config=config)
    writer = EventWriter(log)
    event = writer.append(ZfEvent(
        type="user.message",
        actor=f"feishu:{envelope.user_id or 'unknown'}",
        payload={
            "source": "feishu",
            "target": "feishu-operator-agent",
            "message": message,
            "chat_id": envelope.chat_id,
            "user_id": envelope.user_id,
            "message_id": envelope.message_id,
            "idempotency_key": envelope.idempotency_key,
        },
    ))
    response = f"Accepted /zf ask as user.message ({event.id}): {message}"
    return {"ok": True, "status": "accepted", "message": response, "event_id": event.id}


def _handle_controlled_action_result(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
) -> dict:
    payload = _controlled_action_payload(envelope)
    if payload is None:
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": _controlled_action_usage(envelope.command),
        }

    action = {
        "create": "create-task",
        "update": "update-task",
        "request-fanout": "request-fanout",
    }[envelope.command]
    actor = f"feishu:{envelope.user_id or 'unknown'}"
    log = event_log_from_project(state_dir, config=config)
    writer = EventWriter(log)
    requested = writer.emit(
        "feishu.command.enveloped",
        actor=actor,
        task_id=str(payload.get("task_id") or "") or None,
        payload={
            "command": envelope.command,
            "action": action,
            "chat_id": envelope.chat_id,
            "user_id": envelope.user_id,
            "message_id": envelope.message_id,
            "idempotency_key": envelope.idempotency_key,
            "request": payload,
        },
    )
    writer.emit(
        "runtime.action.accepted",
        actor=actor,
        task_id=str(payload.get("task_id") or "") or None,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "action": action,
            "requested_action": f"/zf {envelope.command}",
            "idempotency_key": envelope.idempotency_key,
        },
    )
    response = ControlledActionService(
        state_dir,
        writer,
        config=config,
        actor=actor,
        source="feishu",
        surface="feishu",
    ).execute(
        action=action,
        requested_action=f"/zf {envelope.command}",
        payload=payload,
        requested=requested,
    )
    message = _format_action_response(response)
    return {**response, "message": message}


def _handle_attention_ack_result(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
) -> dict:
    """Owner-visible alert card acknowledge button → runtime.attention.acknowledged.

    Reaches here only after feishu-B's signature + identity gate (operator
    level, see gateway ACTION_LEVELS). Emits the SAME event contract as the Web
    operator surface's attention-ack controlled action (control_actions_ops),
    so the inbox/attention consumers see one acknowledgement shape regardless
    of surface. Button payload is ``attention-ack:<attention_id>``.
    """
    if not envelope.args or not envelope.args[0].strip():
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": "Usage: /zf attention-ack <attention_id>",
        }
    attention_id = envelope.args[0].strip()
    actor = f"feishu:{envelope.user_id or 'unknown'}"
    # Resolve the human-readable operator name from the identity map so the
    # ack-flip card can show 「已确认 — min 15:18」 instead of a raw open_id.
    operator = ""
    identity = _feishu_identity(config)
    if identity is not None and getattr(identity, "enabled", False):
        entry = (getattr(identity, "users", {}) or {}).get(envelope.user_id)
        operator = str(getattr(entry, "operator", "") or "")
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    acked = writer.emit(
        "runtime.attention.acknowledged",
        actor=actor,
        payload={
            "attention_id": attention_id,
            "operator": operator,
            "source": "feishu_card",
            "surface": "feishu",
        },
    )
    return {
        "ok": True,
        "status": "acknowledged",
        "action": "attention-ack",
        "attention_id": attention_id,
        "event_id": acked.id,
        "message": f"Acknowledged {attention_id}",
    }


def _handle_plan_approval_result(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
) -> dict:
    """feishu-A P0.3: Plan Ready card button → plan.approved/plan.rejected.

    Reaches here only after feishu-B's signature + identity gate. The mutation
    runs through ControlledActionService (actor=operator, surface=feishu); the
    kernel wake then unlocks fanout (approved) or feeds synth replan (rejected).
    Button payload is ``plan-approve:<plan_id>`` / ``plan-reject:<plan_id>:<reason...>``.
    """
    if not envelope.args:
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": f"Usage: /zf {envelope.command} <plan_id> [reason]",
        }
    plan_id = envelope.args[0]
    payload: dict[str, Any] = {"plan_id": plan_id}
    if envelope.command == "plan-reject":
        payload["reason"] = ":".join(envelope.args[1:]).strip()
    action = "plan-approve" if envelope.command == "plan-approve" else "plan-reject"
    actor = f"feishu:{envelope.user_id or 'unknown'}"
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    requested = writer.emit(
        "feishu.command.enveloped",
        actor=actor,
        payload={
            "command": envelope.command,
            "action": action,
            "chat_id": envelope.chat_id,
            "user_id": envelope.user_id,
            "message_id": envelope.message_id,
            "idempotency_key": envelope.idempotency_key,
            "request": payload,
        },
    )
    response = ControlledActionService(
        state_dir,
        writer,
        config=config,
        actor=actor,
        source="feishu",
        surface="feishu",
    ).execute(
        action=action,
        requested_action=f"/zf {envelope.command}",
        payload=payload,
        requested=requested,
    )
    message = _format_action_response(response)
    return {**response, "message": message}


def _handle_agent_cancel_result(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
) -> dict:
    """feishu-C: Interrupt button → agent.session.run.cancelled (headless).

    Reaches here only after feishu-B's gate. Emits the cancellation event as the
    cross-process contract — the headless reactor that owns the provider stream
    stops it. NO tmux/pid kill here (tmux is worker-scope; channel replies are
    headless). Button payload is ``agent-cancel:<request_id>``.
    """
    if not envelope.args:
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": "Usage: /zf agent-cancel <request_id>",
        }
    request_id = envelope.args[0]
    actor = f"feishu:{envelope.user_id or 'unknown'}"
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    requested = writer.emit(
        "feishu.command.enveloped",
        actor=actor,
        payload={
            "command": envelope.command,
            "action": "agent-cancel",
            "chat_id": envelope.chat_id,
            "user_id": envelope.user_id,
            "message_id": envelope.message_id,
            "idempotency_key": envelope.idempotency_key,
            "request": {"request_id": request_id},
        },
    )
    cancelled = writer.emit(
        "agent.session.run.cancelled",
        actor=actor,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "request_id": request_id,
            "status": "cancelled",
            "reason": "operator interrupted from feishu",
            "source": "feishu",
            "surface": "feishu",
        },
    )
    return {
        "ok": True,
        "status": "cancelled",
        "request_id": request_id,
        "event_id": cancelled.id,
        "message": f"Interrupted channel reply {request_id}",
    }


def _handle_channel_question_result(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
) -> dict:
    """Channel question card button → channel.question.resolved (doc 122 §8).

    The clicking Feishu user is the owner surface; the projection's owner-only
    `answered` gate passes because a feishu user id is not a channel agent
    member. The discussion state machine consumes the resolution on its next
    tick — this handler records, never mutates discussion state directly.
    """
    from zf.integrations.feishu.channel_question_card import handle_question_decision

    question_id = envelope.args[0] if envelope.args else ""
    if not question_id:
        return {"ok": False, "reason": "missing_question_id"}
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    return handle_question_decision(
        command=envelope.command,
        question_id=question_id,
        state_dir=state_dir,
        writer=writer,
        user_id=envelope.user_id,
    )


def _handle_kanban_proposal_result(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
    project_root: Path,
) -> dict:
    """Signed proposal card callback -> the shared ControlledActionService."""
    proposal_event_id = envelope.args[0] if envelope.args else ""
    if not proposal_event_id:
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": "missing proposal_event_id",
        }
    event_log = event_log_from_project(state_dir, config=config)
    proposal_event = next(
        (
            event
            for event in event_log.read_all()
            if event.id == proposal_event_id
            and event.type in PROPOSAL_EVENT_TYPES
        ),
        None,
    )
    if proposal_event is None:
        return {
            "ok": False,
            "status": "proposal_not_found",
            "message": f"proposal {proposal_event_id} not found",
        }
    proposal_payload = (
        proposal_event.payload
        if isinstance(proposal_event.payload, dict)
        else {}
    )
    proposal = (
        proposal_payload.get("proposal")
        if isinstance(proposal_payload.get("proposal"), dict)
        else {}
    )
    action = str(proposal.get("action") or "")
    payload = (
        dict(proposal.get("payload"))
        if isinstance(proposal.get("payload"), dict)
        else {}
    )
    if envelope.command == "kanban-proposal-dismiss":
        action = "kanban-proposal-dismiss"
        payload = {
            "proposal_event_id": proposal_event_id,
            "reason": "dismissed from Feishu proposal card",
        }
    else:
        payload["proposal_event_id"] = proposal_event_id
        payload.setdefault("source", "feishu-kanban-proposal")
    actor = f"feishu:{envelope.user_id or 'unknown'}"
    writer = EventWriter(event_log)
    requested = writer.emit(
        "feishu.command.enveloped",
        actor=actor,
        correlation_id=proposal_event.correlation_id,
        payload={
            "command": envelope.command,
            "action": action,
            "chat_id": envelope.chat_id,
            "user_id": envelope.user_id,
            "message_id": envelope.message_id,
            "idempotency_key": envelope.idempotency_key,
            "request": payload,
        },
    )
    response = ControlledActionService(
        state_dir,
        writer,
        config=config,
        project_root=project_root,
        actor=actor,
        source="feishu",
        surface="feishu",
    ).execute(
        action=action,
        requested_action=envelope.command,
        payload=payload,
        requested=requested,
    )
    return {**response, "message": _format_action_response(response)}


def _handle_kanban_plan_result(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
    project_root: Path,
) -> dict:
    """Signed Plan option -> durable answer + same channel/provider continuation."""
    from zf.integrations.feishu.agent_conversation import (
        continue_kanban_plan_response,
    )

    target = envelope.args[0] if envelope.args else ""
    request_event_id, option_id = parse_plan_answer_target(target)
    if not request_event_id or not option_id:
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": "missing Plan request or option",
        }
    event_log = event_log_from_project(state_dir, config=config)
    request = _kanban_plan_request(event_log, request_event_id)
    answers = None
    answer = ""
    if option_id == PLAN_FORM_OPTION_ID:
        answers, form_error = form_answers_from_values(
            request,
            envelope.form_values,
        )
        if form_error:
            return {
                "ok": False,
                "status": form_error,
                "message": f"Plan answer rejected: {form_error}",
            }
    elif option_id == "other":
        answer = "Customize this plan"
    result = continue_kanban_plan_response(
        state_dir=state_dir,
        config=config,
        writer=EventWriter(event_log),
        request_event_id=request_event_id,
        option_id=option_id,
        answer=answer,
        answers=answers,
        user_id=envelope.user_id,
        chat_id=envelope.chat_id,
        message_id=envelope.message_id,
        project_root=project_root,
    )
    return {
        **result,
        "message": (
            f"Plan answer continued in thread {result.get('thread_id')}"
            if result.get("ok")
            else f"Plan answer rejected: {result.get('status')}"
        ),
    }


def _preflight_kanban_plan_form(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
) -> dict | None:
    """Reject incomplete Feishu form input without consuming its signed nonce."""
    if envelope.command != "kanban-plan-answer":
        return None
    target = envelope.args[0] if envelope.args else ""
    request_event_id, option_id = parse_plan_answer_target(target)
    if option_id != PLAN_FORM_OPTION_ID:
        return None
    if not request_event_id:
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": "missing Plan request or option",
        }
    event_log = event_log_from_project(state_dir, config=config)
    request = _kanban_plan_request(event_log, request_event_id)
    if not request:
        return {
            "ok": False,
            "status": "plan_request_not_found",
            "message": "Plan answer rejected: plan_request_not_found",
        }
    _answers, form_error = form_answers_from_values(
        request,
        envelope.form_values,
    )
    if form_error:
        return {
            "ok": False,
            "status": form_error,
            "message": f"Plan answer rejected: {form_error}",
        }
    return None


def _kanban_plan_request(event_log, request_event_id: str) -> dict:
    request_event = next(
        (
            event
            for event in event_log.read_all()
            if event.id == request_event_id
            and event.type == "kanban.agent.plan.requested"
        ),
        None,
    )
    payload = (
        request_event.payload
        if request_event is not None and isinstance(request_event.payload, dict)
        else {}
    )
    request = payload.get("plan_request")
    if not isinstance(request, dict):
        request = payload.get("request")
    return request if isinstance(request, dict) else {}


def _handle_human_decision_result(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
) -> dict:
    """Run Manager decision button → human.escalation.acknowledged.

    Run Manager owns the actual recovery action. The Feishu callback only
    records an operator/approver decision event so the next tick can consume it
    through the same kernel path as Web/CLI acknowledgements.
    """
    if not envelope.args:
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": f"Usage: /zf {envelope.command} <decision_token>",
        }
    decision_token = envelope.args[0]
    if envelope.command == "human-decision-explain":
        actor = f"feishu:{envelope.user_id or 'unknown'}"
        writer = EventWriter(event_log_from_project(state_dir, config=config))
        requested = writer.emit(
            "feishu.command.enveloped",
            actor=actor,
            payload={
                "command": envelope.command,
                "action": "human-decision-explain",
                "chat_id": envelope.chat_id,
                "user_id": envelope.user_id,
                "message_id": envelope.message_id,
                "idempotency_key": envelope.idempotency_key,
                "request": {"decision_token": decision_token},
            },
        )
        event = writer.emit(
            "run.manager.explanation.requested",
            actor=actor,
            causation_id=requested.id,
            correlation_id=requested.correlation_id,
            payload={
                "schema_version": "run-manager.explanation-requested.v1",
                "decision_token": decision_token,
                "source": "feishu",
                "surface": "feishu",
                "message_id": envelope.message_id,
                "user_id": envelope.user_id,
                "chat_id": envelope.chat_id,
                "question": "explain this Run Manager escalation",
            },
        )
        return {
            "ok": True,
            "status": "explanation_requested",
            "decision_token": decision_token,
            "event_id": event.id,
            "message": f"Requested Run Manager explanation for {decision_token}",
        }

    decision_by_command = {
        "human-decision-approve": "approve_controlled_action",
        "human-decision-diagnose": "request_autoresearch",
        "human-decision-halt": "safe_halt",
        "human-decision-reject": "safe_halt",
    }
    decision = decision_by_command.get(envelope.command)
    if decision is None:
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": f"Unsupported human decision {envelope.command}",
        }

    actor = f"feishu:{envelope.user_id or 'unknown'}"
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    requested = writer.emit(
        "feishu.command.enveloped",
        actor=actor,
        payload={
            "command": envelope.command,
            "action": "human-decision",
            "decision": decision,
            "chat_id": envelope.chat_id,
            "user_id": envelope.user_id,
            "message_id": envelope.message_id,
            "idempotency_key": envelope.idempotency_key,
            "request": {"decision_token": decision_token},
        },
    )
    acknowledged = writer.emit(
        "human.escalation.acknowledged",
        actor=actor,
        causation_id=requested.id,
        correlation_id=requested.correlation_id,
        payload={
            "schema_version": "human-escalation-acknowledged.v1",
            "decision_token": decision_token,
            "decision": decision,
            "source": "feishu",
            "surface": "feishu",
            "message_id": envelope.message_id,
            "user_id": envelope.user_id,
            "chat_id": envelope.chat_id,
        },
    )
    return {
        "ok": True,
        "status": "acknowledged",
        "decision": decision,
        "decision_token": decision_token,
        "event_id": acknowledged.id,
        "message": f"Recorded human decision {decision} for {decision_token}",
    }


def _handle_attention_action_result(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
) -> dict:
    parsed = _attention_action_payload(envelope.args)
    if parsed is None:
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": "Usage: /zf attention <ack|resolve|snooze|feedback|escalate> <attention_id|fingerprint> [key=value ...]",
        }
    action, payload = parsed
    payload.setdefault("source", "feishu")
    payload.setdefault("source_event_id", envelope.message_id)
    actor = f"feishu:{envelope.user_id or 'unknown'}"
    log = event_log_from_project(state_dir, config=config)
    writer = EventWriter(log)
    requested = writer.emit(
        "feishu.command.enveloped",
        actor=actor,
        task_id=str(payload.get("task_id") or "") or None,
        payload={
            "command": envelope.command,
            "action": action,
            "chat_id": envelope.chat_id,
            "user_id": envelope.user_id,
            "message_id": envelope.message_id,
            "idempotency_key": envelope.idempotency_key,
            "request": payload,
        },
    )
    response = ControlledActionService(
        state_dir,
        writer,
        config=config,
        actor=actor,
        source="feishu",
        surface="feishu",
    ).execute(
        action=action,
        requested_action=f"/zf attention {action.removeprefix('attention-')}",
        payload=payload,
        requested=requested,
    )
    message = _format_action_response(response)
    return {**response, "message": message}


def _attention_action_payload(args: list[str]) -> tuple[str, dict] | None:
    if not args:
        return None
    verb = args[0].strip().lower().replace("_", "-")
    aliases = {
        "ack": "attention-ack",
        "acknowledge": "attention-ack",
        "resolve": "attention-resolve",
        "resolved": "attention-resolve",
        "snooze": "attention-snooze",
        "feedback": "attention-feedback",
        "escalate": "attention-escalate",
    }
    action = aliases.get(verb)
    if action is None:
        return None
    rest = list(args[1:])
    target = ""
    if rest and "=" not in rest[0]:
        target = rest.pop(0).strip()
    payload = _parse_key_value_args(rest)
    if target:
        if target.startswith("attn-"):
            payload.setdefault("attention_id", target)
        else:
            payload.setdefault("fingerprint", target)
    if "id" in payload and "attention_id" not in payload:
        payload["attention_id"] = payload.pop("id")
    payload.setdefault("reason", f"feishu {verb}")
    return action, payload


def _controlled_action_payload(envelope: FeishuCommandEnvelope) -> dict | None:
    if envelope.command == "create":
        title = " ".join(envelope.args).strip()
        return {"title": title} if title else None
    if envelope.command == "update":
        if not envelope.args:
            return None
        payload = {"task_id": envelope.args[0]}
        payload.update(_parse_key_value_args(envelope.args[1:]))
        if "reason" in payload and "blocked_reason" not in payload:
            payload["blocked_reason"] = payload.pop("reason")
        return payload
    if envelope.command == "request-fanout":
        payload = _parse_key_value_args(envelope.args)
        rest = [arg for arg in envelope.args if "=" not in arg]
        if "stage_id" not in payload and rest:
            payload["stage_id"] = rest[0]
            if len(rest) > 1:
                payload["reason"] = " ".join(rest[1:])
        return payload if payload.get("stage_id") else None
    return None


def _parse_key_value_args(args: list[str]) -> dict:
    payload: dict[str, object] = {}
    loose: list[str] = []
    for item in args:
        if "=" not in item:
            loose.append(item)
            continue
        key, value = item.split("=", 1)
        key = key.strip().replace("-", "_")
        value = value.strip()
        if key in {"skills", "skills_required", "blocked_by"}:
            payload[key] = [part.strip() for part in value.split(",") if part.strip()]
        elif key == "priority":
            try:
                payload[key] = int(value)
            except ValueError:
                payload[key] = value
        else:
            payload[key] = value
    if loose:
        payload.setdefault("reason", " ".join(loose))
    return payload


def _controlled_action_usage(command: str) -> str:
    if command == "create":
        return "Usage: /zf create <task title>"
    if command == "update":
        return "Usage: /zf update <TASK-ID> status=blocked reason=..."
    if command == "request-fanout":
        return "Usage: /zf request-fanout <stage_id> [task_id=TASK-ID] [reason=...]"
    return "Usage: /zf <command>"


def _format_action_response(response: dict) -> str:
    status = str(response.get("status") or "unknown")
    action = str(response.get("action") or "")
    reason = str(response.get("reason") or "")
    task_id = str(response.get("task_id") or "")
    fanout_id = str(response.get("fanout_id") or "")
    parts = [f"{action}: {status}"]
    if task_id:
        parts.append(f"task_id={task_id}")
    if fanout_id:
        parts.append(f"fanout_id={fanout_id}")
    if reason:
        parts.append(reason)
    return " | ".join(parts)


def _handle_approval_result(
    envelope: FeishuCommandEnvelope,
    state_dir: Path,
    *,
    config: object | None,
) -> dict:
    if not envelope.args:
        return {
            "ok": False,
            "status": "invalid_payload",
            "message": f"Usage: /zf {envelope.command} <APPROVAL-ID>",
        }
    approval_id = envelope.args[0]
    status = "approved" if envelope.command == "approve" else "denied"
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    store = ApprovalStore(state_dir / "integrations" / "feishu" / "approvals.json")
    ok, message, record = store.transition(
        approval_id=approval_id,
        status=status,
        actor=f"feishu:{envelope.user_id or 'unknown'}",
        writer=writer,
    )
    return {
        "ok": ok,
        "status": record.status if record is not None else "not_found",
        "message": message,
        "approval_id": approval_id,
    }


def _parse_channels(items: list[str], *, default_to: str = "") -> dict[str, str]:
    channels, _receive_id_types = _parse_channel_targets(
        items,
        default_to=default_to,
        default_receive_id_type="chat_id",
    )
    return channels


def _parse_channel_targets(
    items: list[str],
    *,
    default_to: str = "",
    default_receive_id_type: str = "chat_id",
) -> tuple[dict[str, str], dict[str, str]]:
    channels: dict[str, str] = {}
    receive_id_types: dict[str, str] = {}
    if default_to:
        for role in ("progress", "alert", "approval", "owner"):
            channels[role] = default_to
            receive_id_types[role] = default_receive_id_type
    for item in items:
        if "=" not in item:
            raise ConfigError(f"Invalid --channel {item!r}; expected ROLE=RECEIVE_ID")
        role, receive_id = item.split("=", 1)
        role = role.strip()
        receive_id = receive_id.strip()
        receive_id_type = ""
        if ":" in receive_id:
            maybe_type, maybe_receive_id = receive_id.split(":", 1)
            if maybe_type in {"chat_id", "open_id", "user_id", "union_id", "email"} and maybe_receive_id:
                receive_id_type = maybe_type
                receive_id = maybe_receive_id
        if role and receive_id:
            channels[role] = receive_id
            if receive_id_type:
                receive_id_types[role] = receive_id_type
    return channels, receive_id_types


def _recreate_kanban_bitable_target(
    args: argparse.Namespace,
    *,
    context: ProjectContext,
    client: BitableClient,
    project_name: str,
    field_map: dict[str, str],
) -> tuple[str, str, str]:
    base_name = (
        str(getattr(args, "base_name", "") or "").strip()
        or f"ZaoFu Kanban - {project_name}"
    )
    table_name = str(getattr(args, "table_name", "") or "").strip() or "Kanban"
    folder_token = (
        str(getattr(args, "folder_token", "") or "").strip()
        or os.environ.get("FEISHU_FOLDER_TOKEN", "").strip()
    )
    base = client.create_base(name=base_name, folder_token=folder_token)
    app_token = _app_token_from_result(base)
    table = client.create_table(app_token, name=table_name)
    table_id = _table_id_from_result(table)
    schema = client.ensure_fields(app_token, table_id, kanban_field_specs(field_map))
    views = client.ensure_views(app_token, table_id, kanban_view_specs())
    layouts = client.ensure_view_layouts(
        app_token,
        table_id,
        kanban_view_layout_specs(field_map),
    )

    env_values = {
        "FEISHU_BITABLE_APP_TOKEN": app_token,
        "FEISHU_BITABLE_TABLE_ID": table_id,
    }
    if base.get("url"):
        env_values["FEISHU_BITABLE_URL"] = f"{base['url']}?table={table_id}"
    update_env_file(context.project_root / ".env", env_values, overwrite=True)

    message = (
        "Recreated Feishu Kanban target: "
        f"app_token={app_token} table_id={table_id} "
        f"fields_created={len(schema.get('created') or [])} "
        f"views_created={len(views.get('created') or [])} "
        f"views_configured={len(layouts.get('configured') or [])}"
    )
    return app_token, table_id, message


def _create_automation_insight_table_target(
    args: argparse.Namespace,
    *,
    context: ProjectContext,
    client: BitableClient,
    app_token: str,
    project_name: str,
    field_map: dict[str, str],
) -> tuple[str, str, str]:
    target_app = app_token.strip()
    table_name = str(getattr(args, "table_name", "") or "").strip() or "Automation Insights"
    base_name = (
        str(getattr(args, "base_name", "") or "").strip()
        or f"ZaoFu Automation Insights - {project_name}"
    )
    folder_token = (
        str(getattr(args, "folder_token", "") or "").strip()
        or os.environ.get("FEISHU_FOLDER_TOKEN", "").strip()
    )
    base_url = ""
    if not target_app:
        base = client.create_base(name=base_name, folder_token=folder_token)
        target_app = _app_token_from_result(base)
        base_url = str(base.get("url") or "")
    table = client.create_table(target_app, name=table_name)
    table_id = _table_id_from_result(table)
    schema = client.ensure_fields(
        target_app,
        table_id,
        automation_insight_field_specs(field_map),
    )
    views: dict[str, Any] = {"created": [], "existing": []}
    layouts: dict[str, Any] = {"configured": [], "skipped": []}
    layout_warning = ""
    if not getattr(args, "no_ensure_views", False):
        _, views, layouts, layout_warning = _ensure_automation_insight_table_shape(
            args,
            client=client,
            app_token=target_app,
            table_id=table_id,
            field_map=field_map,
            ensure_fields=False,
        )
    env_values = {
        "FEISHU_AUTOMATION_BITABLE_APP_TOKEN": target_app,
        "FEISHU_AUTOMATION_BITABLE_TABLE_ID": table_id,
    }
    if base_url:
        env_values["FEISHU_AUTOMATION_BITABLE_URL"] = f"{base_url}?table={table_id}"
    update_env_file(context.project_root / ".env", env_values, overwrite=True)
    message = (
        "Created Feishu Automation Insights table: "
        f"app_token={target_app} table_id={table_id} "
        f"fields_created={len(schema.get('created') or [])} "
        f"views_created={len(views.get('created') or [])} "
        f"views_configured={len(layouts.get('configured') or [])}"
    )
    if layout_warning:
        message += f" layout_warning={layout_warning}"
    return target_app, table_id, message


def _ensure_automation_insight_table_shape(
    args: argparse.Namespace,
    *,
    client: BitableClient,
    app_token: str,
    table_id: str,
    field_map: dict[str, str],
    ensure_fields: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    schema: dict[str, Any] = {"created": [], "existing": []}
    if ensure_fields:
        schema = client.ensure_fields(
            app_token,
            table_id,
            automation_insight_field_specs(field_map),
        )
    views = client.ensure_views(app_token, table_id, automation_insight_view_specs())
    layouts: dict[str, Any] = {"configured": [], "skipped": []}
    warning = ""
    if not getattr(args, "no_ensure_layouts", False):
        try:
            layouts = client.ensure_view_layouts(
                app_token,
                table_id,
                automation_insight_view_layout_specs(field_map),
            )
        except FeishuTransportError as exc:
            if not _is_view_layout_permission_error(exc):
                raise
            warning = "missing base:view:write_only; data sync continued"
    return schema, views, layouts, warning


def _is_view_layout_permission_error(exc: FeishuTransportError) -> bool:
    message = str(exc)
    return "base:view:write_only" in message or (
        "Access denied" in message and "/views/" in message
    )


def _is_deleted_feishu_resource_error(exc: FeishuTransportError) -> bool:
    message = str(exc).lower()
    return "note has been deleted" in message or "record has been deleted" in message


def _build_transport(kind: str) -> FeishuTransport:
    if kind == "mock":
        return MockFeishuTransport()
    if kind == "real":
        return FeishuHttpTransport()
    raise ConfigError(f"unknown Feishu transport: {kind}")


def _resolve_projection_backend(args: argparse.Namespace) -> str:
    backend = str(getattr(args, "backend", "") or "").strip()
    transport = str(getattr(args, "transport", "") or "").strip()
    if backend and transport:
        raise ConfigError("--backend and legacy --transport are mutually exclusive")
    if backend:
        return backend
    if transport:
        return {"mock": "mock", "real": "lark-cli"}[transport]
    return str(getattr(args, "projection_backend_default", "mock") or "mock")


def _build_document_client(
    kind: str,
) -> MockFeishuDocumentClient | LarkCliDocumentClient:
    if kind == "mock":
        return MockFeishuDocumentClient()
    if kind == "lark-cli":
        return LarkCliDocumentClient()
    raise ConfigError(f"unknown Feishu document backend: {kind}")


def _build_bitable_client(
    kind: str,
) -> MockFeishuBitableClient | LarkCliBitableClient:
    if kind == "mock":
        return MockFeishuBitableClient()
    if kind == "lark-cli":
        return LarkCliBitableClient()
    raise ConfigError(f"unknown Feishu Bitable backend: {kind}")


def _resolve_document_id(args: argparse.Namespace) -> str:
    candidates = [
        getattr(args, "document_id", ""),
        getattr(args, "document_url", ""),
        os.environ.get("FEISHU_AUTOMATION_DOCUMENT_ID", ""),
        os.environ.get("FEISHU_AUTOMATION_DOC_ID", ""),
        os.environ.get("FEISHU_AUTOMATION_DOCUMENT_URL", ""),
    ]
    for candidate in candidates:
        document_id = parse_feishu_document_id(str(candidate or ""))
        if document_id:
            return document_id
    return ""


def _resolve_bitable_target(args: argparse.Namespace) -> tuple[str, str]:
    explicit_app = str(getattr(args, "app_token", "") or "").strip()
    explicit_table = str(getattr(args, "table_id", "") or "").strip()
    env_app = os.environ.get("FEISHU_BITABLE_APP_TOKEN", "").strip()
    env_table = os.environ.get("FEISHU_BITABLE_TABLE_ID", "").strip()
    url_sources = [
        str(getattr(args, "bitable_url", "") or "").strip(),
        explicit_app if "://" in explicit_app else "",
        os.environ.get("FEISHU_BITABLE_URL", "").strip(),
        env_app if "://" in env_app else "",
    ]

    url_app = ""
    url_table = ""
    for source in url_sources:
        if not source:
            continue
        ref = parse_feishu_bitable_ref(source)
        if ref.app_token and not url_app:
            url_app = ref.app_token
        if ref.table_id and not url_table:
            url_table = ref.table_id

    app_token = (
        (explicit_app if "://" not in explicit_app else "")
        or url_app
        or (env_app if "://" not in env_app else "")
    )
    table_id = explicit_table or url_table or env_table
    return app_token, table_id


def _resolve_automation_bitable_target(args: argparse.Namespace) -> tuple[str, str]:
    explicit_app = str(getattr(args, "app_token", "") or "").strip()
    explicit_table = str(getattr(args, "table_id", "") or "").strip()
    env_app = os.environ.get("FEISHU_AUTOMATION_BITABLE_APP_TOKEN", "").strip()
    fallback_app = os.environ.get("FEISHU_BITABLE_APP_TOKEN", "").strip()
    env_table = (
        os.environ.get("FEISHU_AUTOMATION_BITABLE_TABLE_ID", "").strip()
        or os.environ.get("FEISHU_AUTOMATION_TABLE_ID", "").strip()
    )
    url_sources = [
        str(getattr(args, "bitable_url", "") or "").strip(),
        explicit_app if "://" in explicit_app else "",
        os.environ.get("FEISHU_AUTOMATION_BITABLE_URL", "").strip(),
        env_app if "://" in env_app else "",
    ]

    url_app = ""
    url_table = ""
    for source in url_sources:
        if not source:
            continue
        ref = parse_feishu_bitable_ref(source)
        if ref.app_token and not url_app:
            url_app = ref.app_token
        if ref.table_id and not url_table:
            url_table = ref.table_id

    app_token = (
        (explicit_app if "://" not in explicit_app else "")
        or url_app
        or (env_app if "://" not in env_app else "")
        or (fallback_app if "://" not in fallback_app else "")
    )
    table_id = explicit_table or url_table or env_table
    return app_token, table_id


def _initial_document_content(project_id: str, project_name: str) -> str:
    return (
        f"# ZaoFu Automation Reports - {project_name}\n\n"
        f"- Project ID: {project_id}\n"
        "- Source: zf feishu sync-automations\n\n"
        "Future daily, weekly, and project automation reports will be appended here.\n"
    )


def _document_id_from_result(document: dict[str, Any]) -> str:
    document_id = str(document.get("document_id") or document.get("token") or "").strip()
    if not document_id:
        raise ValueError("created Feishu document did not include document_id")
    return document_id


def _app_token_from_result(base: dict[str, Any]) -> str:
    app_token = str(
        base.get("app_token")
        or base.get("base_token")
        or base.get("token")
        or "",
    ).strip()
    if not app_token:
        raise ValueError("created Feishu Base did not include app_token")
    return app_token


def _table_id_from_result(table: dict[str, Any]) -> str:
    table_id = str(table.get("table_id") or table.get("id") or "").strip()
    if not table_id:
        raise ValueError("created Feishu table did not include table_id")
    return table_id


def _project_identity(context: ProjectContext, args: argparse.Namespace) -> tuple[str, str]:
    configured_name = ""
    if context.config is not None:
        configured_name = str(context.config.project.name or "")
    project_name = str(
        getattr(args, "project_name", "")
        or configured_name
        or context.project_root.name,
    )
    project_id = str(
        getattr(args, "project_id", "")
        or stable_project_id(name=project_name, root=context.project_root),
    )
    return project_id, project_name


def _parse_field_map(items: list[str]) -> dict[str, str]:
    field_map: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ConfigError(f"Invalid --field {item!r}; expected KEY=FEISHU_FIELD")
        key, value = item.split("=", 1)
        key = key.strip().replace("-", "_")
        value = value.strip()
        if not key or not value:
            raise ConfigError(f"Invalid --field {item!r}; key and field name are required")
        field_map[key] = value
    return field_map


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = str(value or "").split(":", 1)
    if len(parts) != 2:
        raise ConfigError("--daily-time must be HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as e:
        raise ConfigError("--daily-time must be HH:MM") from e
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError("--daily-time hour/minute out of range")
    return hour, minute


def _send_json(handler: BaseHTTPRequestHandler, status_code: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
