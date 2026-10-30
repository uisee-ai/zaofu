"""Shared provider-session usage receipts and shutdown-tail reconciliation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.cost.tracker import CostTracker
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.runtime.backend_session_reader import UsageReport, get_reader_for_backend
from zf.runtime.housekeeping import apply_agent_usage_event
from zf.runtime.provider_usage import canonical_usage_tokens


@dataclass(frozen=True)
class ProviderUsageTailResult:
    appended_event_ids: tuple[str, ...] = ()
    replayed_event_ids: tuple[str, ...] = ()
    failed_roles: tuple[str, ...] = ()


def build_disk_usage_event(
    *,
    role: RoleConfig,
    usage: UsageReport,
    config: ZfConfig | None,
    usage_identity: dict[str, str] | None = None,
    task_id: str = "",
    capture_phase: str = "lifecycle_poll",
) -> ZfEvent:
    """Build the canonical ``agent.usage`` receipt for a disk snapshot."""

    usage_semantics = str(
        getattr(usage, "usage_semantics", "incremental") or "incremental"
    )
    report_series_id = str(getattr(usage, "usage_series_id", "") or "")
    usage_series_id = (
        ":".join((
            "disk_reader",
            role.instance_id,
            role.backend,
            report_series_id or usage.model or "default",
        ))
        if usage_semantics == "cumulative"
        else ""
    )
    raw = dict(usage.raw or {})
    receipt = canonical_usage_tokens(
        {
            **raw,
            "input_tokens": raw.get(
                "input_tokens", usage.effective_input_tokens
            ),
            "output_tokens": raw.get("output_tokens", usage.output_tokens),
        },
        backend=role.backend,
        input_semantics=str(getattr(usage, "input_semantics", "") or ""),
    )
    normalised = {
        "input_tokens": receipt["fresh_input_tokens"],
        "combined_input_tokens": receipt["combined_input_tokens"],
        "output_tokens": receipt["output_tokens"],
        "cached_input_tokens": receipt["cache_read_input_tokens"],
        "cache_read_input_tokens": receipt["cache_read_input_tokens"],
        "cache_creation_input_tokens": receipt[
            "cache_creation_input_tokens"
        ],
        "reasoning_output_tokens": receipt["reasoning_output_tokens"],
    }
    identity = dict(usage_identity or {})
    resolved_task_id = str(task_id or identity.get("task_id") or "")
    payload: dict[str, Any] = {
        **identity,
        "task_id": resolved_task_id,
        "usage": normalised,
        "source": "disk_reader",
        "capture_phase": capture_phase,
        "usage_semantics": usage_semantics,
        "context_usage_ratio": round(usage.ratio, 4),
        "ratio": round(usage.ratio, 4),
        "model_context_window": usage.model_context_window,
        "model": usage.model,
        "provider": str(getattr(usage, "provider", "") or ""),
        "accounting_mode": _accounting_mode_for_usage(
            config,
            role.backend,
            str(getattr(usage, "accounting_mode", "unknown") or "unknown"),
        ),
        "input_semantics": "fresh_plus_cache",
        "provider_input_semantics": str(
            getattr(usage, "input_semantics", "") or ""
        ),
        "usage_receipt_schema": receipt["schema_version"],
        "backend": role.backend,
        "usage_timestamp": usage.timestamp,
    }
    if usage_series_id:
        payload["usage_series_id"] = usage_series_id
    payload["usage_sample_id"] = _disk_usage_sample_id(
        actor=role.instance_id,
        backend=role.backend,
        model=usage.model,
        model_context_window=usage.model_context_window,
        usage_timestamp=usage.timestamp,
        usage=normalised,
        usage_series_id=usage_series_id,
    )
    return ZfEvent(
        type="agent.usage",
        actor=role.instance_id,
        task_id=resolved_task_id or None,
        payload=payload,
        correlation_id=str(identity.get("workflow_run_id") or "") or None,
    )


def reconcile_provider_usage_tail(
    *,
    state_dir: Path,
    project_root: Path,
    config: ZfConfig | None,
    event_log: Any,
    excluded_roles: set[str] | None = None,
    included_roles: set[str] | None = None,
    capture_phase: str = "shutdown_tail",
) -> ProviderUsageTailResult:
    """Capture provider snapshots after transport shutdown or session retirement.

    The provider rollout is read only after its process has exited. Existing
    sample receipts are replayed through the idempotent CostTracker so an
    append-before-cost crash is repaired without duplicating the event.
    """

    if config is None:
        return ProviderUsageTailResult()
    excluded = set(excluded_roles or ())
    writer = EventWriter(event_log)
    tracker = CostTracker(Path(state_dir) / "cost.jsonl")
    role_backends = {
        key: role.backend
        for role in config.roles
        for key in {role.name, role.instance_id}
    }
    existing_by_sample = {
        str((event.payload or {}).get("usage_sample_id") or ""): event
        for event in event_log.read_all()
        if event.type == "agent.usage"
        and str((event.payload or {}).get("usage_sample_id") or "")
    }
    registry = RoleSessionRegistry(
        Path(state_dir) / "role_sessions.yaml",
        project_root=str(project_root),
    )
    appended: list[str] = []
    replayed: list[str] = []
    failed: list[str] = []
    seen_instances: set[str] = set()
    for role in config.roles:
        if included_roles is not None and role.instance_id not in included_roles:
            continue
        if role.instance_id in excluded or role.name in excluded:
            continue
        if role.instance_id in seen_instances:
            continue
        seen_instances.add(role.instance_id)
        try:
            usage = _read_latest_role_usage(
                state_dir=Path(state_dir),
                project_root=Path(project_root),
                config=config,
                registry=registry,
                role=role,
            )
        except Exception:
            failed.append(role.instance_id)
            continue
        if usage is None:
            continue
        event = build_disk_usage_event(
            role=role,
            usage=usage,
            config=config,
            capture_phase=capture_phase,
        )
        sample_id = str(event.payload.get("usage_sample_id") or "")
        existing = existing_by_sample.get(sample_id)
        if existing is None:
            try:
                event = writer.append(event)
            except Exception:
                failed.append(role.instance_id)
                continue
            appended.append(event.id)
            existing_by_sample[sample_id] = event
        else:
            event = existing
            replayed.append(event.id)
        try:
            apply_agent_usage_event(
                tracker,
                event,
                role_backends=role_backends,
            )
        except Exception:
            failed.append(role.instance_id)
    return ProviderUsageTailResult(
        appended_event_ids=tuple(appended),
        replayed_event_ids=tuple(replayed),
        failed_roles=tuple(dict.fromkeys(failed)),
    )


def _read_latest_role_usage(
    *,
    state_dir: Path,
    project_root: Path,
    config: ZfConfig,
    registry: RoleSessionRegistry,
    role: RoleConfig,
) -> UsageReport | None:
    reader = get_reader_for_backend(role.backend)
    if reader is None:
        return None
    cached_uuid = registry.get(role.instance_id)
    cached_path = registry.get_path(role.instance_id)
    session_id = str(cached_uuid) if cached_uuid else ""
    if not session_id and cached_path is None:
        return None
    usage_cwd = project_root
    workdirs = config.runtime.workdirs
    if (
        role.backend == "claude-code"
        and workdirs.enabled
        and workdirs.mode == "worktree"
    ):
        worktree = state_dir / "workdirs" / role.instance_id / "project"
        if worktree.exists():
            usage_cwd = worktree
    path = reader.session_path(
        str(usage_cwd),
        session_id,
        cached_path=cached_path,
    )
    if path is None:
        return None
    return reader.read_latest_usage(
        path,
        fallback_window=role.context_window_tokens,
    )


def _disk_usage_sample_id(
    *,
    actor: str,
    backend: str,
    model: str,
    model_context_window: int,
    usage_timestamp: object,
    usage: dict[str, Any],
    usage_series_id: str = "",
) -> str:
    payload = {
        "actor": actor,
        "backend": backend,
        "model": model,
        "model_context_window": model_context_window,
        "source": "disk_reader",
        "usage": usage,
        "usage_series_id": usage_series_id,
        "usage_timestamp": usage_timestamp,
    }
    data = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:24]


def _accounting_mode_for_usage(
    config: ZfConfig | None,
    backend: str,
    observed: str,
) -> str:
    mode = str(observed or "unknown").strip().lower()
    if mode != "unknown":
        return mode
    configured = getattr(
        getattr(config, "cost", None),
        "backend_accounting_modes",
        {},
    )
    return str(configured.get(backend) or "unknown")


__all__ = [
    "ProviderUsageTailResult",
    "build_disk_usage_event",
    "reconcile_provider_usage_tail",
]
