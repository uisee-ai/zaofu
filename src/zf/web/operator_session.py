"""PTY-backed Kanban operator session for the Web workbench.

This module owns only ephemeral terminal process state. Durable ZaoFu truth
still belongs to the event log, kanban store, and session projections.
"""

from __future__ import annotations

import os
import pty
import json
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import fcntl
import struct
import termios
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Callable

from zf.web.operator_contract import (
    KANBAN_AGENT_ALLOWED_ACTIONS,
    KANBAN_AGENT_FORBIDDEN_CAPABILITIES,
    empty_skills_available,
    kanban_agent_boundary,
    kanban_agent_evidence_model,
    kanban_agent_shared_context,
    kanban_agent_status_model,
)


@dataclass(frozen=True)
class OperatorChunk:
    seq: int
    ts: str
    stream: str
    text: str


@dataclass(frozen=True)
class OperatorStartResult:
    ok: bool
    status: str
    reason: str
    session: dict[str, Any]


class OperatorSessionManager:
    """Manage the single kanban-agent operator terminal for one project."""

    def __init__(self, *, state_dir: Path, project_root: Path) -> None:
        self.state_dir = Path(state_dir)
        self.project_root = Path(project_root)
        self.operator_dir = self.state_dir / "operator"
        self.workdir = self.operator_dir / "workdir"
        self.transcript_path = self.operator_dir / "kanban-agent.log"
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._master_fd: int | None = None
        self._reader: threading.Thread | None = None
        self._chunks: list[OperatorChunk] = []
        self._raw_chunks: list[tuple[int, bytes]] = []
        self._raw_listeners: dict[int, Callable[[bytes], None]] = {}
        self._next_listener_id = 0
        self._seq = 0
        self._session: dict[str, Any] = {}
        self._skills_available: dict[str, Any] = empty_skills_available()

    def start(
        self,
        *,
        backend: str,
        scope: str = "project",
        task_id: str = "",
        force: bool = False,
        cols: int = 120,
        rows: int = 30,
        skills_available: dict[str, Any] | None = None,
    ) -> OperatorStartResult:
        backend = backend or "deterministic"
        context_task_id = str(task_id or "").strip()
        scope = "project"
        task_id = ""
        with self._lock:
            self._skills_available = (
                dict(skills_available)
                if isinstance(skills_available, dict)
                else empty_skills_available()
            )
            session_id = self._session_id(
                backend=backend,
                scope=scope,
                task_id=task_id,
            )
            current_session_id = str(self._session.get("session_id") or "")
            same_live_session = self.is_alive() and current_session_id == session_id
            if same_live_session and not force:
                self._session["status"] = "active"
                self._session["last_rebind_at"] = _now()
                self._session["scope"] = scope
                self._session["task_id"] = task_id
                self._session["context_task_id"] = context_task_id
                descriptor = dict(self._session.get("descriptor") or {})
                descriptor["scope"] = scope
                descriptor["task_id"] = task_id
                descriptor["context_task_id"] = context_task_id
                descriptor["backend"] = backend
                descriptor["project"] = self._project_key()
                self._session["descriptor"] = descriptor
                self._write_profile(
                    backend=backend,
                    scope=scope,
                    task_id=task_id,
                    context_task_id=context_task_id,
                )
                return OperatorStartResult(
                    ok=True,
                    status="rebound",
                    reason="operator session already running; rebound to live PTY",
                    session=self.status(),
                )
            if self.is_alive() and force:
                self.stop(reason="restart requested", announce=False)
            elif self.is_alive() and current_session_id != session_id:
                self.stop(
                    reason=f"session descriptor changed: {session_id}",
                    announce=False,
                )

            self.operator_dir.mkdir(parents=True, exist_ok=True)
            self._activate_session_paths(
                session_id=session_id,
                scope=scope,
                task_id=task_id,
                backend=backend,
            )
            self._chunks = []
            self._raw_chunks = []
            self._seq = 0
            self.workdir.mkdir(parents=True, exist_ok=True)
            self._write_profile(
                backend=backend,
                scope=scope,
                task_id=task_id,
                context_task_id=context_task_id,
            )

            command = self._command_for_backend(backend)
            if not command:
                session = self._base_session(
                    session_id=session_id,
                    backend=backend,
                    scope=scope,
                    task_id=task_id,
                    context_task_id=context_task_id,
                    status="failed",
                    command=[],
                )
                session["reason"] = f"operator backend {backend!r} command not found"
                self._session = session
                self._append("system", session["reason"] + "\n")
                return OperatorStartResult(
                    ok=False,
                    status="failed",
                    reason=session["reason"],
                    session=self.status(),
                )

            try:
                master_fd, slave_fd = pty.openpty()
                _set_winsize(slave_fd, cols=cols, rows=rows)
                env = self._process_env(
                    backend=backend,
                    scope=scope,
                    task_id=task_id,
                    context_task_id=context_task_id,
                )
                process = subprocess.Popen(
                    command,
                    cwd=self.workdir,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    env=env,
                    close_fds=True,
                    start_new_session=True,
                )
                os.close(slave_fd)
            except Exception as exc:
                session = self._base_session(
                    session_id=session_id,
                    backend=backend,
                    scope=scope,
                    task_id=task_id,
                    context_task_id=context_task_id,
                    status="failed",
                    command=command,
                )
                session["reason"] = str(exc)
                self._session = session
                self._append("system", f"operator start failed: {exc}\n")
                return OperatorStartResult(
                    ok=False,
                    status="failed",
                    reason=str(exc),
                    session=self.status(),
                )

            self._process = process
            self._master_fd = master_fd
            self._session = self._base_session(
                session_id=session_id,
                backend=backend,
                scope=scope,
                task_id=task_id,
                context_task_id=context_task_id,
                status="active",
                command=command,
            )
            self._session["pid"] = process.pid
            self._session["cols"] = cols
            self._session["rows"] = rows
            self._append(
                "system",
                f"operator session started backend={backend} pid={process.pid}\n",
            )
            self._reader = threading.Thread(
                target=self._read_loop,
                name="zf-kanban-operator-pty",
                daemon=True,
            )
            self._reader.start()
            return OperatorStartResult(
                ok=True,
                status="started",
                reason="operator PTY session started",
                session=self.status(),
            )

    def attach_raw_output(self, callback: Callable[[bytes], None]) -> Callable[[], None]:
        with self._lock:
            self._next_listener_id += 1
            listener_id = self._next_listener_id
            self._raw_listeners[listener_id] = callback

        def detach() -> None:
            with self._lock:
                self._raw_listeners.pop(listener_id, None)

        return detach

    def raw_output_since(self, cursor: int = 0, limit: int = 400) -> list[bytes]:
        with self._lock:
            chunks = [data for seq, data in self._raw_chunks if seq > cursor]
            if limit > 0:
                chunks = chunks[-limit:]
            return chunks

    def write_raw(self, data: bytes) -> dict[str, Any]:
        with self._lock:
            if not self.is_alive() or self._master_fd is None:
                return {
                    "ok": False,
                    "status": "not_running",
                    "reason": "operator session is not running",
                    # FIX-8(bizsim r4 F8):给 operator 指条活路——外部触发
                    # workflow 不依赖 operator 会话在跑。
                    "hint": (
                        "无 operator 会话时可用 `zf emit user.message "
                        "--payload-file <json>` 直接触发 workflow 外部事件"
                    ),
                }
            try:
                os.write(self._master_fd, data)
            except OSError as exc:
                return {"ok": False, "status": "write_failed", "reason": str(exc)}
            self._session["last_input_at"] = _now()
            return {
                "ok": True,
                "status": "submitted",
                "reason": "operator raw input submitted",
                "bytes": len(data),
            }

    def write(self, text: str) -> dict[str, Any]:
        with self._lock:
            if not self.is_alive() or self._master_fd is None:
                return {
                    "ok": False,
                    "status": "not_running",
                    "reason": "operator session is not running",
                    # FIX-8(bizsim r4 F8):给 operator 指条活路——外部触发
                    # workflow 不依赖 operator 会话在跑。
                    "hint": (
                        "无 operator 会话时可用 `zf emit user.message "
                        "--payload-file <json>` 直接触发 workflow 外部事件"
                    ),
                }
            payload = text if text.endswith("\n") else text + "\n"
            try:
                os.write(self._master_fd, payload.encode("utf-8", errors="replace"))
            except OSError as exc:
                return {"ok": False, "status": "write_failed", "reason": str(exc)}
            self._session["last_input_at"] = _now()
            return {
                "ok": True,
                "status": "submitted",
                "reason": "operator input submitted",
                "bytes": len(payload.encode("utf-8", errors="replace")),
            }

    def resize(self, *, cols: int, rows: int) -> dict[str, Any]:
        cols = max(1, min(int(cols), 1000))
        rows = max(1, min(int(rows), 1000))
        with self._lock:
            if self._master_fd is None:
                return {
                    "ok": False,
                    "status": "not_running",
                    "reason": "operator session is not running",
                    # FIX-8(bizsim r4 F8):给 operator 指条活路——外部触发
                    # workflow 不依赖 operator 会话在跑。
                    "hint": (
                        "无 operator 会话时可用 `zf emit user.message "
                        "--payload-file <json>` 直接触发 workflow 外部事件"
                    ),
                }
            try:
                _set_winsize(self._master_fd, cols=cols, rows=rows)
            except OSError as exc:
                return {"ok": False, "status": "resize_failed", "reason": str(exc)}
            self._session["cols"] = cols
            self._session["rows"] = rows
            self._session["last_resize_at"] = _now()
            return {
                "ok": True,
                "status": "resized",
                "reason": "operator terminal resized",
                "cols": cols,
                "rows": rows,
            }

    def append_system(self, text: str) -> None:
        payload = text if text.endswith("\n") else text + "\n"
        self._append("system", payload)

    def output_since(self, cursor: int = 0, limit: int = 200) -> dict[str, Any]:
        with self._lock:
            chunks = [chunk for chunk in self._chunks if chunk.seq > cursor]
            if limit > 0:
                chunks = chunks[-limit:]
            return {
                "session": self.status(),
                "cursor": cursor,
                "next_cursor": self._seq,
                "chunks": [chunk.__dict__ for chunk in chunks],
            }

    def stop(
        self,
        *,
        reason: str = "stop requested",
        announce: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            process = self._process
            master_fd = self._master_fd
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except Exception:
                    try:
                        process.terminate()
                    except Exception:
                        pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except Exception:
                        process.kill()
                    process.wait(timeout=3)
            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
            self._process = None
            self._master_fd = None
            self._session["status"] = "stopped"
            self._session["stopped_at"] = _now()
            self._session["reason"] = reason
            if announce:
                self._append("system", f"operator session stopped: {reason}\n")
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            session = dict(self._session)
            if not session:
                session = self._base_session(
                    session_id=self._session_id(
                        backend="deterministic",
                        scope="project",
                        task_id="",
                    ),
                    backend="deterministic",
                    scope="project",
                    task_id="",
                    context_task_id="",
                    status="idle",
                    command=[],
                )
            process = self._process
            alive = process is not None and process.poll() is None
            if process is not None and not alive and session.get("status") == "active":
                session["status"] = "exited"
                session["exit_code"] = process.returncode
            session["alive"] = alive
            session["output_seq"] = self._seq
            session["transcript_path"] = str(self.transcript_path)
            session["workdir"] = str(self.workdir)
            session.update(self._contract_projection())
            return session

    def is_alive(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def _read_loop(self) -> None:
        fd = self._master_fd
        if fd is None:
            return
        while True:
            with self._lock:
                process = self._process
                live = process is not None and process.poll() is None
            try:
                data = os.read(fd, 4096)
            except OSError:
                break
            if not data:
                if not live:
                    break
                time.sleep(0.05)
                continue
            self._append_bytes("pty", data)
        with self._lock:
            process = self._process
            if process is not None:
                self._session["exit_code"] = process.poll()
            if self._session.get("status") == "active":
                self._session["status"] = "exited"
                self._session["ended_at"] = _now()

    def _append(self, stream: str, text: str) -> None:
        self._append_bytes(stream, text.encode("utf-8", errors="replace"))

    def _append_bytes(self, stream: str, data: bytes) -> None:
        if not data:
            return
        self.operator_dir.mkdir(parents=True, exist_ok=True)
        text = data.decode("utf-8", errors="replace")
        with self._lock:
            self._seq += 1
            chunk = OperatorChunk(seq=self._seq, ts=_now(), stream=stream, text=text)
            self._chunks.append(chunk)
            self._raw_chunks.append((chunk.seq, data))
            if len(self._chunks) > 1200:
                self._chunks = self._chunks[-900:]
            if len(self._raw_chunks) > 1200:
                self._raw_chunks = self._raw_chunks[-900:]
            with self.transcript_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"{chunk.ts} {chunk.stream} {chunk.seq} "
                    f"{chunk.text.rstrip()}\n"
                )
            listeners = list(self._raw_listeners.values())
        for listener in listeners:
            try:
                listener(data)
            except Exception:
                pass

    def _command_for_backend(self, backend: str) -> list[str]:
        override = os.environ.get("ZF_KANBAN_AGENT_COMMAND", "").strip()
        if override:
            return shlex.split(override)
        if backend == "deterministic":
            return [
                sys.executable,
                "-u",
                "-c",
                (
                    "import sys\n"
                    "print('ZaoFu Kanban Agent deterministic operator ready', flush=True)\n"
                    "print('Allowed: read projections; submit controlled /api/actions', flush=True)\n"
                    "print('Forbidden: direct .zf writes; git mutation; role terminal control', flush=True)\n"
                    "for line in sys.stdin:\n"
                    "    text=line.rstrip('\\n')\n"
                    "    if text.strip().lower() in {'exit','quit'}:\n"
                    "        print('operator exit requested', flush=True)\n"
                    "        break\n"
                    "    print('operator> ' + text, flush=True)\n"
                ),
            ]
        if backend in {"codex", "codex-headless"}:
            command = (
                os.environ.get("ZF_KANBAN_AGENT_CODEX_HEADLESS_CMD", "")
                if backend == "codex-headless"
                else ""
            ).strip() or os.environ.get("ZF_KANBAN_AGENT_CODEX_CMD", "codex").strip()
            binary = shlex.split(command)[0] if command else "codex"
            if not shutil.which(binary):
                return []
            return shlex.split(command)
        if backend in {"claude", "claude-code", "claude-headless"}:
            command = (
                os.environ.get("ZF_KANBAN_AGENT_CLAUDE_HEADLESS_CMD", "")
                if backend == "claude-headless"
                else ""
            ).strip() or os.environ.get("ZF_KANBAN_AGENT_CLAUDE_CMD", "claude").strip()
            binary = shlex.split(command)[0] if command else "claude"
            if not shutil.which(binary):
                return []
            return shlex.split(command)
        return []

    def _process_env(
        self,
        *,
        backend: str,
        scope: str,
        task_id: str,
        context_task_id: str,
    ) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "ZF_KANBAN_AGENT": "1",
            "ZF_KANBAN_AGENT_BACKEND": backend,
            "ZF_KANBAN_AGENT_SCOPE": scope,
            "ZF_KANBAN_AGENT_TASK_ID": context_task_id,
            "ZF_KANBAN_AGENT_CONTEXT_TASK_ID": context_task_id,
            "ZF_PROJECT_ROOT": str(self.project_root),
            "ZF_STATE_DIR": str(self.state_dir),
            "ZF_OPERATOR_PROFILE": str(self.workdir / "AGENTS.md"),
            "ZF_OPERATOR_CLAUDE_PROFILE": str(self.workdir / "CLAUDE.md"),
            "TERM": env.get("TERM") or "xterm-256color",
            "COLORTERM": env.get("COLORTERM") or "truecolor",
        })
        return env

    def _base_session(
        self,
        *,
        session_id: str,
        backend: str,
        scope: str,
        task_id: str,
        context_task_id: str,
        status: str,
        command: list[str],
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "backend": backend,
            "status": status,
            "delivery": "terminal_and_actions",
            "profile": "operator",
            "scope": scope,
            "task_id": task_id,
            "context_task_id": context_task_id,
            "descriptor": {
                "project": self._project_key(),
                "scope": scope,
                "task_id": task_id,
                "context_task_id": context_task_id,
                "backend": backend,
            },
            "started_at": _now() if status == "active" else "",
            "command": command[:1] + (["..."] if len(command) > 1 else []),
            "terminal_backed": True,
            "workdir": str(self.workdir),
            "operator_workdir": str(self.workdir),
            "shared_project_workdir": str(self.project_root),
            "state_dir": str(self.state_dir),
            "transcript_path": str(self.transcript_path),
            "cols": 120,
            "rows": 30,
            **self._contract_projection(),
        }

    def _project_key(self) -> str:
        return sha1(str(self.state_dir.resolve()).encode("utf-8")).hexdigest()[:12]

    def _session_id(self, *, backend: str, scope: str, task_id: str) -> str:
        clean_backend = _safe_segment(backend or "deterministic")
        return f"kanban-agent:{self._project_key()}:project:{clean_backend}"

    def _activate_session_paths(
        self,
        *,
        session_id: str,
        scope: str,
        task_id: str,
        backend: str,
    ) -> None:
        session_dir = (
            self.operator_dir
            / "sessions"
            / _safe_segment(session_id)
        )
        self.workdir = session_dir / "workdir"
        self.transcript_path = session_dir / "kanban-agent.log"

    def _contract_projection(self) -> dict[str, Any]:
        return {
            "shared_context": kanban_agent_shared_context(
                project_root=self.project_root,
                state_dir=self.state_dir,
                operator_workdir=self.workdir,
            ),
            "skills_available": self._skills_available,
            "allowed_actions": list(KANBAN_AGENT_ALLOWED_ACTIONS),
            "forbidden_capabilities": list(KANBAN_AGENT_FORBIDDEN_CAPABILITIES),
            "boundary": kanban_agent_boundary(),
            "status_model": kanban_agent_status_model(),
            "evidence_model": kanban_agent_evidence_model(),
        }

    def _write_profile(
        self,
        *,
        backend: str,
        scope: str,
        task_id: str,
        context_task_id: str = "",
    ) -> None:
        relative_root = os.path.relpath(self.project_root, self.workdir)
        contract = self._contract_projection()
        shared_context = contract["shared_context"]
        skills = contract["skills_available"]
        skill_names = [str(name) for name in skills.get("names", [])][:24]
        skills_line = ", ".join(skill_names) if skill_names else "none projected"
        profile = f"""# ZaoFu Kanban Operator Agent

You are the Kanban/project management agent for this ZaoFu project.
You are NOT a coding agent in this session.
You are an operator/action requester, not the orchestrator scheduler.

Project root: `{self.project_root}`
State dir: `{self.state_dir}`
Operator workdir: `{self.workdir}`
Relative project root: `{relative_root}`
Backend: `{backend}`
Scope: `{scope}`
Session task: `{task_id or "none"}`
Context task: `{context_task_id or "none"}`
Shared skills: `{skills_line}`

Runtime boundary:
- This operator shares the active ZaoFu runtime through the same project root and state dir.
- It shares project root, project.state_dir, runtime projections, and the skills catalog with the orchestrator for context.
- Treat `events.jsonl`, `kanban.json`, `session.yaml`, `role_sessions.yaml`, traces, fanouts, workdirs, skills, and runs as projections/truth owned by ZaoFu services.
- Canonical task/card status comes from TaskStore/EventWriter accepted actions, not terminal output.
- Run completion, operator exit, or backend completion is evidence only; it never marks a task done by itself.
- Do not attach to or send keys into the orchestrator, dev, review, test, judge, or other role terminals.
- Board/project state changes must go through `zf` CLI commands or the controlled Web operator helper.

Shared context files in this workdir:
- `PROJECT_ROOT`: absolute project root.
- `STATE_DIR`: absolute runtime state dir.
- `SHARED_CONTEXT.json`: machine-readable shared context and boundary.
- `SKILLS.md`: projected skill catalog summary.

Allowed:
- Read projections from `/api/snapshot`, `/api/tasks/:id`, `/api/fanouts/:id`, `zf status`, `zf kanban list`, and `zf events`.
- Explain orchestrator, star/DAG/fanout, task, run, trace, and agent state from projections.
- Submit controlled actions through the Web operator helper by sending `/action ACTION_NAME {{"payload":"json"}}`.
- Create, move, archive, split, or start tasks only through controlled actions or equivalent `zf` CLI commands.
- Link run/trace/fanout/workdir/transcript evidence without treating the evidence as status truth.
- Request DAG/star/fanout/collaboration; the orchestrator runtime accepts, rejects, dispatches, and schedules.
- Ask the user before destructive or broad actions; only use controlled actions for project truth changes.

Forbidden:
- Do not write `.zf/events.jsonl`, `.zf/kanban.json`, or `.zf/session.yaml` directly.
- Do not edit product source files, tests, docs, or generated artifacts as this operator.
- Do not run `git commit`, `git merge`, `git cherry-pick`, `git push`, or mutate worktrees.
- Do not attach to or control role terminals or tmux panes.
- Do not directly mutate task status outside controlled ZaoFu actions.
- Do not directly dispatch role agents. Request orchestration/fanout instead.
- Do not treat chat or PTY transcript text as durable business truth.

Useful controlled action examples:
- `/action create-task {{"title":"Investigate failing docker e2e"}}`
- `/action update-task {{"task_id":"TASK-001","status":"in_progress"}}`
- `/action archive-task {{"task_id":"TASK-001","status":"done"}}`
- `/action link-evidence {{"task_id":"TASK-001","run_id":"RUN-001","trace_id":"trace-001"}}`
- `/action request-fanout {{"stage_id":"verify","reason":"parallel browser coverage"}}`
- `/action start-collaboration {{"intent":"implement the accepted Kanban Agent evidence split"}}`

Durable business truth belongs in ZaoFu event/action contracts, not in this terminal transcript.
"""
        shared_context_payload = {
            **shared_context,
            "allowed_actions": list(KANBAN_AGENT_ALLOWED_ACTIONS),
            "forbidden_capabilities": list(KANBAN_AGENT_FORBIDDEN_CAPABILITIES),
            "boundary": kanban_agent_boundary(),
            "status_model": kanban_agent_status_model(),
            "evidence_model": kanban_agent_evidence_model(),
            "skills_available": skills,
        }
        skills_doc = "\n".join([
            "# ZaoFu Kanban Agent Skills Context",
            "",
            f"Pool path: `{skills.get('pool_path') or ''}`",
            f"Pool count: `{skills.get('pool_count', 0)}`",
            f"Enabled role count: `{skills.get('enabled_role_count', 0)}`",
            f"Names: `{skills_line}`",
            "",
            "Skills are shared context and planning vocabulary only.",
            "They do not grant direct role execution, dispatch, git, or truth-write authority.",
            "",
        ])
        (self.workdir / "AGENTS.md").write_text(profile, encoding="utf-8")
        (self.workdir / "CLAUDE.md").write_text(profile, encoding="utf-8")
        (self.workdir / "PROJECT_ROOT").write_text(str(self.project_root) + "\n", encoding="utf-8")
        (self.workdir / "STATE_DIR").write_text(str(self.state_dir) + "\n", encoding="utf-8")
        (self.workdir / "SHARED_CONTEXT.json").write_text(
            json.dumps(shared_context_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.workdir / "SKILLS.md").write_text(skills_doc, encoding="utf-8")


def _safe_segment(value: str) -> str:
    safe = []
    for char in str(value or ""):
        if char.isalnum() or char in {"-", "_", "."}:
            safe.append(char)
        else:
            safe.append("-")
    result = "".join(safe).strip("-")
    return result or "unknown"


def _set_winsize(fd: int, *, cols: int, rows: int) -> None:
    fcntl.ioctl(
        fd,
        termios.TIOCSWINSZ,
        struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0),
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
