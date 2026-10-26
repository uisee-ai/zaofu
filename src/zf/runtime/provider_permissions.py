"""Provider permission snapshot helpers.

The snapshot is an observability contract: it records the effective provider
execution posture used when a headless session starts or resumes. ZaoFu keeps
role write policies, artifact boundaries, and controlled actions as the
semantic enforcement layer; trusted local provider processes run without an
OS sandbox by default.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.events import EventWriter, ZfEvent
from zf.core.security.redaction import redact_obj
from zf.runtime.channel_contracts import (
    normalize_permission_profile,
    permission_profile_write_policy,
)

KANBAN_AGENT_PERMISSION_PROFILES = (
    "read_only",
    "operator",
    "workspace_writer",
    "isolated_writer",
    "dangerous_full",
)
KANBAN_AGENT_DEFAULT_PERMISSION_PROFILE = "workspace_writer"
DEFAULT_CODEX_HEADLESS_SANDBOX = "danger-full-access"
DEFAULT_CLAUDE_HEADLESS_PERMISSION_MODE = "bypassPermissions"


def normalize_kanban_permission_profile(value: object) -> str:
    """Use the operator write default only when no profile was specified."""
    return normalize_permission_profile(
        value if str(value or "").strip() else KANBAN_AGENT_DEFAULT_PERMISSION_PROFILE
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def codex_security_config_for_profile(
    permission_profile: str,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    # Validate the logical profile even though provider execution is globally
    # full access. The profile still governs ZaoFu's write-policy and action
    # gates; it is not an OS-sandbox selector.
    normalize_permission_profile(permission_profile)
    values = env if env is not None else os.environ
    return {
        "approvalPolicy": str(values.get(
            "ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY", "never",
        )).strip() or "never",
        "sandbox": str(values.get(
            "ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX",
            DEFAULT_CODEX_HEADLESS_SANDBOX,
        )).strip() or DEFAULT_CODEX_HEADLESS_SANDBOX,
    }


def claude_permission_mode_for_profile(
    permission_profile: str,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    # See codex_security_config_for_profile: this validates logical policy,
    # while the provider process uses the trusted-local full-access baseline.
    normalize_permission_profile(permission_profile)
    values = env if env is not None else os.environ
    return str(values.get(
        "ZF_KANBAN_AGENT_CLAUDE_HEADLESS_PERMISSION_MODE",
        DEFAULT_CLAUDE_HEADLESS_PERMISSION_MODE,
    )).strip() or DEFAULT_CLAUDE_HEADLESS_PERMISSION_MODE


def provider_security_for_backend(
    backend: str,
    permission_profile: str,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    normalized = str(backend or "")
    if normalized == "kimi-headless-canary":
        return {"permission_mode": "tool_free_canary", "native_delegation": "disabled",
                "native_resume": "not_admitted"}
    if normalized in {"codex", "codex-headless", "codex-app-server"}:
        return codex_security_config_for_profile(permission_profile, env=env)
    if normalized in {"claude", "claude-code", "claude-headless"}:
        return {
            "permission_mode": claude_permission_mode_for_profile(
                permission_profile,
                env=env,
            )
        }
    return {}


def build_provider_permission_snapshot(
    *,
    backend: str,
    permission_profile: str,
    cwd: Path | str,
    project_id: str = "",
    conversation_id: str = "",
    thread_id: str = "",
    run_id: str = "",
    provider_session_id: str = "",
    runtime_snapshot_ref: str = "",
    role: str = "",
    member_id: str = "",
    source: str = "headless-agent",
    workspace_roots: list[str] | None = None,
    skills: list[str] | None = None,
    hooks: list[str] | None = None,
    profile_id: str = "",
    profile_revision: int = 0,
    profile_digest: str = "",
    config_digest: str = "",
    skill_set_digest: str = "",
    permission_digest: str = "",
    profile_snapshot_ref: str = "",
    profile_snapshot_sha256: str = "",
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    profile = normalize_permission_profile(permission_profile)
    cwd_path = str(Path(cwd))
    roots = workspace_roots if workspace_roots is not None else [cwd_path]
    security = provider_security_for_backend(backend, profile, env=env)
    snapshot: dict[str, Any] = {
        "schema_version": "provider-permission-snapshot.v1",
        "created_at": _now(),
        "source": source,
        "backend": str(backend or ""),
        "provider_session_id": str(provider_session_id or ""),
        "runtime_snapshot_ref": str(runtime_snapshot_ref or ""),
        "project_id": str(project_id or ""),
        "conversation_id": str(conversation_id or ""),
        "thread_id": str(thread_id or ""),
        "run_id": str(run_id or ""),
        "role": str(role or ""),
        "member_id": str(member_id or ""),
        "cwd": cwd_path,
        "workspace_roots": [str(root) for root in roots],
        "permission_profile": profile,
        "profile_id": str(profile_id or ""),
        "profile_revision": int(profile_revision or 0),
        "profile_digest": str(profile_digest or ""),
        "config_digest": str(config_digest or ""),
        "skill_set_digest": str(skill_set_digest or ""),
        "permission_digest": str(permission_digest or ""),
        "profile_snapshot_ref": str(profile_snapshot_ref or ""),
        "profile_snapshot_sha256": str(profile_snapshot_sha256 or ""),
        "write_policy": permission_profile_write_policy(profile),
        "skills": list(skills or []),
        "hooks": list(hooks or []),
        "security": security,
    }
    if "approvalPolicy" in security:
        snapshot["approval_policy"] = security["approvalPolicy"]
    if "sandbox" in security:
        snapshot["sandbox_policy"] = security["sandbox"]
    if "permission_mode" in security:
        snapshot["permission_mode"] = security["permission_mode"]
    return snapshot


def snapshot_with_provider_session(
    snapshot: Mapping[str, Any],
    provider_session_id: str,
) -> dict[str, Any]:
    updated = dict(snapshot)
    updated["provider_session_id"] = str(provider_session_id or "")
    return updated


def provider_permission_drift(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not previous or not current:
        return {"status": "ok", "items": []}

    fields = [
        "project_id",
        "cwd",
        "workspace_roots",
        "backend",
        "permission_profile",
        "approval_policy",
        "sandbox_policy",
        "permission_mode",
        "profile_id",
        "profile_revision",
        "profile_digest",
        "config_digest",
        "skill_set_digest",
        "permission_digest",
        "profile_snapshot_ref",
        "profile_snapshot_sha256",
    ]
    items: list[dict[str, Any]] = []
    for field in fields:
        before = previous.get(field)
        after = current.get(field)
        if field.startswith("profile_") or field in {
            "config_digest",
            "skill_set_digest",
            "permission_digest",
        }:
            if not before and not after:
                continue
        if before != after:
            severity = (
                "blocking"
                if field
                in {
                    "cwd",
                    "workspace_roots",
                    "backend",
                    "profile_id",
                    "profile_revision",
                    "profile_digest",
                    "config_digest",
                    "skill_set_digest",
                    "permission_digest",
                    "profile_snapshot_ref",
                    "profile_snapshot_sha256",
                }
                else "warning"
            )
            items.append({
                "field": field,
                "previous": before,
                "current": after,
                "severity": severity,
            })
    status = "ok"
    if any(item["severity"] == "blocking" for item in items):
        status = "blocking"
    elif items:
        status = "warning"
    return {"status": status, "items": items}


def emit_provider_permission_snapshot(
    writer: EventWriter,
    *,
    task_id: str | None,
    causation_id: str | None,
    correlation_id: str | None,
    actor: str,
    snapshot: Mapping[str, Any],
    drift: Mapping[str, Any] | None = None,
) -> ZfEvent:
    payload = {
        "run_id": str(snapshot.get("run_id") or ""),
        "thread_id": str(snapshot.get("thread_id") or ""),
        "provider_session_id": str(snapshot.get("provider_session_id") or ""),
        "runtime_snapshot_ref": str(snapshot.get("runtime_snapshot_ref") or ""),
        "snapshot_ref": str(snapshot.get("runtime_snapshot_ref") or ""),
        "project_id": str(snapshot.get("project_id") or ""),
        "conversation_id": str(snapshot.get("conversation_id") or ""),
        "backend": str(snapshot.get("backend") or ""),
        "permission_profile": str(snapshot.get("permission_profile") or ""),
        "source": str(snapshot.get("source") or "headless-agent"),
        "snapshot": redact_obj(dict(snapshot)),
        "drift": redact_obj(dict(drift or {"status": "ok", "items": []})),
    }
    event = writer.emit(
        "provider.permission.snapshot.recorded",
        actor=actor,
        task_id=task_id,
        causation_id=causation_id,
        correlation_id=correlation_id,
        payload=payload,
    )
    drift_payload = dict(drift or {})
    if drift_payload.get("status") in {"warning", "blocking"}:
        writer.emit(
            "provider.permission.snapshot.drift",
            actor=actor,
            task_id=task_id,
            causation_id=event.id,
            correlation_id=correlation_id,
            payload=redact_obj({
                "run_id": payload["run_id"],
                "thread_id": payload["thread_id"],
                "provider_session_id": payload["provider_session_id"],
                "backend": payload["backend"],
                "status": drift_payload.get("status"),
                "items": drift_payload.get("items") or [],
                "source": payload["source"],
            }),
        )
    return event


__all__ = [
    "KANBAN_AGENT_DEFAULT_PERMISSION_PROFILE",
    "KANBAN_AGENT_PERMISSION_PROFILES",
    "DEFAULT_CLAUDE_HEADLESS_PERMISSION_MODE",
    "DEFAULT_CODEX_HEADLESS_SANDBOX",
    "build_provider_permission_snapshot",
    "claude_permission_mode_for_profile",
    "codex_security_config_for_profile",
    "emit_provider_permission_snapshot",
    "normalize_kanban_permission_profile",
    "provider_permission_drift",
    "provider_security_for_backend",
    "snapshot_with_provider_session",
]
