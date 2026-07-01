"""zf workspace — inspect local workspace metadata."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from zf.core.config.schema import OpenClawRemoteBindingConfig
from zf.core.workspace.providers import WorkspaceProviderRegistry


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace",
        help="Inspect or update local workspace metadata",
    )
    parser.add_argument("--workspace", default="default", help="Workspace id")
    sub = parser.add_subparsers(dest="workspace_cmd")

    providers = sub.add_parser("providers", help="Manage workspace provider bindings")
    providers_sub = providers.add_subparsers(dest="providers_cmd")

    openclaw = providers_sub.add_parser("openclaw", help="Manage OpenClaw bindings")
    openclaw_sub = openclaw.add_subparsers(dest="openclaw_cmd")

    openclaw_list = openclaw_sub.add_parser("list", help="List OpenClaw bindings")
    openclaw_list.add_argument("--json", action="store_true", help="Emit JSON")
    openclaw_list.set_defaults(func=_run_openclaw_list)

    openclaw_set = openclaw_sub.add_parser("set", help="Create or update an OpenClaw binding")
    openclaw_set.add_argument("binding_id", help="Binding id, e.g. remote")
    openclaw_set.add_argument("--base-url", required=True, help="OpenClaw gateway base URL")
    openclaw_set.add_argument(
        "--token-env",
        default="OPENCLAW_GATEWAY_TOKEN",
        help="Environment variable that holds the gateway token",
    )
    openclaw_set.add_argument(
        "--workspace-policy",
        default="isolated",
        help="Remote workspace policy",
    )
    openclaw_set.add_argument(
        "--tool-profile",
        default="safe",
        help="Remote tool profile",
    )
    openclaw_set.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Gateway reply timeout in seconds",
    )
    openclaw_set.add_argument(
        "--provision-agent",
        action="store_true",
        help="Ask the gateway to upsert the remote agent descriptor",
    )
    openclaw_set.add_argument(
        "--default",
        action="store_true",
        help="Make this the default OpenClaw binding",
    )
    openclaw_set.add_argument("--json", action="store_true", help="Emit JSON")
    openclaw_set.set_defaults(func=_run_openclaw_set)

    parser.set_defaults(func=_run_help)


def _run_help(args: argparse.Namespace) -> int:
    del args
    print("Usage: zf workspace providers openclaw <list|set>", file=sys.stderr)
    return 2


def _registry(args: argparse.Namespace) -> WorkspaceProviderRegistry:
    return WorkspaceProviderRegistry(workspace=str(getattr(args, "workspace", "default") or "default"))


def _run_openclaw_list(args: argparse.Namespace) -> int:
    try:
        registry = _registry(args)
        openclaw = registry.openclaw()
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    report = {
        "workspace": str(getattr(args, "workspace", "default") or "default"),
        "path": str(registry.path),
        "default_binding": openclaw.default_binding,
        "bindings": {
            binding_id: _binding_dict(binding)
            for binding_id, binding in sorted(openclaw.bindings.items())
        },
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"Workspace provider registry: {report['path']}")
    if not openclaw.bindings:
        print("OpenClaw bindings: none")
        return 0
    print(f"OpenClaw default: {openclaw.default_binding or '-'}")
    for binding_id, binding in sorted(openclaw.bindings.items()):
        default_mark = " (default)" if binding_id == openclaw.default_binding else ""
        print(
            f"- {binding_id}{default_mark}: {binding.base_url} "
            f"token_env={binding.token_env or '-'} "
            f"profile={binding.tool_profile}"
        )
    return 0


def _run_openclaw_set(args: argparse.Namespace) -> int:
    binding = OpenClawRemoteBindingConfig(
        id=str(args.binding_id),
        base_url=str(args.base_url),
        token_env=str(args.token_env or ""),
        default_workspace_policy=str(args.workspace_policy or "isolated"),
        tool_profile=str(args.tool_profile or "safe"),
        timeout_seconds=float(args.timeout_seconds),
        provision_agent=bool(args.provision_agent),
    )
    try:
        registry = _registry(args)
        openclaw = registry.upsert_openclaw_binding(
            binding,
            default=bool(args.default),
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    report = {
        "ok": True,
        "workspace": str(getattr(args, "workspace", "default") or "default"),
        "path": str(registry.path),
        "default_binding": openclaw.default_binding,
        "binding": _binding_dict(openclaw.bindings[binding.id]),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"Saved OpenClaw binding {binding.id!r} to {registry.path}")
    print(f"Default binding: {openclaw.default_binding or '-'}")
    print("Token value is not stored; set the configured token_env before use.")
    return 0


def _binding_dict(binding: OpenClawRemoteBindingConfig) -> dict[str, object]:
    data = asdict(binding)
    return {
        key: value
        for key, value in data.items()
        if key != "id"
    }
