"""Mechanical preflight for explicitly concurrent Project Runs."""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, Iterable

from zf.core.events.model import ZfEvent
from zf.runtime.run_contract import stable_json_sha256
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def concurrent_isolation_blocker(
    runtime: Any,
    event: ZfEvent,
    *,
    active_run_ids: list[str],
    events: list[ZfEvent],
    run_id_for: Callable[[ZfEvent], str],
) -> str:
    """Fail closed unless a second Run has isolated mechanical identity."""

    payload = _payload(event)
    required = (
        "workflow_run_id",
        "request_id",
        "effective_config_digest",
        "run_contract_digest",
    )
    missing = [
        key
        for key in required
        if not str(payload.get(key) or "").strip()
    ]
    if missing:
        return "concurrent isolation missing " + ", ".join(missing)
    workdirs = getattr(getattr(runtime, "config", None), "runtime", None)
    workdir_policy = getattr(workdirs, "workdirs", None)
    if not (
        bool(getattr(workdir_policy, "enabled", False))
        and str(getattr(workdir_policy, "mode", "") or "") == "worktree"
    ):
        return "concurrent isolation requires runtime.workdirs worktree mode"

    task_id = str(event.task_id or payload.get("task_id") or "").strip()
    if not task_id:
        return "concurrent isolation requires a scoped task_id"
    active_task_ids: set[str] = set()
    active_digests_by_run: dict[str, set[str]] = {
        run_id: set() for run_id in active_run_ids
    }
    active_contracts_by_run: dict[str, set[str]] = {
        run_id: set() for run_id in active_run_ids
    }
    active_scopes_by_run: dict[str, list[str]] = {
        run_id: [] for run_id in active_run_ids
    }
    snapshot_identities: dict[str, str] = {}
    for candidate in events:
        body = _payload(candidate)
        candidate_run = run_id_for(candidate)
        if candidate_run not in active_run_ids:
            continue
        candidate_task = str(
            candidate.task_id or body.get("task_id") or ""
        ).strip()
        if candidate_task:
            active_task_ids.add(candidate_task)
        config_identity, config_error = _effective_config_identity(runtime, body)
        if config_error:
            return config_error
        if config_identity:
            active_digests_by_run[candidate_run].add(config_identity)
            ref = body.get("effective_config_ref")
            if isinstance(ref, Mapping):
                snapshot_sha = str(ref.get("sha256") or "").strip()
                if snapshot_sha:
                    snapshot_identities[snapshot_sha] = config_identity
        contract_digest = str(body.get("run_contract_digest") or "").strip()
        if contract_digest:
            active_contracts_by_run[candidate_run].add(contract_digest)
        target_root_error = _target_root_error(runtime, body)
        if target_root_error:
            return target_root_error
        if _invalid_scope_values(body):
            return (
                "concurrent isolation has invalid work scope for active Run "
                f"{candidate_run}"
            )
        active_scopes_by_run[candidate_run].extend(_scope_paths(body))
    if task_id in active_task_ids:
        return f"concurrent task_id collision: {task_id}"
    # Admission facts retain only the snapshot digest, not its full descriptor.
    # Resolve those through verified bindings; unknown digests still diverge.
    active_digests_by_run = {
        run_id: {snapshot_identities.get(digest, digest) for digest in digests}
        for run_id, digests in active_digests_by_run.items()
    }
    missing_active_config = next(
        (
            run_id
            for run_id, digests in active_digests_by_run.items()
            if not digests
        ),
        "",
    )
    if missing_active_config:
        return (
            "concurrent isolation requires pinned config for active Run "
            f"{missing_active_config}"
        )
    divergent_active_config = next(
        (
            run_id
            for run_id, digests in active_digests_by_run.items()
            if len(digests) != 1
        ),
        "",
    )
    if divergent_active_config:
        return (
            "concurrent isolation found config identity divergence for "
            f"active Run {divergent_active_config}"
        )
    missing_active_contract = next(
        (
            run_id
            for run_id, digests in active_contracts_by_run.items()
            if not digests
        ),
        "",
    )
    if missing_active_contract:
        return (
            "concurrent isolation requires run contract for active Run "
            f"{missing_active_contract}"
        )
    divergent_active_contract = next(
        (
            run_id
            for run_id, digests in active_contracts_by_run.items()
            if len(digests) != 1
        ),
        "",
    )
    if divergent_active_contract:
        return (
            "concurrent isolation found run contract identity divergence for "
            f"active Run {divergent_active_contract}"
        )
    active_digests = {
        digest
        for digests in active_digests_by_run.values()
        for digest in digests
    }
    config_digest, config_error = _effective_config_identity(runtime, payload)
    if config_error:
        return config_error
    config_digest = snapshot_identities.get(config_digest, config_digest)
    if active_digests and config_digest not in active_digests:
        return "concurrent effective config digest mismatch"
    missing_active_scope = next(
        (
            run_id
            for run_id, scopes in active_scopes_by_run.items()
            if not scopes
        ),
        "",
    )
    if missing_active_scope:
        return (
            "concurrent isolation requires explicit work scope for active Run "
            f"{missing_active_scope}"
        )
    if _invalid_scope_values(payload):
        return "concurrent isolation has invalid work scope"
    target_root_error = _target_root_error(runtime, payload)
    if target_root_error:
        return target_root_error
    new_scopes = _scope_paths(payload)
    if not new_scopes:
        return "concurrent isolation requires explicit work scope"
    active_scopes = [
        scope
        for scopes in active_scopes_by_run.values()
        for scope in scopes
    ]
    if _paths_overlap(active_scopes, new_scopes):
        return "concurrent workdir scope overlaps an active Run"
    return ""


def _scope_values(payload: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("scope", "paths"):
        raw = payload.get(key)
        if isinstance(raw, str):
            values.extend(part.strip() for part in raw.split(","))
        elif isinstance(raw, list):
            values.extend(str(item).strip() for item in raw)
    return [value for value in values if value]


def _target_root_error(runtime: Any, payload: Mapping[str, Any]) -> str:
    """Validate project identity separately from the relative write scope."""
    refs = payload.get("source_refs")
    roots = [str(value).strip() for value in (
        refs.get("target_root") if isinstance(refs, Mapping) else "",
        payload.get("target_root"),
    ) if value]
    if not roots:
        return ""
    project_root = getattr(runtime, "project_root", None)
    if project_root is None:
        return "concurrent target_root requires project root identity"
    try:
        root = Path(project_root).resolve(strict=False)
        for raw in roots:
            if "\x00" in raw or any(ch in raw for ch in "*?[") or (len(raw) > 1 and raw[1] == ":"):
                return "concurrent target_root is outside project root"
            target = Path(raw).expanduser()
            if not target.is_absolute():
                target = root / target
            target = target.resolve(strict=False)
            target.relative_to(root)
            for scope in _scope_paths(dict(payload)):
                if not (root / scope).resolve(strict=False).is_relative_to(target):
                    return "concurrent work scope is outside target_root"
    except (OSError, RuntimeError, ValueError):
        return "concurrent target_root is outside project root"
    return ""


def _effective_config_identity(runtime: Any, payload: Mapping[str, Any]) -> tuple[str, str]:
    """Compare semantic config content while retaining snapshot binding checks."""
    raw_digest = str(payload.get("effective_config_digest") or "").strip()
    ref = payload.get("effective_config_ref")
    if "effective_config_ref" not in payload:
        return raw_digest, ""
    if not isinstance(ref, Mapping) or not str(ref.get("ref") or "").strip():
        return "", "concurrent effective config binding invalid"
    expected = str(ref.get("sha256") or "").strip()
    if not expected or (raw_digest and raw_digest != expected):
        return "", "concurrent effective config binding invalid"
    try:
        hydrated = hydrate_sidecar_ref(runtime.state_dir, dict(ref))
    except (OSError, ValueError):
        return "", "concurrent effective config binding invalid"
    if not hydrated.ok or not isinstance(hydrated.payload, Mapping):
        return "", "concurrent effective config binding invalid"
    body = hydrated.payload
    if str(body.get("schema_version") or "") != "effective-config-snapshot.v1":
        return "", "concurrent effective config binding invalid"
    config = body.get("config")
    if not isinstance(config, Mapping):
        return "", "concurrent effective config binding invalid"
    return stable_json_sha256(config), ""


def _invalid_scope_values(payload: dict[str, Any]) -> list[str]:
    return [
        value
        for value in _scope_values(payload)
        if not _normalize_scope_path(value)
    ]


def _scope_paths(payload: dict[str, Any]) -> list[str]:
    normalized = [
        _normalize_scope_path(value)
        for value in _scope_values(payload)
    ]
    return list(dict.fromkeys(value for value in normalized if value))


def _normalize_scope_path(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or "\x00" in raw or any(ch in raw for ch in "*?["):
        return ""
    if (
        posixpath.isabs(raw)
        or raw.startswith("~")
        or (len(raw) >= 2 and raw[1] == ":")
    ):
        return ""
    normalized = posixpath.normpath(raw)
    if normalized in {"", ".", "/", ".."} or normalized.startswith("../"):
        return ""
    return normalized.rstrip("/")


def _paths_overlap(left: Iterable[str], right: Iterable[str]) -> bool:
    normalized_left = [
        str(path).strip("/") for path in left if str(path).strip("/")
    ]
    normalized_right = [
        str(path).strip("/") for path in right if str(path).strip("/")
    ]
    return any(
        first == second
        or first.startswith(second + "/")
        or second.startswith(first + "/")
        for first in normalized_left
        for second in normalized_right
    )


def _payload(event: ZfEvent) -> dict[str, Any]:
    return event.payload if isinstance(event.payload, dict) else {}


__all__ = ["concurrent_isolation_blocker"]
