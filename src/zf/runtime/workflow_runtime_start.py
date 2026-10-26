"""Start one project runtime after an authorized Workflow submission."""

from __future__ import annotations

import fcntl
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from zf.core.config.loader import load_config
from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.core.state.atomic_io import atomic_write_text
from zf.core.state.locks import locked_path
from zf.runtime.cli_command import default_zf_cli_cmd


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _process_identity(pid: int) -> str:
    if pid <= 1:
        return ""
    try:
        os.kill(pid, 0)
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists():
            fields = stat.read_text().rsplit(")", 1)[1].split()
            return "" if fields[0] == "Z" else fields[19]
        return str(pid)
    except (OSError, ValueError, IndexError):
        return ""


def _loop_owned(state_dir: Path) -> bool:
    try:
        with (state_dir / "loop.lock").open("r") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    return False


def _runtime_owner(state_dir: Path) -> dict[str, Any]:
    guard = _read(state_dir / "processes" / "watcher.pid.json")
    try:
        pid = int(guard.get("owner_pid") or 0)
    except (TypeError, ValueError):
        return {}
    return guard if _process_identity(pid) and _loop_owned(state_dir) else {}


def mark_runtime_ready(state_dir: Path) -> None:
    """Publish readiness for this watcher epoch, after startup catch-up."""
    guard = _read(state_dir / "processes" / "watcher.pid.json")
    if guard.get("owner_pid") != os.getpid() or not guard.get("epoch"):
        return
    atomic_write_text(
        state_dir / "processes" / "runtime-ready.json",
        json.dumps({"owner_pid": os.getpid(), "epoch": guard["epoch"]}) + "\n",
    )


def _ready_owner(state_dir: Path) -> int:
    guard = _runtime_owner(state_dir)
    if not guard:
        return 0
    ready = _read(state_dir / "processes" / "runtime-ready.json")
    if ready.get("owner_pid") == guard["owner_pid"] and ready.get("epoch") == guard.get("epoch"):
        return int(guard["owner_pid"])
    # A runtime started by an older installed version has no ready marker.
    # Its loop.started occurrence must belong to the current guard epoch.
    if not ready:
        from zf.core.events import EventLog

        for event in reversed(EventLog(state_dir / "events.jsonl").read_all()):
            if event.type == "loop.started" and event.ts >= str(guard.get("started_at") or "~"):
                return int(guard["owner_pid"])
    return 0


def ensure_project_runtime(
    *, project_root: Path, state_dir: Path, writer: EventWriter,
    causation_id: str = "", timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Reuse a live watcher or launch zf start, with bounded readiness waiting.

    The launch record is a process projection. The CLI retains ownership of
    loop.lock, worker startup, preflight, catch-up, and all runtime state.
    """
    project_root, state_dir = project_root.resolve(), state_dir.resolve()
    processes = state_dir / "processes"
    record_path = processes / "workflow-runtime-start.json"
    log_path = state_dir / "logs" / "workflow-runtime-start.log"
    child: subprocess.Popen | None = None
    launched = False
    pid = 0
    try:
        with locked_path(record_path, timeout_seconds=5.0):
            owner = _ready_owner(state_dir)
            if owner:
                return {"ok": True, "status": "running", "pid": owner, "launched": False}
            guard = _runtime_owner(state_dir)
            record = _read(record_path)
            saved_pid = int(record.get("pid") or 0)
            if guard:
                pid = int(guard["owner_pid"])
            elif record.get("process_identity") and _process_identity(saved_pid) == record["process_identity"]:
                pid = saved_pid
            else:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "ab") as output:
                    child = subprocess.Popen(
                        [*shlex.split(default_zf_cli_cmd()), "start"],
                        cwd=project_root, stdin=subprocess.DEVNULL,
                        stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                        env={**{key: value for key, value in os.environ.items() if key not in {
                            "ZF_WORKFLOW_ACTION_TOKEN", "ZF_WEB_ACTION_TOKEN", "ZF_WEB_PASSCODE",
                            "ZF_WEB_TRUSTED_SESSION", "ZF_TMUX_SESSION",
                        }}, "ZF_PROJECT_ROOT": str(project_root), "ZF_STATE_DIR": str(state_dir),
                            "ZF_CLI_CMD": default_zf_cli_cmd()},
                    )
                pid, launched = child.pid, True
                atomic_write_text(record_path, json.dumps({
                    "pid": pid, "process_identity": _process_identity(pid),
                    "project_root": str(project_root), "log_ref": str(log_path),
                }) + "\n")
                writer.emit("runtime.start.requested", actor="kernel", causation_id=causation_id or None,
                            payload={"pid": pid, "project_root": str(project_root), "log_ref": str(log_path)})
        deadline = time.monotonic() + timeout_seconds
        while True:
            owner = _ready_owner(state_dir)
            if owner:
                return {"ok": True, "status": "running", "pid": owner,
                        "launched": launched, "log_ref": str(log_path)}
            code = child.poll() if child is not None else None
            if code is not None or not _process_identity(pid):
                reason = f"project runtime exited before readiness (exit_code={code}); inspect {log_path}"
                status = "failed"
                break
            if time.monotonic() >= deadline:
                reason = f"project runtime is still starting; retry the same proposal and inspect {log_path}"
                status = "starting"
                break
            time.sleep(0.1)
    except (OSError, ValueError, TimeoutError) as exc:
        reason, status = f"could not start project runtime: {exc}", "failed"
    writer.emit("runtime.start.failed", actor="kernel", causation_id=causation_id or None,
                payload={"pid": pid, "status": status, "reason": reason,
                         "project_root": str(project_root), "log_ref": str(log_path)})
    return {"ok": False, "status": status, "pid": pid, "launched": launched,
            "reason": reason, "log_ref": str(log_path)}


def ensure_submitted_workflow_runtime(
    result: dict[str, Any], *, config_path: Path, config: ZfConfig | None = None,
) -> dict[str, Any]:
    """Keep accepted requests retryable when their runtime cannot start."""
    if result.get("status") == "STOP" or result.get("ok") is False or result.get("runtime_start"):
        return result
    config_path = config_path.resolve()
    config = config or load_config(config_path)
    state_dir = (config_path.parent / config.project.state_dir).resolve()
    writer = EventWriter(event_log_from_project(state_dir, config=config))
    try:
        runtime = ensure_project_runtime(
            project_root=config_path.parent, state_dir=state_dir, writer=writer,
            causation_id=str(result.get("workflow_invoke_event_id") or result.get("event_id") or ""),
        )
    finally:
        writer.event_log.close()
    if runtime["ok"]:
        return {**result, "runtime_start": runtime,
                "next_action": "project runtime is ready; follow Workflow dispatch and execution events"}
    blocker = {"severity": "STOP", "kind": "workflow_runtime_start_failed",
               "message": runtime["reason"], "log_ref": runtime["log_ref"]}
    return {**result, "ok": False, "status": "STOP", "_status_code": 503,
            "runtime_start": runtime, "reason": runtime["reason"],
            "next_action": "fix runtime startup and retry this proposal; the accepted request is preserved",
            "blockers": [*result.get("blockers", []), blocker]}
