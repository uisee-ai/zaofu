"""Recovery contract sufficiency and artifact rehydration helpers."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zf.core.config.schema import RoleConfig, ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


CONTRACT_REF_FIELDS = (
    "spec_ref",
    "plan_ref",
    "tdd_ref",
    "critic_gate_ref",
    "critic_event_id",
    "evidence_contract",
)


@dataclass(frozen=True)
class RecoverySufficiencyResult:
    status: str
    missing_fields: list[str] = field(default_factory=list)
    missing_refs: list[str] = field(default_factory=list)
    hash_failures: list[dict[str, Any]] = field(default_factory=list)
    layers: list[dict[str, str]] = field(default_factory=list)
    reason: str = ""
    packet: dict[str, Any] = field(default_factory=dict)

    @property
    def sufficient(self) -> bool:
        return self.status == "sufficient"

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "missing_fields": list(self.missing_fields),
            "missing_refs": list(self.missing_refs),
            "hash_failures": [dict(item) for item in self.hash_failures],
            "layers": [dict(item) for item in self.layers],
            "reason": self.reason,
        }


def read_task_ref_entry(state_dir: Path, task_id: str) -> dict[str, Any]:
    path = state_dir / "refs" / "task-index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    entry = data.get(task_id)
    return entry if isinstance(entry, dict) else {}


def build_artifact_recovery_refs(
    state_dir: Path,
    task: Task | None,
    *,
    project_root: Path,
    required_contract_refs: list[str] | None = None,
) -> dict[str, Any]:
    task_id = task.id if task is not None else ""
    entry = read_task_ref_entry(state_dir, task_id) if task_id else {}
    artifact_refs = [
        dict(item) for item in entry.get("artifact_refs", [])
        if isinstance(item, dict)
    ]
    accepted_artifact_refs = [ref for ref in artifact_refs if _artifact_ref_is_accepted(ref)]
    stale_artifact_refs = [ref for ref in artifact_refs if not _artifact_ref_is_accepted(ref)]
    contract_refs = entry.get("contract_refs")
    if not isinstance(contract_refs, dict):
        contract_refs = {}
    task_contract_refs = _task_contract_refs(task)
    merged_contract_refs = dict(task_contract_refs)
    merged_contract_refs.update({
        key: value for key, value in contract_refs.items()
        if str(value or "").strip()
    })
    required = list(dict.fromkeys(required_contract_refs or []))
    missing_required = [
        field for field in required
        if not _ref_present(merged_contract_refs.get(field))
    ]
    hash_status = [
        verify_artifact_ref(ref, project_root=project_root, state_dir=state_dir)
        for ref in artifact_refs
    ]
    accepted_hash_status = [
        item for item in hash_status
        if _hash_status_is_for_accepted_ref(item, accepted_artifact_refs)
    ]
    return {
        "schema_version": "artifact-recovery.v1",
        "task_index_path": str(state_dir / "refs" / "task-index.json"),
        "manifest_event_id": str(entry.get("manifest_event_id") or ""),
        "manifest_role": str(entry.get("manifest_role") or ""),
        "contract_refs": merged_contract_refs,
        "artifact_refs": artifact_refs,
        "accepted_artifact_refs": accepted_artifact_refs,
        "stale_artifact_refs": stale_artifact_refs,
        "hash_status": hash_status,
        "accepted_hash_status": accepted_hash_status,
        "required_contract_refs": required,
        "missing_required_refs": missing_required,
    }


def verify_artifact_ref(
    ref: dict[str, Any],
    *,
    project_root: Path,
    state_dir: Path,
) -> dict[str, Any]:
    raw_path = str(ref.get("path") or "").strip()
    expected = str(ref.get("sha256") or "").strip().lower()
    base = {
        "artifact_id": str(ref.get("artifact_id") or ""),
        "kind": str(ref.get("kind") or ""),
        "path": raw_path,
        "workdir_path": str(ref.get("workdir_path") or ""),
        "version": ref.get("version", ""),
        "ledger_status": str(ref.get("status") or ""),
        "expected_sha256": expected,
    }
    if not raw_path or not expected:
        return {**base, "status": "unknown", "reason": "path_or_sha_missing"}
    path = _resolve_artifact_path(
        raw_path,
        project_root=project_root,
        state_dir=state_dir,
        workdir_path=str(ref.get("workdir_path") or ""),
    )
    if path is None or not path.exists() or not path.is_file():
        return {**base, "status": "missing", "reason": "artifact_file_missing"}
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        return {
            **base,
            "status": "mismatch",
            "actual_sha256": actual,
            "resolved_path": str(path),
            "reason": "sha256_mismatch",
        }
    return {
        **base,
        "status": "ok",
        "actual_sha256": actual,
        "resolved_path": str(path),
    }


def evaluate_recovery_packet(packet: dict[str, Any]) -> RecoverySufficiencyResult:
    missing_fields: list[str] = []
    if not str(packet.get("task_id") or "").strip():
        missing_fields.append("task_id")
    if str(packet.get("current_state") or "").strip() in {"", "missing"}:
        missing_fields.append("current_state")
    if not (
        str(packet.get("next_required_event") or "").strip()
        or str(packet.get("next_required_action") or "").strip()
        or packet.get("missing_evidence")
    ):
        missing_fields.append("next_required_action")

    missing_refs = _packet_missing_refs(packet)
    hash_failures = _packet_hash_failures(packet)
    status = "sufficient"
    if missing_fields or missing_refs:
        status = "insufficient"
    if any(item.get("status") == "mismatch" for item in hash_failures):
        status = "unrecoverable"
    reason = "recovery contract sufficient"
    if status != "sufficient":
        parts = []
        if missing_fields:
            parts.append("missing fields: " + ", ".join(missing_fields))
        if missing_refs:
            parts.append("missing refs: " + ", ".join(missing_refs))
        if hash_failures:
            parts.append("hash failures: " + str(len(hash_failures)))
        reason = "; ".join(parts)
    return RecoverySufficiencyResult(
        status=status,
        missing_fields=missing_fields,
        missing_refs=missing_refs,
        hash_failures=hash_failures,
        reason=reason,
        packet=dict(packet),
    )


def rehydrate_recovery_context(
    state_dir: Path,
    packet: dict[str, Any],
    *,
    project_root: Path,
) -> RecoverySufficiencyResult:
    task_id = str(packet.get("task_id") or "").strip()
    enriched = dict(packet)
    layers: list[dict[str, str]] = [
        {"layer": "L0", "status": "loaded", "source": "resume_packet"},
    ]
    task: Task | None = None
    try:
        task = TaskStore(state_dir / "kanban.json").get(task_id) if task_id else None
    except Exception:
        task = None
    if task is not None:
        layers.append({"layer": "L0", "status": "loaded", "source": "kanban_task"})
        enriched.setdefault("current_state", task.status)
    artifact_entry = read_task_ref_entry(state_dir, task_id) if task_id else {}
    if artifact_entry:
        layers.append({"layer": "L1", "status": "loaded", "source": "task-index.json"})
        required = _required_refs_from_packet(enriched)
        enriched["artifact_recovery"] = build_artifact_recovery_refs(
            state_dir,
            task,
            project_root=project_root,
            required_contract_refs=required,
        )
        enriched["accepted_artifact_refs"] = enriched["artifact_recovery"].get(
            "accepted_artifact_refs", []
        )
        enriched["artifact_hash_status"] = enriched["artifact_recovery"].get(
            "accepted_hash_status", enriched["artifact_recovery"].get("hash_status", [])
        )
        enriched["missing_artifact_refs"] = enriched["artifact_recovery"].get(
            "missing_required_refs", []
        )
    event_count = _count_task_events(state_dir, task_id)
    if event_count:
        layers.append({"layer": "L2", "status": "loaded", "source": "events.jsonl"})
    if (state_dir / "progress.md").exists():
        layers.append({"layer": "L3", "status": "loaded", "source": "progress.md"})
    if _git_head(project_root):
        layers.append({"layer": "L3", "status": "loaded", "source": "git"})
    result = evaluate_recovery_packet(enriched)
    return RecoverySufficiencyResult(
        status=result.status,
        missing_fields=result.missing_fields,
        missing_refs=result.missing_refs,
        hash_failures=result.hash_failures,
        layers=layers,
        reason=result.reason,
        packet=enriched,
    )


def run_recovery_sufficiency_gate(
    *,
    state_dir: Path,
    project_root: Path,
    task: Task,
    role: RoleConfig,
    config: ZfConfig,
    event_writer: EventWriter,
) -> RecoverySufficiencyResult:
    """Evaluate recovery sufficiency and emit the deterministic audit trail."""
    dispatch_id = getattr(task, "active_dispatch_id", "") or "recovery"
    try:
        from zf.runtime.long_horizon import build_resume_packet

        packet = build_resume_packet(
            state_dir,
            task.id,
            dispatch_id=dispatch_id,
            config=config,
            project_root=project_root,
        )
    except Exception as exc:
        result = RecoverySufficiencyResult(
            status="unrecoverable",
            missing_fields=["resume_packet"],
            reason=f"resume packet build failed: {exc}",
        )
        _append_gate_event(
            event_writer,
            "worker.recovery.blocked",
            task=task,
            role=role,
            dispatch_id=dispatch_id,
            result=result,
        )
        return result

    result = evaluate_recovery_packet(packet)
    if result.sufficient:
        return result

    insufficient = _append_gate_event(
        event_writer,
        "worker.recovery.insufficient",
        task=task,
        role=role,
        dispatch_id=dispatch_id,
        result=result,
    )
    requested = _append_gate_event(
        event_writer,
        "recovery.contract.rehydrate.requested",
        task=task,
        role=role,
        dispatch_id=dispatch_id,
        payload={
            "reason": result.reason,
            "layers": ["L0", "L1", "L2", "L3", "L4"],
        },
        causation_id=insufficient.id if insufficient is not None else None,
        correlation_id=insufficient.correlation_id if insufficient is not None else None,
    )
    rehydrated = rehydrate_recovery_context(
        state_dir,
        packet,
        project_root=project_root,
    )
    _append_gate_event(
        event_writer,
        "recovery.contract.rehydrated",
        task=task,
        role=role,
        dispatch_id=dispatch_id,
        result=rehydrated,
        causation_id=requested.id if requested is not None else None,
        correlation_id=requested.correlation_id if requested is not None else None,
    )
    if not rehydrated.sufficient:
        _append_gate_event(
            event_writer,
            "worker.recovery.blocked",
            task=task,
            role=role,
            dispatch_id=dispatch_id,
            result=rehydrated,
        )
    return rehydrated


def _append_gate_event(
    event_writer: EventWriter,
    event_type: str,
    *,
    task: Task,
    role: RoleConfig,
    dispatch_id: str,
    result: RecoverySufficiencyResult | None = None,
    payload: dict[str, Any] | None = None,
    causation_id: str | None = None,
    correlation_id: str | None = None,
) -> ZfEvent | None:
    actor = "zf-cli" if event_type.startswith("recovery.") else role.instance_id
    body: dict[str, Any] = {}
    if result is not None:
        body.update(result.to_payload())
    if payload:
        body.update(payload)
    body.update({
        "role": role.name,
        "instance_id": role.instance_id,
        "dispatch_id": dispatch_id,
    })
    try:
        return event_writer.append(ZfEvent(
            type=event_type,
            actor=actor,
            task_id=task.id,
            payload=body,
            causation_id=causation_id,
            correlation_id=correlation_id,
        ))
    except Exception:
        return None


def _packet_missing_refs(packet: dict[str, Any]) -> list[str]:
    missing = packet.get("missing_artifact_refs")
    if isinstance(missing, list):
        return [str(item) for item in missing if str(item).strip()]
    artifact_recovery = packet.get("artifact_recovery")
    if isinstance(artifact_recovery, dict):
        value = artifact_recovery.get("missing_required_refs")
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
    return []


def _packet_hash_failures(packet: dict[str, Any]) -> list[dict[str, Any]]:
    statuses = packet.get("artifact_hash_status")
    if not isinstance(statuses, list):
        artifact_recovery = packet.get("artifact_recovery")
        if isinstance(artifact_recovery, dict):
            statuses = artifact_recovery.get("hash_status")
    if not isinstance(statuses, list):
        return []
    return [
        dict(item) for item in statuses
        if isinstance(item, dict)
        and item.get("status") in {"missing", "mismatch"}
        and str(item.get("ledger_status") or "accepted") in {"", "accepted"}
    ]


def _required_refs_from_packet(packet: dict[str, Any]) -> list[str]:
    requirements = packet.get("sufficiency_requirements")
    if isinstance(requirements, dict):
        refs = requirements.get("required_contract_refs")
        if isinstance(refs, list):
            return [str(item) for item in refs if str(item).strip()]
    artifact_recovery = packet.get("artifact_recovery")
    if isinstance(artifact_recovery, dict):
        refs = artifact_recovery.get("required_contract_refs")
        if isinstance(refs, list):
            return [str(item) for item in refs if str(item).strip()]
    return []


def _task_contract_refs(task: Task | None) -> dict[str, Any]:
    if task is None or task.contract is None:
        return {}
    refs: dict[str, Any] = {}
    for field_name in CONTRACT_REF_FIELDS:
        refs[field_name] = getattr(task.contract, field_name, "")
    return refs


def _ref_present(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list | tuple | set):
        return bool(value)
    return bool(str(value or "").strip())


def _artifact_ref_is_accepted(ref: dict[str, Any]) -> bool:
    return str(ref.get("status") or "accepted").strip() in {"", "accepted"}


def _hash_status_is_for_accepted_ref(
    status: dict[str, Any],
    accepted_artifact_refs: list[dict[str, Any]],
) -> bool:
    artifact_id = str(status.get("artifact_id") or "").strip()
    path = str(status.get("path") or "").strip()
    if artifact_id:
        return any(
            artifact_id == str(ref.get("artifact_id") or "").strip()
            for ref in accepted_artifact_refs
        )
    for ref in accepted_artifact_refs:
        if str(ref.get("artifact_id") or "").strip():
            continue
        if path and path == str(ref.get("path") or "").strip():
            return True
    return False


def _resolve_artifact_path(
    raw_path: str,
    *,
    project_root: Path,
    state_dir: Path,
    workdir_path: str = "",
) -> Path | None:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        candidates = [candidate]
    else:
        candidates = []
        workdir_root = _safe_workdir_root(workdir_path, state_dir=state_dir)
        if workdir_root is not None:
            candidates.append(workdir_root / candidate)
        candidates.extend([
            project_root / candidate,
            state_dir / candidate,
        ])
    for item in candidates:
        try:
            resolved = item.resolve(strict=False)
        except Exception:
            resolved = item
        if resolved.exists():
            return resolved
    # Cross-worktree handoff fallback: an accepted artifact produced by a worker
    # often lives only in that worker's worktree
    # (state_dir/workdirs/<instance>/project/<path>) and its ref carries an empty
    # workdir_path, so project_root/state_dir resolution misses it and the
    # dispatch-time preflight raises artifact_file_missing (the cj-mono/calc
    # full-flow dev-dispatch stall). Scan worker worktrees before giving up,
    # bounded to relative, non-escaping paths that stay under workdirs.
    if not candidate.is_absolute() and ".." not in candidate.parts:
        workdirs_root = (state_dir / "workdirs").resolve(strict=False)
        for project_dir in sorted(workdirs_root.glob("*/project")):
            hit = (project_dir / candidate).resolve(strict=False)
            try:
                if not hit.is_relative_to(workdirs_root):
                    continue
            except ValueError:
                continue
            if hit.is_file():
                return hit
    return candidates[0] if candidates else None


def _safe_workdir_root(raw_path: str, *, state_dir: Path) -> Path | None:
    raw_path = raw_path.strip()
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if any(part == ".." for part in candidate.parts):
        return None
    workdirs_root = (state_dir / "workdirs").resolve(strict=False)
    candidates = [candidate] if candidate.is_absolute() else [
        state_dir / candidate,
        workdirs_root / candidate,
    ]
    for item in candidates:
        try:
            resolved = item.resolve(strict=False)
        except Exception:
            continue
        try:
            if resolved.is_relative_to(workdirs_root):
                return resolved
        except ValueError:
            continue
    return None


def _count_task_events(state_dir: Path, task_id: str) -> int:
    path = state_dir / "events.jsonl"
    if not path.exists() or not task_id:
        return 0
    needles = (f'"task_id":"{task_id}"', f'"task_id": "{task_id}"')
    return sum(
        1 for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if any(needle in line for needle in needles)
    )


def _git_head(project_root: Path) -> str:
    if not (project_root / ".git").exists():
        return ""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=project_root,
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""
