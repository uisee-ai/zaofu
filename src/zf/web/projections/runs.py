"""Projections layer: runs (moved verbatim from web/server.py)."""
from __future__ import annotations

from fastapi import HTTPException
from pathlib import Path
from typing import Any
from zf.core.events.log import EventLog
from zf.core.security.redaction import redact_obj
from zf.runtime.run_archive import RunArchiveError
from zf.runtime.run_archive import RunProjector
from zf.runtime.run_archive import read_run_detail
from zf.runtime.run_archive import read_run_events
from zf.runtime.run_archive import validate_run_id
from zf.web.projections.common import _read_json_file
from zf.web.projections.summaries import _safe_run_dir
from zf.web.projections.events import _EVENT_LOG_RUN_ID, _event_log_run_summary, _event_to_dict, _events_with_seq


def _runtime_snapshots(
    state_dir: Path,
    *,
    project_root: Path | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    project_root = project_root or state_dir.parent
    try:
        from zf.web.projections.events import _events_with_seq

        events = [event for _, event in _events_with_seq(state_dir)]
    except Exception:
        events = []
    rows: list[dict[str, Any]] = []
    invalid = 0
    missing = 0
    latest_by_task: dict[str, dict[str, Any]] = {}
    for event in reversed(events):
        if not event.type.startswith("runtime.snapshot."):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == "runtime.snapshot.invalid":
            invalid += 1
        snapshot_ref = str(payload.get("snapshot_ref") or "")
        summary = {
            "event_id": event.id,
            "event_type": event.type,
            "task_id": event.task_id or str(payload.get("task_id") or ""),
            "source": str(payload.get("source") or ""),
            "snapshot_id": str(payload.get("snapshot_id") or ""),
            "snapshot_ref": snapshot_ref,
            "dispatch_id": str(payload.get("dispatch_id") or ""),
            "run_id": str(payload.get("run_id") or ""),
            "trace_id": str(payload.get("trace_id") or ""),
            "role": str(payload.get("role") or ""),
            "instance_id": str(payload.get("instance_id") or ""),
            "created_at": event.ts,
            "status": "ok" if event.type == "runtime.snapshot.recorded" else "invalid",
            "refs_count": 0,
        }
        if snapshot_ref:
            try:
                from zf.runtime.runtime_snapshot import (
                    read_runtime_snapshot,
                    resolve_snapshot_ref,
                )

                path = resolve_snapshot_ref(
                    state_dir,
                    snapshot_ref,
                    project_root=project_root,
                )
                snapshot = read_runtime_snapshot(path)
                refs = snapshot.get("refs") if isinstance(snapshot, dict) else {}
                output = snapshot.get("output_contract") if isinstance(snapshot, dict) else {}
                summary["refs_count"] = len(refs) if isinstance(refs, dict) else 0
                summary["expected_event"] = (
                    str(output.get("expected_event") or "")
                    if isinstance(output, dict)
                    else ""
                )
            except Exception:
                missing += 1
                summary["status"] = "missing"
        rows.append(summary)
        task_id = str(summary.get("task_id") or "")
        if task_id and task_id not in latest_by_task:
            latest_by_task[task_id] = summary
        if len(rows) >= limit:
            break
    return {
        "schema_version": "runtime-snapshot-projection.v1",
        "summary": {
            "total_seen": sum(1 for e in events if e.type.startswith("runtime.snapshot.")),
            "returned": len(rows),
            "invalid": invalid,
            "missing": missing,
            "tasks": len(latest_by_task),
        },
        "latest_by_task": latest_by_task,
        "snapshots": rows,
    }


def _run_projector(state_dir: Path, *, project_root: Path) -> RunProjector:
    return RunProjector(project_root=project_root, state_dir=state_dir)


def _runs_index(state_dir: Path, *, project_root: Path) -> dict:
    try:
        data = _run_projector(state_dir, project_root=project_root).load_index()
        if not data.get("runs"):
            fallback = _event_log_run_summary(state_dir)
            if fallback is not None:
                data = dict(data)
                data["runs"] = [fallback]
        return redact_obj(data)
    except Exception as exc:
        return {
            "version": 1,
            "runs": [],
            "degraded": True,
            "error": str(exc),
        }


def _active_runs(state_dir: Path, *, project_root: Path) -> dict:
    try:
        return redact_obj(_run_projector(state_dir, project_root=project_root).load_active())
    except Exception as exc:
        return {
            "version": 1,
            "active_runs": [],
            "degraded": True,
            "error": str(exc),
        }


def _run_detail(state_dir: Path, run_id: str) -> dict:
    if run_id == _EVENT_LOG_RUN_ID:
        summary = _event_log_run_summary(state_dir)
        if summary is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        events = [_event_to_dict(seq, event) for seq, event in _events_with_seq(state_dir)]
        return redact_obj({
            "run_id": run_id,
            "run": summary,
            "manifest": {
                "source": "events.jsonl",
                "summary": summary.get("summary", {}),
            },
            "artifact_dir": "",
            "artifacts": [],
            "events": events[-200:],
            "empty": False,
        })
    try:
        detail = read_run_detail(state_dir=state_dir, run_id=run_id)
    except RunArchiveError as exc:
        raise HTTPException(400, str(exc)) from exc
    if detail.get("empty"):
        raise HTTPException(404, f"run {run_id!r} not found")
    return redact_obj(detail)


def _run_events(state_dir: Path, run_id: str) -> dict:
    if run_id == _EVENT_LOG_RUN_ID:
        return {
            "run_id": run_id,
            "items": [_event_to_dict(seq, event) for seq, event in _events_with_seq(state_dir)],
        }
    try:
        return {
            "run_id": validate_run_id(run_id),
            "items": read_run_events(state_dir=state_dir, run_id=run_id),
        }
    except RunArchiveError as exc:
        raise HTTPException(400, str(exc)) from exc


def _run_scorecard(state_dir: Path, run_id: str) -> dict:
    run_dir = _safe_run_dir(state_dir, run_id)
    path = run_dir / "scorecard.json"
    if not path.exists():
        raise HTTPException(404, f"scorecard for run {run_id!r} not found")
    return redact_obj(_read_json_file(path))


def _run_fanouts(state_dir: Path, run_id: str) -> dict:
    run_dir = _safe_run_dir(state_dir, run_id)
    fanouts_dir = run_dir / "fanouts"
    manifests = []
    if fanouts_dir.exists():
        for path in sorted(fanouts_dir.glob("*/manifest.json")):
            data = _read_json_file(path)
            if data:
                manifests.append(redact_obj(data))
        for path in sorted(fanouts_dir.glob("*.json")):
            data = _read_json_file(path)
            if data:
                manifests.append(redact_obj(data))
    return {"run_id": validate_run_id(run_id), "fanouts": manifests}


def _runtime_instance_retired(meta: dict) -> bool:
    return str(meta.get("status") or "") in {"retired", "stopped"} or bool(
        meta.get("retired_at")
    )
