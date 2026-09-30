"""Explicit CLI control surface for project Feishu collaboration-group bindings."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zf.core.config.loader import ConfigError
from zf.core.config.project_context import load_project_env, resolve_project_context
from zf.core.workspace.feishu_binding_index import WorkspaceFeishuBindingIndex
from zf.core.workspace.registry import WorkspaceRegistry
from zf.integrations.feishu.project_group_binding import (
    ProjectFeishuGroupBindingStore,
    attach_project_feishu_group,
    ensure_project_feishu_group_binding,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "group",
        help="Inspect or explicitly provision a project Feishu collaboration group",
    )
    actions = parser.add_subparsers(dest="feishu_group_command")

    status = actions.add_parser("status", help="Show project group binding state")
    status.add_argument("--state-dir", default=None)
    status.set_defaults(func=run_status)

    provision = actions.add_parser(
        "provision",
        help="Create/verify the configured Feishu project group (external write)",
    )
    provision.add_argument("--state-dir", default=None)
    provision.add_argument(
        "--workspace",
        default=None,
        help=(
            "Workspace for a new binding; existing project bindings keep their "
            "recorded workspace when this is omitted"
        ),
    )
    provision.add_argument(
        "--confirm",
        action="store_true",
        help="Acknowledge that this creates or updates a real Feishu group",
    )
    provision.set_defaults(func=run_provision)

    attach = actions.add_parser(
        "attach",
        help="Verify and attach an existing Feishu chat to the configured binding",
    )
    attach.add_argument("--state-dir", default=None)
    attach.add_argument("--binding-id", default="project-collaboration")
    attach.add_argument("--chat-id", required=True)
    attach.add_argument(
        "--confirm",
        action="store_true",
        help="Acknowledge verification/add-member calls against this real chat",
    )
    attach.set_defaults(func=run_attach)

    parser.set_defaults(func=run_root)


def run_root(_args: argparse.Namespace) -> int:
    print("Use `zf feishu group status|provision|attach`.", file=sys.stderr)
    return 2


def run_status(args: argparse.Namespace) -> int:
    context = _context(args)
    if context is None:
        return 1
    bindings = [binding.to_dict() for binding in ProjectFeishuGroupBindingStore(
        context.state_dir
    ).list()]
    print(json.dumps({"bindings": bindings}, ensure_ascii=False, indent=2))
    return 0


def run_provision(args: argparse.Namespace) -> int:
    if not bool(getattr(args, "confirm", False)):
        print("Error: `zf feishu group provision` requires --confirm.", file=sys.stderr)
        return 2
    context = _context(args)
    if context is None:
        return 1
    integrations = getattr(context.config, "integrations", None)
    group = getattr(integrations, "feishu_project_group", None)
    binding_id = str(
        getattr(group, "binding_id", "project-collaboration")
        or "project-collaboration"
    )
    existing = ProjectFeishuGroupBindingStore(context.state_dir).get(binding_id)
    requested_workspace = str(getattr(args, "workspace", "") or "").strip()
    workspace_id = requested_workspace or (
        existing.workspace_id if existing is not None else "default"
    )
    registry = WorkspaceRegistry(workspace=workspace_id)
    project = registry.upsert_context(context)
    binding = ensure_project_feishu_group_binding(
        context,
        workspace_id=registry.workspace,
        project_id=project.project_id,
        provision=True,
    )
    if binding is None:
        print(
            "Error: integrations.feishu_project_group.enabled=true is required.",
            file=sys.stderr,
        )
        return 2
    _rebuild_index(registry)
    print(json.dumps(binding.to_dict(), ensure_ascii=False, indent=2))
    return 0 if binding.status == "active" else 1


def run_attach(args: argparse.Namespace) -> int:
    if not bool(getattr(args, "confirm", False)):
        print("Error: `zf feishu group attach` requires --confirm.", file=sys.stderr)
        return 2
    context = _context(args)
    if context is None:
        return 1
    binding = attach_project_feishu_group(
        context,
        binding_id=str(args.binding_id),
        chat_id=str(args.chat_id),
    )
    registry = WorkspaceRegistry(workspace=binding.workspace_id)
    _rebuild_index(registry)
    print(json.dumps(binding.to_dict(), ensure_ascii=False, indent=2))
    return 0 if binding.status == "active" else 1


def _context(args: argparse.Namespace):
    try:
        # Resolve once to locate the owning project, load its dotenv, then
        # resolve again so feishu.yaml's ${ENV:-sentinel} references compile
        # with the real credentials.  Other Feishu CLI entrypoints already do
        # this before loading config; group commands must preserve the same
        # contract before provisioning or attaching a real chat.
        initial = resolve_project_context(
            cwd=Path.cwd(),
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=True,
        )
        load_project_env(initial.project_root)
        return resolve_project_context(
            cwd=Path.cwd(),
            explicit_state_dir=getattr(args, "state_dir", None),
            load_config_with_explicit=True,
        )
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None


def _rebuild_index(registry: WorkspaceRegistry) -> None:
    try:
        WorkspaceFeishuBindingIndex(registry).rebuild()
    except Exception as exc:  # noqa: BLE001 - binding's own result remains authoritative.
        print(f"Warning: unable to refresh workspace Feishu route index: {exc}", file=sys.stderr)
