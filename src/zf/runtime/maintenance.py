"""Runtime maintenance projections for supervised self-repair."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.state.atomic_io import atomic_write_text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class MaintenanceCheckpoint:
    checkpoint_id: str
    task_id: str
    role: str = ""
    session_id: str = ""
    tmux_session: str = ""
    pane_id: str = ""
    last_event_id: str = ""
    last_progress: str = ""
    current_stage: str = ""
    assigned_worker: str = ""
    briefing_digest: str = ""
    git_head: str = ""
    git_status: str = ""
    dirty_diff_artifact: str = ""
    transcript_path: str = ""
    resume_packet_path: str = ""
    reason: str = "suspended_for_zaofu_self_repair"
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def maintenance_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "autoresearch" / "maintenance"


def read_current_maintenance(state_dir: Path) -> dict[str, Any]:
    path = maintenance_dir(Path(state_dir)) / "current.yaml"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def self_repair_active(state_dir: Path) -> bool:
    current = read_current_maintenance(state_dir)
    return (
        str(current.get("status") or "").strip().lower() == "entered"
        and bool(current.get("dispatch_paused"))
    )


def _event_writer(state_dir: Path) -> EventWriter:
    return EventWriter(EventLog(Path(state_dir) / "events.jsonl"))


def _git_output(project_root: Path, args: list[str], timeout: int = 10) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _last_event_id(state_dir: Path, task_id: str) -> str:
    try:
        events = EventLog(Path(state_dir) / "events.jsonl").read_all()
    except Exception:
        return ""
    for event in reversed(events):
        if event.task_id == task_id:
            return event.id
    return events[-1].id if events else ""


def enter_maintenance(
    state_dir: Path,
    *,
    trigger_id: str,
    reason: str,
    emit_events: bool = True,
) -> Path:
    state_dir = Path(state_dir)
    current = {
        "status": "entered",
        "trigger_id": trigger_id,
        "reason": reason,
        "entered_at": _now_iso(),
        "dispatch_paused": True,
    }
    path = maintenance_dir(state_dir) / "current.yaml"
    atomic_write_text(path, json.dumps(current, ensure_ascii=False, indent=2) + "\n")
    if emit_events:
        writer = _event_writer(state_dir)
        writer.append(ZfEvent(
            type="runtime.maintenance.entered",
            actor="zf-autoresearch",
            payload=current,
        ))
        writer.append(ZfEvent(
            type="dispatch.paused",
            actor="zf-autoresearch",
            payload={
                "reason": reason,
                "trigger_id": trigger_id,
                "maintenance_current": str(path),
            },
        ))
    return path


def exit_maintenance(
    state_dir: Path,
    *,
    repair_run_id: str,
    validation_summary: str,
    emit_events: bool = True,
) -> Path:
    state_dir = Path(state_dir)
    current = {
        "status": "exited",
        "repair_run_id": repair_run_id,
        "validation_summary": validation_summary,
        "exited_at": _now_iso(),
        "dispatch_paused": False,
    }
    path = maintenance_dir(state_dir) / "current.yaml"
    atomic_write_text(path, json.dumps(current, ensure_ascii=False, indent=2) + "\n")
    if emit_events:
        writer = _event_writer(state_dir)
        writer.append(ZfEvent(
            type="runtime.maintenance.exited",
            actor="zf-autoresearch",
            payload=current,
        ))
        writer.append(ZfEvent(
            type="dispatch.resumed",
            actor="zf-autoresearch",
            payload={
                "repair_run_id": repair_run_id,
                "maintenance_current": str(path),
            },
        ))
    return path


def create_checkpoint(
    state_dir: Path,
    *,
    task_id: str,
    project_root: Path | None = None,
    role: str = "",
    assigned_worker: str = "",
    session_id: str = "",
    tmux_session: str = "",
    pane_id: str = "",
    last_progress: str = "",
    current_stage: str = "",
    transcript_path: str = "",
    emit_events: bool = True,
) -> MaintenanceCheckpoint:
    state_dir = Path(state_dir)
    root = Path(project_root) if project_root is not None else state_dir.parent
    checkpoint_id = f"ckpt-{task_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    ckpt_dir = maintenance_dir(state_dir) / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    diff_path = ckpt_dir / f"{checkpoint_id}.diff"
    diff = _git_output(root, ["diff", "--no-color"], timeout=30)
    atomic_write_text(diff_path, diff + ("\n" if diff and not diff.endswith("\n") else ""))
    resume_path = ckpt_dir / f"{checkpoint_id}.resume.json"
    checkpoint = MaintenanceCheckpoint(
        checkpoint_id=checkpoint_id,
        task_id=task_id,
        role=role,
        session_id=session_id,
        tmux_session=tmux_session,
        pane_id=pane_id,
        last_event_id=_last_event_id(state_dir, task_id),
        last_progress=last_progress,
        current_stage=current_stage,
        assigned_worker=assigned_worker,
        git_head=_git_output(root, ["rev-parse", "HEAD"]),
        git_status=_git_output(root, ["status", "--short"]),
        dirty_diff_artifact=str(diff_path),
        transcript_path=transcript_path,
        resume_packet_path=str(resume_path),
    )
    atomic_write_text(
        resume_path,
        json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(
        ckpt_dir / f"{checkpoint_id}.json",
        json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2) + "\n",
    )
    if emit_events:
        _event_writer(state_dir).append(ZfEvent(
            type="worker.checkpointed",
            actor="zf-autoresearch",
            task_id=task_id,
            payload=checkpoint.to_dict(),
        ))
    return checkpoint


__all__ = [
    "MaintenanceCheckpoint",
    "maintenance_dir",
    "read_current_maintenance",
    "self_repair_active",
    "enter_maintenance",
    "exit_maintenance",
    "create_checkpoint",
]
