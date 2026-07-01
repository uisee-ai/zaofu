"""Read-only effective gate projection for a ZaoFu project."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from zf.runtime.hook_registry import project_hook_registry
from zf.runtime.provider_capabilities import project_provider_capabilities


_DEFAULT_OPERATOR_ACTIONS: tuple[str, ...] = (
    "chat-orchestrator",
    "create-task",
    "update-task",
    "archive-task",
    "link-evidence",
    "request-fanout",
    "start-collaboration",
    "start-operator-session",
    "dispatch-task",
    "request-verify",
    "request-review",
    "ship-candidate",
    "agent-session-cancel",
)


def project_gate_projection(
    state_dir: Path,
    *,
    config: Any | None = None,
    project_root: Path | None = None,
    operator_backends: list[dict[str, Any]] | None = None,
    allowed_actions: Iterable[str] | None = None,
    web_token_configured: bool | None = None,
    web_authorization_available: bool | None = None,
    web_mutation_mode: str | None = None,
    events: Iterable[Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the current effective gate view.

    This is a projection only. It does not change runtime state and does not
    grant capabilities; callers still go through their existing token-gated or
    kernel-gated paths.
    """

    now = now or datetime.now(timezone.utc)
    state_dir = Path(state_dir)
    project_root = Path(project_root or state_dir.parent)
    actions = sorted(set(allowed_actions or _DEFAULT_OPERATOR_ACTIONS))
    providers = project_provider_capabilities(
        config=config,
        operator_backends=operator_backends,
        now=now,
    )
    hooks = project_hook_registry(
        state_dir,
        config=config,
        project_root=project_root,
        events=events,
        now=now,
    )
    gates: list[dict[str, Any]] = []
    gates.extend(_workflow_gates(config))
    gates.extend(_role_gates(config))
    gates.extend(_provider_gates(providers))
    gates.extend(_hook_gates(hooks))
    gates.append(_web_mutation_gate(
        actions=actions,
        token_configured=web_token_configured,
        authorization_available=web_authorization_available,
        mutation_mode=web_mutation_mode,
    ))
    gates.append(_controlled_actions_gate(actions))

    warnings = [row for row in gates if row.get("status") in {"warning", "unwired"}]
    return {
        "schema_version": "gate-projection.v1",
        "generated_at": now.isoformat(),
        "project_root": str(project_root),
        "state_dir": str(state_dir),
        "summary": {
            "gates": len(gates),
            "roles": sum(1 for row in gates if row.get("surface") == "role"),
            "providers": sum(1 for row in gates if row.get("surface") == "provider"),
            "hooks": sum(1 for row in gates if row.get("surface") == "hook"),
            "mutation_gates": sum(
                1 for row in gates if row.get("surface") in {"web", "controlled_action"}
            ),
            "blocking": sum(1 for row in gates if row.get("blocking")),
            "warnings": len(warnings),
        },
        "gates": gates,
        "provider_capabilities": providers,
        "hook_registry": hooks,
    }


def _workflow_gates(config: Any | None) -> list[dict[str, Any]]:
    workflow = getattr(config, "workflow", None)
    dag = getattr(workflow, "dag", None)
    inline = getattr(workflow, "inline_overrides", None)
    return [{
        "id": "workflow.profile",
        "surface": "workflow",
        "source": "zf.yaml",
        "status": "enabled",
        "blocking": True,
        "harness_profile": getattr(workflow, "harness_profile", "baseline"),
        "dag_enabled": bool(getattr(dag, "enabled", False)),
        "inline_overrides_enabled": bool(getattr(inline, "enabled", False)),
        "event_actions": len(getattr(workflow, "event_actions", []) or []),
        "stages": len(getattr(workflow, "stages", []) or []),
    }]


def _role_gates(config: Any | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for role in getattr(config, "roles", []) or []:
        role_id = str(getattr(role, "instance_id", "") or getattr(role, "name", ""))
        backend = str(getattr(role, "backend", "") or "")
        backends = list(getattr(role, "backends", []) or [])
        permission_mode = str(getattr(role, "permission_mode", "") or "bypass")
        allowed_tools = list(getattr(role, "allowed_tools", []) or [])
        rows.append({
            "id": f"role.{role_id}",
            "surface": "role",
            "source": "zf.yaml",
            "status": permission_mode,
            "blocking": permission_mode == "allowlist",
            "role": role_id,
            "role_type": getattr(role, "name", ""),
            "role_kind": getattr(role, "role_kind", "auto"),
            "backend": backend,
            "backends": backends or [backend],
            "permission_mode": permission_mode,
            "allowed_tools": allowed_tools,
            "allowed_tool_count": len(allowed_tools),
            "transport": getattr(role, "transport", ""),
            "skills": list(getattr(role, "skills", []) or []),
            "plugins": list(getattr(role, "plugins", []) or []),
        })
    return rows


def _provider_gates(providers: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for provider in providers.get("providers", []) or []:
        if not isinstance(provider, dict):
            continue
        backend = str(provider.get("backend") or "")
        review_required = bool(provider.get("hook_review_required"))
        rows.append({
            "id": f"provider.{backend}",
            "surface": "provider",
            "source": provider.get("source", "provider_capabilities"),
            "status": provider.get("availability", "not_checked"),
            "blocking": review_required,
            "backend": backend,
            "family": provider.get("family", ""),
            "surface_kind": provider.get("surface", ""),
            "native_resume": bool(provider.get("native_resume")),
            "streaming": bool(provider.get("streaming")),
            "per_turn_hook": bool(provider.get("per_turn_hook")),
            "session_start_hook": bool(provider.get("session_start_hook")),
            "hook_review_required": review_required,
            "roles": list(provider.get("roles", []) or []),
        })
    return rows


def _hook_gates(hooks: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hook in hooks.get("hooks", []) or []:
        if not isinstance(hook, dict):
            continue
        status = str(hook.get("status") or "")
        rows.append({
            "id": f"hook.{hook.get('id', '')}",
            "surface": "hook",
            "source": hook.get("source", ""),
            "status": "unwired" if status == "experimental_unwired" else status,
            "blocking": bool(hook.get("blocking")),
            "provider": hook.get("provider", ""),
            "event_type": hook.get("event_type", ""),
            "last_event_id": hook.get("last_event_id", ""),
            "last_error": hook.get("last_error", ""),
            "reason": hook.get("reason", ""),
        })
    return rows


def _web_mutation_gate(
    *,
    actions: list[str],
    token_configured: bool | None,
    authorization_available: bool | None,
    mutation_mode: str | None,
) -> dict[str, Any]:
    auth_known = authorization_available is not None
    enabled = bool(authorization_available) if auth_known else False
    return {
        "id": "web.mutation",
        "surface": "web",
        "source": "web_action",
        "status": "enabled" if enabled else "disabled" if auth_known else "unknown",
        "blocking": True,
        "token_configured": bool(token_configured) if token_configured is not None else None,
        "authorization_available": authorization_available,
        "mutation_mode": mutation_mode or "unknown",
        "allowed_actions": actions,
        "allowed_action_count": len(actions),
    }


def _controlled_actions_gate(actions: list[str]) -> dict[str, Any]:
    return {
        "id": "kernel.controlled_actions",
        "surface": "controlled_action",
        "source": "ControlledActionService",
        "status": "enabled",
        "blocking": True,
        "allowed_actions": actions,
        "allowed_action_count": len(actions),
        "truth_write_path": "kernel_action_only",
    }
