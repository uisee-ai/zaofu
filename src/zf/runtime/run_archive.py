"""Run archive and projection services.

This module is the deterministic boundary for Project/Test Task/Run
Archive V1. It keeps run history in events + immutable artifacts, while
``.zf/runs/active.json`` and ``.zf/runs/index.json`` remain rebuildable
projections.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.safety import PathGuard, PathGuardError
from zf.core.security.redaction import redact_obj, redact_text
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.core.task.store import TERMINAL_STATES as TASK_TERMINAL_STATES
from zf.core.task.store import TaskStore
from zf.runtime.git_capture import capture_git_state
from zf.runtime.feature_completion import close_feature_if_all_tasks_done


RUN_EVENT_TYPES: frozenset[str] = frozenset({
    "run.started",
    "run.heartbeat",
    "run.stalled",
    "run.cancelled",
    "run.completed",
    "run.archived",
    "run.abandoned",
})

RUN_STATUSES: frozenset[str] = frozenset({
    "queued",
    "running",
    "passed",
    "failed",
    "cancelled",
    "abandoned",
})

_STATUS_ALIASES = {
    "pass": "passed",
    "ok": "passed",
    "success": "passed",
    "fail": "failed",
    "error": "failed",
    "timeout": "failed",
    "canceled": "cancelled",
    "cancelled": "cancelled",
}
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TEXT_EXTENSIONS = {
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".txt",
    ".md",
    ".log",
}
_DEFAULT_TEXT_LIMIT = 1_000_000


class RunArchiveError(ValueError):
    pass


@dataclass
class ArtifactRecord:
    path: str
    kind: str
    bytes: int = 0
    sha256: str = ""
    source: str = ""
    redacted: bool = False
    truncated: bool = False
    missing: bool = False
    unsafe_raw: bool = False


@dataclass
class RunMetadata:
    version: int = 1
    run_id: str = ""
    trace_id: str = ""
    kind: str = "e2e"
    owner_project_id: str = ""
    target_project_id: str = ""
    workspace_id: str | None = None
    test_task_id: str = ""
    scenario_id: str = ""
    attempt: int = 1
    preset: str = ""
    target_config: str = ""
    command: str = ""
    live_state_dir: str = ""
    artifact_dir: str = ""
    status: str = ""
    exit_code: int | None = None
    started_at: str = ""
    ended_at: str = ""
    archived_at: str = ""
    git: dict[str, Any] = field(default_factory=dict)
    provider: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunArchiveResult:
    run_id: str
    status: str
    artifact_dir: Path
    run_yaml_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


@dataclass
class RunProjectionResult:
    active: dict[str, Any]
    index: dict[str, Any]


@dataclass
class ReconcileResult:
    inspected: int = 0
    stalled: int = 0
    archived: int = 0
    abandoned: int = 0
    errors: list[str] = field(default_factory=list)


def normalize_run_status(status: str) -> str:
    value = (status or "").strip().lower()
    value = _STATUS_ALIASES.get(value, value)
    if value not in RUN_STATUSES:
        raise RunArchiveError(f"invalid run status: {status!r}")
    return value


def validate_run_id(run_id: str) -> str:
    value = str(run_id or "").strip()
    if not value:
        raise RunArchiveError("run_id is required")
    if "/" in value or "\\" in value or ".." in value:
        raise RunArchiveError(f"unsafe run_id: {run_id!r}")
    if not _RUN_ID_RE.match(value):
        raise RunArchiveError(f"unsafe run_id: {run_id!r}")
    return value


def archive_run(
    *,
    project_root: Path | None = None,
    owner_project: Path | None = None,
    state_dir: Path,
    live_state_dir: Path,
    run_id: str,
    status: str,
    trace_id: str = "",
    test_task_id: str = "",
    scenario_id: str = "",
    target_project_id: str = "",
    target_config: str = "",
    preset: str = "",
    command: str = "",
    exit_code: int | None = None,
    run_root: Path | None = None,
    started_at: str = "",
    ended_at: str = "",
    provider: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    allow_existing: bool = True,
) -> RunArchiveResult:
    """Archive one live run state into ``state_dir/runs/<run_id>``.

    ``project_root`` is intentionally explicit. Code that only has a
    state dir must resolve a ProjectContext first instead of assuming
    ``state_dir.parent`` is the project root.
    """
    if project_root is None:
        if owner_project is None:
            raise RunArchiveError("project_root or owner_project is required")
        project_root = owner_project
    project_root = Path(project_root).resolve(strict=False)
    state_dir = Path(state_dir).resolve(strict=False)
    live_state_dir = Path(live_state_dir).resolve(strict=False)
    run_id = validate_run_id(run_id)
    status = normalize_run_status(status)

    if run_root is not None:
        PathGuard.assert_under(live_state_dir, Path(run_root))

    runs_root = state_dir / "runs"
    artifact_dir = PathGuard.assert_under(runs_root / run_id, runs_root)
    manifest_path = artifact_dir / "artifact_manifest.json"
    run_yaml_path = artifact_dir / "run.yaml"

    if manifest_path.exists() and allow_existing:
        manifest = _read_json_obj(manifest_path)
        return RunArchiveResult(
            run_id=run_id,
            status=str(manifest.get("status") or status),
            artifact_dir=artifact_dir,
            run_yaml_path=run_yaml_path,
            manifest_path=manifest_path,
            manifest=manifest,
        )
    if manifest_path.exists() and not allow_existing:
        raise RunArchiveError(f"run archive already exists: {manifest_path}")

    artifact_dir.mkdir(parents=True, exist_ok=True)
    records: list[ArtifactRecord] = []

    now = _now_iso()
    started_at = started_at or _event_ts_for_run(state_dir, run_id, "run.started") or now
    ended_at = ended_at or _event_ts_for_run(state_dir, run_id, "run.completed") or now
    archived_at = now
    summary = dict(summary or {})
    summary.update({k: v for k, v in _summary_from_live_state(live_state_dir).items() if k not in summary})

    metadata = RunMetadata(
        run_id=run_id,
        trace_id=trace_id,
        owner_project_id=project_root.name,
        target_project_id=target_project_id or project_root.name,
        test_task_id=test_task_id,
        scenario_id=scenario_id,
        preset=preset,
        target_config=target_config,
        command=command,
        live_state_dir=str(live_state_dir),
        artifact_dir=str(Path(".zf") / "runs" / run_id),
        status=status,
        exit_code=exit_code,
        started_at=started_at,
        ended_at=ended_at,
        archived_at=archived_at,
        git=_git_payload(project_root),
        provider=dict(provider or {}),
        summary=summary,
    )
    _write_yaml(run_yaml_path, asdict(metadata))
    records.append(_artifact_record(run_yaml_path, artifact_dir, kind="metadata"))

    _write_text_artifact(artifact_dir, "command.txt", command + ("\n" if command else ""))
    records.append(_artifact_record(artifact_dir / "command.txt", artifact_dir, kind="reproducibility"))

    _copy_first_existing(
        records,
        artifact_dir=artifact_dir,
        dest_name="resolved_zf.yaml",
        candidates=[
            _path_if_exists(live_state_dir.parent / "zf.yaml"),
            _path_if_exists(project_root / target_config) if target_config else None,
            _path_if_exists(project_root / "zf.yaml"),
        ],
        default="",
        kind="reproducibility",
    )
    _copy_first_existing(
        records,
        artifact_dir=artifact_dir,
        dest_name="config_snapshot.yaml",
        candidates=[_path_if_exists(project_root / "zf.yaml")],
        default="",
        kind="reproducibility",
    )
    _write_json_artifact(
        artifact_dir,
        "env_redacted.json",
        redact_obj(dict(env or {})),
    )
    records.append(_artifact_record(
        artifact_dir / "env_redacted.json",
        artifact_dir,
        kind="reproducibility",
        redacted=True,
    ))

    _copy_file_artifact(
        records,
        live_state_dir / "events.jsonl",
        artifact_dir / "events.jsonl",
        artifact_root=artifact_dir,
        default="",
        kind="runtime",
    )
    _copy_file_artifact(
        records,
        live_state_dir / "cost.jsonl",
        artifact_dir / "cost.jsonl",
        artifact_root=artifact_dir,
        default="",
        kind="runtime",
    )
    _copy_file_artifact(
        records,
        live_state_dir / "kanban.json",
        artifact_dir / "kanban_active.json",
        artifact_root=artifact_dir,
        default="[]\n",
        kind="runtime",
    )
    _copy_file_artifact(
        records,
        live_state_dir / "feature_list.json",
        artifact_dir / "feature_active.json",
        artifact_root=artifact_dir,
        default="[]\n",
        kind="runtime",
    )
    _copy_dir_artifact(
        records,
        live_state_dir / "kanban",
        artifact_dir / "kanban_archive",
        artifact_root=artifact_dir,
        kind="runtime",
    )
    _copy_dir_artifact(
        records,
        live_state_dir / "feature_list",
        artifact_dir / "feature_archive",
        artifact_root=artifact_dir,
        kind="runtime",
    )
    for dirname in ("fanouts", "candidates", "diagnostics"):
        _copy_dir_artifact(
            records,
            live_state_dir / dirname,
            artifact_dir / dirname,
            artifact_root=artifact_dir,
            kind="runtime",
        )

    _copy_file_artifact(
        records,
        live_state_dir / "role_sessions.yaml",
        artifact_dir / "role_sessions.redacted.yaml",
        artifact_root=artifact_dir,
        default="{}\n",
        kind="runtime",
        redacted=True,
    )
    _copy_file_artifact(
        records,
        live_state_dir / "session.yaml",
        artifact_dir / "session.redacted.yaml",
        artifact_root=artifact_dir,
        default="{}\n",
        kind="runtime",
        redacted=True,
    )
    _copy_file_artifact(
        records,
        live_state_dir / "session_recording.jsonl",
        artifact_dir / "session_recording.redacted.jsonl",
        artifact_root=artifact_dir,
        default="",
        kind="runtime",
        redacted=True,
    )

    _copy_file_artifact(
        records,
        live_state_dir / "phase_report.txt",
        artifact_dir / "phase_report.txt",
        artifact_root=artifact_dir,
        default=_phase_report_text(summary, status),
        kind="summary",
    )
    _copy_file_artifact(
        records,
        live_state_dir / "cost_by_backend.txt",
        artifact_dir / "cost_by_backend.txt",
        artifact_root=artifact_dir,
        default=_cost_by_backend_text(live_state_dir / "cost.jsonl"),
        kind="summary",
    )
    _copy_file_artifact(
        records,
        live_state_dir / "scorecard.json",
        artifact_dir / "scorecard.json",
        artifact_root=artifact_dir,
        default=_minimal_scorecard(metadata),
        kind="summary",
    )
    _copy_file_artifact(
        records,
        live_state_dir / "postmortem.md",
        artifact_dir / "postmortem.md",
        artifact_root=artifact_dir,
        default=_postmortem_text(metadata),
        kind="summary",
    )

    manifest = {
        "version": 1,
        "run_id": run_id,
        "trace_id": trace_id,
        "status": status,
        "created_at": archived_at,
        "artifact_dir": str(artifact_dir),
        "live_state_dir": str(live_state_dir),
        "run_yaml": "run.yaml",
        "artifacts": [asdict(record) for record in records],
        "missing": [record.path for record in records if record.missing],
        "redacted": [record.path for record in records if record.redacted],
        "truncated": [record.path for record in records if record.truncated],
        "summary": summary,
    }
    atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return RunArchiveResult(
        run_id=run_id,
        status=status,
        artifact_dir=artifact_dir,
        run_yaml_path=run_yaml_path,
        manifest_path=manifest_path,
        manifest=manifest,
    )


class RunProjector:
    def __init__(
        self,
        *,
        project_root: Path,
        state_dir: Path,
        event_log: EventLog | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve(strict=False)
        self.state_dir = Path(state_dir).resolve(strict=False)
        self.event_log = event_log or EventLog(self.state_dir / "events.jsonl")

    @property
    def runs_root(self) -> Path:
        return self.state_dir / "runs"

    def rebuild(self, *, write: bool = True) -> RunProjectionResult:
        runs: dict[str, dict[str, Any]] = {}
        active: dict[str, dict[str, Any]] = {}
        for event in self.event_log.read_all():
            if event.type not in RUN_EVENT_TYPES:
                continue
            run_id = _run_id_from_event(event)
            if not run_id:
                continue
            entry = runs.setdefault(run_id, _base_projection_entry(run_id, event))
            _merge_event_into_entry(entry, event)
            if event.type == "run.started":
                active[run_id] = entry
            elif event.type == "run.heartbeat":
                active.setdefault(run_id, entry)
            elif event.type == "run.stalled":
                active.setdefault(run_id, entry)
                entry["health"] = "stalled"
                entry["stalled_at"] = event.ts
            elif event.type in {"run.completed", "run.cancelled", "run.abandoned"}:
                active.setdefault(run_id, entry)
            elif event.type == "run.archived":
                entry["archived_at"] = event.ts
                entry["artifact_dir"] = str((event.payload or {}).get("artifact_dir") or entry.get("artifact_dir") or "")
                active.pop(run_id, None)

        for run_dir in sorted(self.runs_root.glob("*")) if self.runs_root.exists() else []:
            if not run_dir.is_dir():
                continue
            run_id = run_dir.name
            try:
                validate_run_id(run_id)
            except RunArchiveError:
                continue
            manifest_path = run_dir / "artifact_manifest.json"
            run_yaml_path = run_dir / "run.yaml"
            if not manifest_path.exists() and not run_yaml_path.exists():
                continue
            manifest = _read_json_obj(manifest_path) if manifest_path.exists() else {}
            metadata = _read_yaml_obj(run_yaml_path) if run_yaml_path.exists() else {}
            entry = runs.setdefault(run_id, _base_projection_entry(run_id))
            _merge_metadata_into_entry(entry, metadata, manifest, run_dir)
            if manifest_path.exists():
                active.pop(run_id, None)

        active_doc = {
            "version": 1,
            "active_runs": sorted(active.values(), key=lambda item: str(item.get("started_at") or "")),
        }
        index_doc = {
            "version": 1,
            "runs": sorted(
                [
                    item for item in runs.values()
                    if item.get("archived_at") or item.get("artifact_dir")
                ],
                key=lambda item: str(item.get("started_at") or ""),
                reverse=True,
            ),
        }
        if write:
            self.write(active_doc=active_doc, index_doc=index_doc)
        return RunProjectionResult(active=active_doc, index=index_doc)

    def write(self, *, active_doc: dict[str, Any], index_doc: dict[str, Any]) -> None:
        self.runs_root.mkdir(parents=True, exist_ok=True)
        active_path = self.runs_root / "active.json"
        index_path = self.runs_root / "index.json"
        with locked_path(active_path):
            atomic_write_text(active_path, json.dumps(active_doc, ensure_ascii=False, indent=2) + "\n")
        with locked_path(index_path):
            atomic_write_text(index_path, json.dumps(index_doc, ensure_ascii=False, indent=2) + "\n")

    def load_active(self) -> dict[str, Any]:
        return _read_json_or_rebuild(
            self.runs_root / "active.json",
            lambda: self.rebuild(write=False).active,
        )

    def load_index(self) -> dict[str, Any]:
        return _read_json_or_rebuild(
            self.runs_root / "index.json",
            lambda: self.rebuild(write=False).index,
        )


def read_run_detail(*, state_dir: Path, run_id: str) -> dict[str, Any]:
    run_dir = _safe_run_dir(state_dir, run_id)
    run_yaml = _read_yaml_obj(run_dir / "run.yaml")
    manifest = _read_json_obj(run_dir / "artifact_manifest.json")
    return {
        "run_id": validate_run_id(run_id),
        "run": run_yaml,
        "manifest": manifest,
        "artifact_dir": str(run_dir),
        "artifacts": manifest.get("artifacts", []) if isinstance(manifest, dict) else [],
        "empty": not run_yaml and not manifest,
    }


def read_run_events(*, state_dir: Path, run_id: str) -> list[dict[str, Any]]:
    run_dir = _safe_run_dir(state_dir, run_id)
    path = run_dir / "events.jsonl"
    events = EventLog(path).read_all()
    return [
        {
            "seq": index,
            "id": event.id,
            "ts": event.ts,
            "type": event.type,
            "actor": event.actor,
            "task_id": event.task_id,
            "payload": redact_obj(event.payload),
            "causation_id": event.causation_id,
            "correlation_id": event.correlation_id,
        }
        for index, event in enumerate(events, start=1)
    ]


def read_task_runs(
    *,
    project_root: Path,
    state_dir: Path,
    task_id: str,
) -> list[dict[str, Any]]:
    projector = RunProjector(project_root=project_root, state_dir=state_dir)
    docs = [
        *(projector.load_active().get("active_runs") or []),
        *(projector.load_index().get("runs") or []),
    ]
    return [
        item for item in docs
        if isinstance(item, dict) and str(item.get("test_task_id") or item.get("task_id") or "") == task_id
    ]


def close_test_task_for_passed_run(
    *,
    state_dir: Path,
    event_log: EventLog,
    writer: EventWriter,
    test_task_id: str,
    run_id: str,
    status: str,
    completion_event: ZfEvent | None = None,
    trace_id: str = "",
) -> ZfEvent | None:
    """Mark the owner Test Task done when its archived run passed.

    The owner Test Task is a validation wrapper around a run, so its
    terminal predicate is ``run.completed(status=passed)`` rather than the
    target project's internal ``judge.passed`` or ``test.passed`` event.
    """
    task_id = str(test_task_id or "")
    if not task_id:
        return None
    try:
        normalized = normalize_run_status(status)
    except RunArchiveError:
        return None
    if normalized != "passed":
        return None

    task_store = TaskStore(Path(state_dir) / "kanban.json")
    task = task_store.get(task_id)
    if task is None or task.status in TASK_TERMINAL_STATES:
        return None

    updated = task_store.update(task_id, status="done")
    if updated is None:
        return None

    event = writer.emit(
        "task.status_changed",
        actor="zf-cli",
        task_id=task_id,
        causation_id=completion_event.id if completion_event else None,
        correlation_id=trace_id or None,
        payload={
            "from": task.status,
            "to": "done",
            "source": "run_completed",
            "run_id": run_id,
            "trigger_event": "run.completed",
        },
    )
    try:
        close_feature_if_all_tasks_done(
            state_dir=Path(state_dir),
            task=task,
            task_store=task_store,
            event_writer=writer,
            event_log=event_log,
            actor="zf-cli",
            source="run_completed",
            trigger_event="run.completed",
        )
    except Exception:
        pass
    return event


def run_and_archive_command(
    *,
    project_root: Path,
    state_dir: Path,
    live_state_dir: Path,
    run_id: str,
    command: str,
    trace_id: str = "",
    test_task_id: str = "",
    scenario_id: str = "",
    target_project_id: str = "",
    target_config: str = "",
    preset: str = "",
    run_root: Path | None = None,
    timeout: float | None = None,
) -> RunArchiveResult:
    """Execute a command, emit lifecycle events, and archive evidence."""
    event_log = EventLog(Path(state_dir) / "events.jsonl")
    writer = EventWriter(event_log, correlation_id=trace_id or None)
    run_id = validate_run_id(run_id)
    started = _now_iso()
    ended = ""
    exit_code: int | None = None
    status = "abandoned"
    completion_payload: dict[str, Any] = {}
    start_event = writer.emit(
        "run.started",
        actor="zf-cli",
        task_id=test_task_id or None,
        correlation_id=trace_id or None,
        payload={
            "run_id": run_id,
            "scenario_id": scenario_id,
            "target_project_id": target_project_id,
            "target_config": target_config,
            "live_state_dir": str(live_state_dir),
            "status": "running",
            "command": command,
        },
    )
    projector = RunProjector(project_root=project_root, state_dir=state_dir, event_log=event_log)
    projector.rebuild(write=True)
    try:
        proc = subprocess.run(
            command,
            cwd=project_root,
            shell=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        exit_code = proc.returncode
        if not Path(live_state_dir).exists():
            status = "abandoned"
            completion_payload = {
                "run_id": run_id,
                "reason": "live_state_dir_missing",
                "exit_code": exit_code,
            }
        else:
            status = "passed" if exit_code == 0 else "failed"
            completion_payload = {
                "run_id": run_id,
                "status": status,
                "exit_code": exit_code,
                "validation_status": status,
            }
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        if Path(live_state_dir).exists():
            status = "failed"
            completion_payload = {
                "run_id": run_id,
                "status": status,
                "exit_code": exit_code,
                "validation_status": status,
                "reason": "timeout",
                "timeout_seconds": timeout,
            }
        else:
            status = "abandoned"
            completion_payload = {
                "run_id": run_id,
                "reason": "timeout_live_state_dir_missing",
                "exit_code": exit_code,
                "timeout_seconds": timeout,
                "error": str(exc),
            }
    except Exception as exc:  # noqa: BLE001 - archive recoverable evidence before returning
        status = "abandoned"
        completion_payload = {
            "run_id": run_id,
            "reason": "wrapper_error",
            "error": str(exc),
        }
    ended = _now_iso()
    if status == "abandoned":
        completion_event = writer.emit(
            "run.abandoned",
            actor="zf-cli",
            task_id=test_task_id or None,
            causation_id=start_event.id,
            correlation_id=trace_id or None,
            payload=completion_payload,
        )
    else:
        completion_event = writer.emit(
            "run.completed",
            actor="zf-cli",
            task_id=test_task_id or None,
            causation_id=start_event.id,
            correlation_id=trace_id or None,
            payload=completion_payload,
        )
    close_test_task_for_passed_run(
        state_dir=state_dir,
        event_log=event_log,
        writer=writer,
        test_task_id=test_task_id,
        run_id=run_id,
        status=status,
        completion_event=completion_event,
        trace_id=trace_id,
    )
    result = archive_run(
        project_root=project_root,
        state_dir=state_dir,
        live_state_dir=live_state_dir,
        run_id=run_id,
        trace_id=trace_id,
        test_task_id=test_task_id,
        scenario_id=scenario_id,
        target_project_id=target_project_id,
        target_config=target_config,
        preset=preset,
        command=command,
        status=status,
        exit_code=exit_code,
        run_root=run_root,
        started_at=started,
        ended_at=ended,
        env=dict(os.environ),
    )
    writer.emit(
        "run.archived",
        actor="zf-cli",
        task_id=test_task_id or None,
        correlation_id=trace_id or None,
        payload={
            "run_id": run_id,
            "artifact_dir": str(result.artifact_dir),
            "artifact_manifest": str(result.manifest_path),
        },
    )
    projector.rebuild(write=True)
    return result


def reconcile_runs(
    *,
    project_root: Path,
    state_dir: Path,
    stale_after_seconds: float = 900.0,
) -> ReconcileResult:
    event_log = EventLog(Path(state_dir) / "events.jsonl")
    writer = EventWriter(event_log)
    projector = RunProjector(project_root=project_root, state_dir=state_dir, event_log=event_log)
    projection = projector.rebuild(write=False).active
    result = ReconcileResult()
    now = datetime.now(timezone.utc)
    for entry in projection.get("active_runs", []) or []:
        if not isinstance(entry, dict):
            continue
        result.inspected += 1
        run_id = str(entry.get("run_id") or "")
        try:
            validate_run_id(run_id)
        except RunArchiveError:
            result.errors.append(f"invalid run_id {run_id!r}")
            continue
        if _pid_alive(entry.get("pid")) and not _heartbeat_stale(entry, now, stale_after_seconds):
            continue
        trace_id = str(entry.get("trace_id") or "")
        task_id = str(entry.get("test_task_id") or entry.get("task_id") or "")
        live_state_dir = Path(str(entry.get("live_state_dir") or ""))
        writer.emit(
            "run.stalled",
            actor="zf-cli",
            task_id=task_id or None,
            correlation_id=trace_id or None,
            payload={
                "run_id": run_id,
                "threshold_seconds": stale_after_seconds,
                "live_state_dir": str(live_state_dir),
            },
        )
        result.stalled += 1
        if not live_state_dir.exists():
            writer.emit(
                "run.abandoned",
                actor="zf-cli",
                task_id=task_id or None,
                correlation_id=trace_id or None,
                payload={"run_id": run_id, "reason": "live_state_dir_missing"},
            )
            result.abandoned += 1
            continue
        writer.emit(
            "run.abandoned",
            actor="zf-cli",
            task_id=task_id or None,
            correlation_id=trace_id or None,
            payload={"run_id": run_id, "reason": "stale_or_dead"},
        )
        result.abandoned += 1
        try:
            archive = archive_run(
                project_root=project_root,
                state_dir=state_dir,
                live_state_dir=live_state_dir,
                run_id=run_id,
                trace_id=trace_id,
                test_task_id=task_id,
                scenario_id=str(entry.get("scenario_id") or ""),
                target_project_id=str(entry.get("target_project_id") or ""),
                target_config=str(entry.get("target_config") or ""),
                status="abandoned",
                command=str(entry.get("command") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - report and continue reconciling
            result.errors.append(f"{run_id}: {exc}")
            continue
        writer.emit(
            "run.archived",
            actor="zf-cli",
            task_id=task_id or None,
            correlation_id=trace_id or None,
            payload={
                "run_id": run_id,
                "artifact_dir": str(archive.artifact_dir),
                "artifact_manifest": str(archive.manifest_path),
            },
        )
        result.archived += 1
    projector.rebuild(write=True)
    return result


def _safe_run_dir(state_dir: Path, run_id: str) -> Path:
    run_id = validate_run_id(run_id)
    runs_root = Path(state_dir).resolve(strict=False) / "runs"
    return PathGuard.assert_under(runs_root / run_id, runs_root)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def _write_text_artifact(root: Path, name: str, text: str) -> None:
    path = PathGuard.assert_under(root / name, root)
    atomic_write_text(path, text)


def _write_json_artifact(root: Path, name: str, data: Any) -> None:
    _write_text_artifact(root, name, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _copy_first_existing(
    records: list[ArtifactRecord],
    *,
    artifact_dir: Path,
    dest_name: str,
    candidates: list[Path | None],
    default: str,
    kind: str,
) -> None:
    source = next((path for path in candidates if path is not None and path.exists()), None)
    _copy_file_artifact(
        records,
        source,
        artifact_dir / dest_name,
        artifact_root=artifact_dir,
        default=default,
        kind=kind,
    )


def _copy_file_artifact(
    records: list[ArtifactRecord],
    source: Path | None,
    dest: Path,
    *,
    artifact_root: Path,
    default: str,
    kind: str,
    redacted: bool = False,
) -> None:
    root = artifact_root
    if source is not None:
        source = Path(source)
    dest = PathGuard.assert_under(dest, root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    missing = source is None or not source.exists() or not source.is_file()
    truncated = False
    if missing:
        atomic_write_text(dest, default)
    elif redacted or _is_text_file(source):
        text = source.read_text(encoding="utf-8", errors="replace")
        if len(text) > _DEFAULT_TEXT_LIMIT:
            text = text[:_DEFAULT_TEXT_LIMIT] + "\n[TRUNCATED]\n"
            truncated = True
        if redacted:
            text = redact_text(text)
        atomic_write_text(dest, text)
    else:
        shutil.copy2(source, dest)
    records.append(
        _artifact_record(
            dest,
            root,
            kind=kind,
            source=str(source) if source is not None else "",
            redacted=redacted,
            truncated=truncated,
            missing=missing,
        )
    )


def _copy_dir_artifact(
    records: list[ArtifactRecord],
    source: Path,
    dest: Path,
    *,
    artifact_root: Path,
    kind: str,
) -> None:
    root = artifact_root
    dest = PathGuard.assert_under(dest, root)
    missing = not source.exists() or not source.is_dir()
    if dest.exists():
        shutil.rmtree(dest)
    if missing:
        dest.mkdir(parents=True, exist_ok=True)
    else:
        shutil.copytree(source, dest)
    records.append(
        _artifact_record(
            dest,
            root,
            kind=kind,
            source=str(source),
            missing=missing,
        )
    )


def _artifact_record(
    path: Path,
    root: Path,
    *,
    kind: str,
    source: str = "",
    redacted: bool = False,
    truncated: bool = False,
    missing: bool = False,
) -> ArtifactRecord:
    path = Path(path)
    rel = str(path.relative_to(root))
    if path.is_dir():
        digest = _hash_dir(path)
        size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    else:
        digest = _hash_file(path)
        size = path.stat().st_size if path.exists() else 0
    return ArtifactRecord(
        path=rel,
        kind=kind,
        bytes=size,
        sha256=digest,
        source=source,
        redacted=redacted,
        truncated=truncated,
        missing=missing,
    )


def _hash_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_dir(path: Path) -> str:
    if not path.exists() or not path.is_dir():
        return ""
    h = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file():
            h.update(str(item.relative_to(path)).encode("utf-8"))
            h.update(b"\0")
            h.update(_hash_file(item).encode("ascii"))
    return h.hexdigest()


def _path_if_exists(path: Path) -> Path | None:
    return path if path.exists() else None


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTENSIONS


def _read_json_obj(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_yaml_obj(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_json_or_rebuild(path: Path, rebuild) -> dict[str, Any]:
    data = _read_json_obj(path)
    if data:
        return data
    return rebuild()


def _summary_from_live_state(live_state_dir: Path) -> dict[str, Any]:
    events = _read_event_dicts(live_state_dir / "events.jsonl")
    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("type") or "")
        counts[event_type] = counts.get(event_type, 0) + 1
    return {
        "events": len(events),
        "event_counts": counts,
        "fanout_children": counts.get("fanout.child.completed", 0) + counts.get("fanout.child.failed", 0),
        "children_completed": counts.get("fanout.child.completed", 0),
        "children_failed": counts.get("fanout.child.failed", 0),
    }


def _read_event_dicts(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "event" in value and "sig" in value:
            value = value.get("event") or {}
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _git_payload(project_root: Path) -> dict[str, Any]:
    state = capture_git_state(project_root)
    return {
        "repo": str(project_root),
        "branch": state.branch or "",
        "commit": state.head or "",
        "dirty": bool(state.dirty_files),
        "dirty_files": list(state.dirty_files),
        "last_commit_msg": state.last_commit_msg,
        "captured_at": state.ts,
    }


def _minimal_scorecard(metadata: RunMetadata) -> str:
    data = {
        "scenario": metadata.scenario_id,
        "preset": metadata.preset,
        "status": metadata.status,
        "exit_code": metadata.exit_code,
        "run_id": metadata.run_id,
        "trace_id": metadata.trace_id,
        "event_count": metadata.summary.get("events", 0),
        "artifact_completeness": {},
    }
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _phase_report_text(summary: dict[str, Any], status: str) -> str:
    return "\n".join([
        "Run Phase Report",
        "",
        f"status: {status}",
        f"events: {summary.get('events', 0)}",
        "",
    ])


def _cost_by_backend_text(cost_path: Path) -> str:
    totals: dict[str, dict[str, float]] = {}
    for entry in _read_jsonl(cost_path):
        backend = str(entry.get("backend") or "unknown")
        bucket = totals.setdefault(
            backend,
            {"entries": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
        bucket["entries"] += 1
        bucket["input_tokens"] += float(entry.get("input_tokens", 0) or 0)
        bucket["output_tokens"] += float(entry.get("output_tokens", 0) or 0)
        bucket["cost_usd"] += float(entry.get("cost_usd", 0.0) or 0.0)
    if not totals:
        return "backend,entries,input_tokens,output_tokens,cost_usd\n"
    lines = ["backend,entries,input_tokens,output_tokens,cost_usd"]
    for backend, bucket in sorted(totals.items()):
        lines.append(
            f"{backend},{bucket['entries']:.0f},{bucket['input_tokens']:.0f},"
            f"{bucket['output_tokens']:.0f},{bucket['cost_usd']:.6f}"
        )
    return "\n".join(lines) + "\n"


def _postmortem_text(metadata: RunMetadata) -> str:
    return "\n".join([
        f"# {metadata.scenario_id or metadata.run_id} Run Postmortem",
        "",
        f"- run_id: {metadata.run_id}",
        f"- trace_id: {metadata.trace_id}",
        f"- status: {metadata.status}",
        f"- exit_code: {metadata.exit_code}",
        f"- events: {metadata.summary.get('events', 0)}",
        "",
    ])


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _event_ts_for_run(state_dir: Path, run_id: str, event_type: str) -> str:
    try:
        events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        return ""
    for event in reversed(events):
        if event.type == event_type and _run_id_from_event(event) == run_id:
            return event.ts
    return ""


def _run_id_from_event(event: ZfEvent) -> str:
    payload = event.payload or {}
    return str(payload.get("run_id") or "")


def _base_projection_entry(run_id: str, event: ZfEvent | None = None) -> dict[str, Any]:
    payload = (event.payload if event else {}) or {}
    return {
        "run_id": run_id,
        "trace_id": str((event.correlation_id if event else "") or payload.get("trace_id") or ""),
        "test_task_id": str((event.task_id if event else "") or payload.get("test_task_id") or payload.get("task_id") or ""),
        "scenario_id": str(payload.get("scenario_id") or ""),
        "target_project_id": str(payload.get("target_project_id") or ""),
        "target_config": str(payload.get("target_config") or ""),
        "attempt": int(payload.get("attempt") or 1),
        "status": str(payload.get("status") or "running"),
        "health": "ok",
        "pid": payload.get("pid"),
        "live_state_dir": str(payload.get("live_state_dir") or ""),
        "artifact_dir": str(payload.get("artifact_dir") or ""),
        "started_at": event.ts if event else "",
        "heartbeat_at": "",
        "ended_at": "",
        "archived_at": "",
        "summary": {},
    }


def _merge_event_into_entry(entry: dict[str, Any], event: ZfEvent) -> None:
    payload = event.payload or {}
    for key in (
        "scenario_id",
        "target_project_id",
        "target_config",
        "live_state_dir",
        "artifact_dir",
        "command",
    ):
        if payload.get(key):
            entry[key] = str(payload[key])
    if event.correlation_id:
        entry["trace_id"] = event.correlation_id
    if event.task_id:
        entry["test_task_id"] = event.task_id
    if payload.get("pid") is not None:
        entry["pid"] = payload.get("pid")
    if event.type == "run.started":
        entry["status"] = "running"
        entry["started_at"] = entry.get("started_at") or event.ts
    elif event.type == "run.heartbeat":
        entry["heartbeat_at"] = event.ts
        entry["health"] = "ok"
    elif event.type == "run.stalled":
        entry["health"] = "stalled"
        entry["stalled_at"] = event.ts
    elif event.type in {"run.completed", "run.cancelled", "run.abandoned"}:
        status = str(payload.get("status") or "")
        if event.type == "run.cancelled":
            status = "cancelled"
        elif event.type == "run.abandoned":
            status = "abandoned"
        if status:
            try:
                status = normalize_run_status(status)
            except RunArchiveError:
                pass
            entry["status"] = status
        entry["ended_at"] = event.ts
        entry["exit_code"] = payload.get("exit_code")
        entry["validation_status"] = payload.get("validation_status")
    elif event.type == "run.archived":
        entry["archived_at"] = event.ts
        entry["artifact_manifest"] = str(payload.get("artifact_manifest") or "")


def _merge_metadata_into_entry(
    entry: dict[str, Any],
    metadata: dict[str, Any],
    manifest: dict[str, Any],
    run_dir: Path,
) -> None:
    for src_key, dst_key in (
        ("trace_id", "trace_id"),
        ("test_task_id", "test_task_id"),
        ("scenario_id", "scenario_id"),
        ("target_project_id", "target_project_id"),
        ("target_config", "target_config"),
        ("attempt", "attempt"),
        ("status", "status"),
        ("live_state_dir", "live_state_dir"),
        ("started_at", "started_at"),
        ("ended_at", "ended_at"),
        ("archived_at", "archived_at"),
    ):
        if metadata.get(src_key):
            entry[dst_key] = metadata[src_key]
    entry["artifact_dir"] = str(run_dir)
    entry["artifact_manifest"] = str(run_dir / "artifact_manifest.json") if manifest else ""
    if metadata.get("summary"):
        entry["summary"] = metadata["summary"]
    elif manifest.get("summary"):
        entry["summary"] = manifest["summary"]


def _pid_alive(pid_value: Any) -> bool:
    try:
        pid = int(pid_value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _heartbeat_stale(entry: dict[str, Any], now: datetime, stale_after_seconds: float) -> bool:
    value = str(entry.get("heartbeat_at") or entry.get("started_at") or "")
    parsed = _parse_ts(value)
    if parsed is None:
        return True
    return (now - parsed).total_seconds() > stale_after_seconds


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


__all__ = [
    "RUN_EVENT_TYPES",
    "RUN_STATUSES",
    "RunArchiveError",
    "RunArchiveResult",
    "RunMetadata",
    "RunProjectionResult",
    "RunProjector",
    "ReconcileResult",
    "archive_run",
    "normalize_run_status",
    "read_run_detail",
    "read_run_events",
    "read_task_runs",
    "reconcile_runs",
    "run_and_archive_command",
    "validate_run_id",
]
