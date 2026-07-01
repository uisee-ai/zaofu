"""Provider permission snapshot helpers.

The snapshot is an observability contract: it records the effective local
security posture used when a headless provider session starts or resumes.
It does not grant permissions by itself.
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def codex_security_config_for_profile(
    permission_profile: str,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    profile = normalize_permission_profile(permission_profile)
    values = env if env is not None else os.environ
    defaults = {
        "read_only": {"approvalPolicy": "never", "sandbox": "read-only"},
        "artifact_writer": {"approvalPolicy": "never", "sandbox": "workspace-write"},
        "project_writer": {"approvalPolicy": "never", "sandbox": "workspace-write"},
        "dangerous_full": {"approvalPolicy": "never", "sandbox": "danger-full-access"},
    }[profile]
    return {
        "approvalPolicy": values.get(
            "ZF_KANBAN_AGENT_CODEX_HEADLESS_APPROVAL_POLICY",
            defaults["approvalPolicy"],
        ),
        "sandbox": values.get(
            "ZF_KANBAN_AGENT_CODEX_HEADLESS_SANDBOX",
            defaults["sandbox"],
        ),
    }


def claude_permission_mode_for_profile(
    permission_profile: str,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    profile = normalize_permission_profile(permission_profile)
    values = env if env is not None else os.environ
    default = "default"
    if profile == "dangerous_full":
        default = "bypassPermissions"
    elif profile in {"artifact_writer", "project_writer"}:
        default = "acceptEdits"
    return values.get("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_PERMISSION_MODE", default)


def provider_security_for_backend(
    backend: str,
    permission_profile: str,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    normalized = str(backend or "")
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
    ]
    items: list[dict[str, Any]] = []
    for field in fields:
        before = previous.get(field)
        after = current.get(field)
        if before != after:
            severity = "blocking" if field in {"cwd", "workspace_roots", "backend"} else "warning"
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
    "build_provider_permission_snapshot",
    "claude_permission_mode_for_profile",
    "codex_security_config_for_profile",
    "emit_provider_permission_snapshot",
    "provider_permission_drift",
    "provider_security_for_backend",
    "snapshot_with_provider_session",
]
