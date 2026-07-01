"""Runtime Snapshot Ledger projection helpers.

Runtime snapshots are kernel-owned artifacts that bind one dispatch/run to
the state packet, task docs, context manifest, provider session, permission
snapshot, and output contract it used. They are not runtime truth and never
mutate EventLog/TaskStore/FeatureStore.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zf.core.security.redaction import redact_obj
from zf.core.state.atomic_io import atomic_write_text


SCHEMA_VERSION = "runtime-snapshot.v1"


@dataclass(frozen=True)
class RuntimeSnapshotInput:
    state_dir: Path
    project_root: Path
    source: str
    project_id: str = ""
    task: Any | None = None
    role: Any | None = None
    dispatch_id: str = ""
    run_id: str = ""
    trace_id: str = ""
    fanout_id: str = ""
    fanout_child_id: str = ""
    stage_id: str = ""
    provider_session_id: str = ""
    provider_snapshot: Mapping[str, Any] | None = None
    capability_snapshot: Mapping[str, Any] | None = None
    refs: Mapping[str, Any] = field(default_factory=dict)
    output_contract: Mapping[str, Any] | None = None
    created_at: str = ""


@dataclass(frozen=True)
class RuntimeSnapshotWriteResult:
    snapshot: dict[str, Any]
    snapshot_id: str
    snapshot_ref: str
    json_path: Path
    md_path: Path

    def event_payload(self) -> dict[str, Any]:
        task = self.snapshot.get("task") or {}
        run = self.snapshot.get("run") or {}
        role = self.snapshot.get("role") or {}
        return {
            "schema_version": self.snapshot.get("schema_version", SCHEMA_VERSION),
            "snapshot_id": self.snapshot_id,
            "snapshot_ref": self.snapshot_ref,
            "source": self.snapshot.get("source", ""),
            "task_id": task.get("task_id", ""),
            "dispatch_id": run.get("dispatch_id", ""),
            "run_id": run.get("run_id", ""),
            "trace_id": run.get("trace_id", ""),
            "fanout_id": run.get("fanout_id", ""),
            "fanout_child_id": run.get("fanout_child_id", ""),
            "role": role.get("role", ""),
            "instance_id": role.get("instance_id", ""),
        }


def build_runtime_snapshot(data: RuntimeSnapshotInput) -> dict[str, Any]:
    task = data.task
    role = data.role
    contract = getattr(task, "contract", None)
    backend = str(getattr(role, "backend", "") or "")
    provider_snapshot = dict(data.provider_snapshot or {})
    capability_snapshot = dict(data.capability_snapshot or {})
    provider_session_id = (
        data.provider_session_id
        or str(provider_snapshot.get("provider_session_id") or "")
    )
    task_id = str(getattr(task, "id", "") or "")
    dispatch_id = data.dispatch_id or str(getattr(task, "active_dispatch_id", "") or "")
    run_id = data.run_id or dispatch_id
    source = data.source or "dispatch"
    snapshot_id = _snapshot_id(
        source=source,
        scope=task_id or run_id or data.trace_id or "runtime",
        run_key=dispatch_id or run_id or data.fanout_child_id or "latest",
    )
    output_contract = dict(data.output_contract or _output_contract(task, role))
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "created_at": data.created_at or _now_iso(),
        "source": source,
        "project": {
            "project_id": str(data.project_id or ""),
            "project_root": str(data.project_root),
            "state_dir": _display_path(data.state_dir, data.project_root),
        },
        "task": {
            "task_id": task_id,
            "status": str(getattr(task, "status", "") or ""),
            "assigned_to": str(getattr(task, "assigned_to", "") or ""),
            "active_dispatch_id": dispatch_id,
            "contract_revision": str(getattr(contract, "contract_revision", "") or ""),
            "capsule_revision": str(getattr(contract, "capsule_revision", "") or ""),
            "source_revision": str(getattr(contract, "source_revision", "") or ""),
        },
        "run": {
            "run_id": run_id,
            "trace_id": data.trace_id,
            "dispatch_id": dispatch_id,
            "fanout_id": data.fanout_id,
            "fanout_child_id": data.fanout_child_id,
            "stage_id": data.stage_id,
        },
        "role": {
            "role": str(getattr(role, "name", "") or ""),
            "instance_id": str(getattr(role, "instance_id", "") or ""),
            "role_kind": str(getattr(role, "role_kind", "") or ""),
            "backend": backend,
        },
        "provider": {
            "provider": _provider_for_backend(backend),
            "provider_session_id": provider_session_id,
            "capability_snapshot": capability_snapshot,
            "permission_snapshot": provider_snapshot,
        },
        "refs": _normalize_refs(data.refs, data.state_dir, data.project_root),
        "output_contract": output_contract,
        "redaction": {
            "secret_policy": "secret_ref_only",
            "contains_secret": False,
        },
    }
    return redact_obj(snapshot)


def write_runtime_snapshot(
    snapshot: Mapping[str, Any],
    *,
    state_dir: Path,
    project_root: Path | None = None,
) -> RuntimeSnapshotWriteResult:
    state_dir = Path(state_dir)
    project_root = Path(project_root) if project_root is not None else state_dir.parent
    scope = _safe_segment(
        _first_text(
            (snapshot.get("task") or {}).get("task_id"),
            (snapshot.get("run") or {}).get("run_id"),
            "runtime",
        )
    )
    run_key = _safe_segment(
        _first_text(
            (snapshot.get("run") or {}).get("dispatch_id"),
            (snapshot.get("run") or {}).get("run_id"),
            "latest",
        )
    )
    target_dir = state_dir / "snapshots" / scope / run_key
    json_path = target_dir / "runtime-snapshot.json"
    md_path = target_dir / "runtime-snapshot.md"
    body = dict(snapshot)
    snapshot_id = str(body.get("snapshot_id") or _snapshot_id(
        source=str(body.get("source") or "runtime"),
        scope=scope,
        run_key=run_key,
    ))
    body["snapshot_id"] = snapshot_id
    atomic_write_text(
        json_path,
        json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(md_path, render_runtime_snapshot_md(body))
    snapshot_ref = _display_path(json_path, project_root)
    return RuntimeSnapshotWriteResult(
        snapshot=body,
        snapshot_id=snapshot_id,
        snapshot_ref=snapshot_ref,
        json_path=json_path,
        md_path=md_path,
    )


def render_runtime_snapshot_md(snapshot: Mapping[str, Any]) -> str:
    task = snapshot.get("task") or {}
    run = snapshot.get("run") or {}
    role = snapshot.get("role") or {}
    refs = snapshot.get("refs") or {}
    output = snapshot.get("output_contract") or {}
    lines = [
        f"# Runtime Snapshot · {snapshot.get('snapshot_id', '')}",
        "",
        "> projection only, not runtime truth",
        "",
        f"- schema_version: `{snapshot.get('schema_version', SCHEMA_VERSION)}`",
        f"- source: `{snapshot.get('source', '')}`",
        f"- created_at: `{snapshot.get('created_at', '')}`",
        f"- task_id: `{task.get('task_id', '')}`",
        f"- dispatch_id: `{run.get('dispatch_id', '')}`",
        f"- run_id: `{run.get('run_id', '')}`",
        f"- role: `{role.get('role', '')}` / `{role.get('instance_id', '')}`",
        f"- backend: `{role.get('backend', '')}`",
        f"- expected_event: `{output.get('expected_event', '')}`",
        "",
        "## Refs",
        "",
    ]
    if isinstance(refs, Mapping) and refs:
        for key in sorted(refs):
            value = refs.get(key)
            if value:
                lines.append(f"- `{key}`: `{value}`")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def runtime_snapshot_event_payload(result: RuntimeSnapshotWriteResult) -> dict[str, Any]:
    return result.event_payload()


def read_runtime_snapshot(path: Path | str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_snapshot_ref(
    state_dir: Path,
    snapshot_ref: str,
    *,
    project_root: Path | None = None,
) -> Path:
    ref = str(snapshot_ref or "").strip()
    path = Path(ref)
    if path.is_absolute():
        return path
    root = Path(project_root) if project_root is not None else Path(state_dir).parent
    candidate = root / path
    if candidate.exists() or ref.startswith(str(Path(state_dir).name) + "/"):
        return candidate
    return Path(state_dir) / path


def latest_snapshot_ref_for_dispatch(
    events: list[Any],
    *,
    task_id: str = "",
    dispatch_id: str = "",
    source: str = "",
) -> str:
    for event in reversed(events):
        if getattr(event, "type", "") != "runtime.snapshot.recorded":
            continue
        if task_id and getattr(event, "task_id", "") != task_id:
            continue
        payload = getattr(event, "payload", {})
        if not isinstance(payload, Mapping):
            continue
        if dispatch_id and str(payload.get("dispatch_id") or "") != dispatch_id:
            continue
        if source and str(payload.get("source") or "") != source:
            continue
        ref = str(payload.get("snapshot_ref") or "")
        if ref:
            return ref
    return ""


def _normalize_refs(
    refs: Mapping[str, Any],
    state_dir: Path,
    project_root: Path,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in refs.items():
        if value is None:
            continue
        if isinstance(value, Path):
            out[str(key)] = _display_path(value, project_root)
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                continue
            path = Path(text)
            out[str(key)] = (
                _display_path(path, project_root)
                if path.is_absolute()
                else text
            )
        elif isinstance(value, list | tuple):
            out[str(key)] = [
                _display_path(item, project_root) if isinstance(item, Path) else item
                for item in value
            ]
        else:
            out[str(key)] = value
    return redact_obj(out)


def _output_contract(task: Any | None, role: Any | None) -> dict[str, Any]:
    contract = getattr(task, "contract", None)
    expected_event = ""
    publishes = getattr(role, "publishes", None) or []
    if publishes:
        expected_event = str(publishes[0] or "")
    if not expected_event:
        expected_event = {
            "arch": "arch.proposal.done",
            "critic": "design.critique.done",
            "dev": "dev.build.done",
            "review": "review.approved",
            "verify": "verify.passed",
            "verifier": "verify.passed",
            "test": "test.passed",
            "judge": "judge.passed",
            "static_gate": "static_gate.passed",
        }.get(str(getattr(role, "name", "") or ""), "")
    return {
        "expected_event": expected_event,
        "verification_tiers": list(getattr(contract, "verification_tiers", []) or []),
        "evidence_contract": dict(getattr(contract, "evidence_contract", {}) or {}),
    }


def _provider_for_backend(backend: str) -> str:
    if backend.startswith("claude"):
        return "claude"
    if backend.startswith("codex"):
        return "codex"
    if backend:
        return backend
    return ""


def _display_path(path: Path, project_root: Path) -> str:
    path = Path(path)
    try:
        return str(path.resolve(strict=False).relative_to(project_root.resolve(strict=False)))
    except Exception:
        return str(path)


def _snapshot_id(*, source: str, scope: str, run_key: str) -> str:
    return f"snap-{_safe_segment(source)}-{_safe_segment(scope)}-{_safe_segment(run_key)}"


def _safe_segment(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", text)
    text = text.strip(".-")
    return text[:96] or "unknown"


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "SCHEMA_VERSION",
    "RuntimeSnapshotInput",
    "RuntimeSnapshotWriteResult",
    "build_runtime_snapshot",
    "latest_snapshot_ref_for_dispatch",
    "read_runtime_snapshot",
    "render_runtime_snapshot_md",
    "resolve_snapshot_ref",
    "runtime_snapshot_event_payload",
    "write_runtime_snapshot",
]
