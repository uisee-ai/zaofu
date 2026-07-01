"""Shared process/worktree helpers for real-provider E2E smoke runners."""

from __future__ import annotations

import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC_ROOT)
    return env


def _run(
    cmd: list[str],
    *,
    cwd: Path = REPO_ROOT,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=_env(),
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        check=False,
    )


def _read_events(events_path: Path) -> list[dict]:
    if not events_path.exists():
        return []
    rows: list[dict] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _count_event(events_path: Path, event_type: str) -> int:
    return sum(
        1 for event in _read_events(events_path)
        if event.get("type") == event_type
    )


def _read_session_name(worktree: Path) -> str | None:
    try:
        data = yaml.safe_load((worktree / "zf.yaml").read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    value = (data.get("session") or {}).get("tmux_session")
    return value if isinstance(value, str) else None


def _pid_is_worktree_watcher(pid: int, worktree: Path) -> bool:
    proc = Path("/proc") / str(pid)
    try:
        raw_cmdline = (proc / "cmdline").read_bytes()
        cwd = (proc / "cwd").resolve()
    except OSError:
        return False
    cmdline = [
        part for part in raw_cmdline.decode(errors="replace").split("\0")
        if part
    ]
    if "start" not in cmdline or "--foreground" not in cmdline:
        return False
    try:
        return cwd == worktree.resolve()
    except OSError:
        return False


def _kill_worktree_watcher(worktree: Path) -> None:
    lock_path = worktree / ".zf" / "loop.lock"
    if not lock_path.exists():
        return
    try:
        pid = int(lock_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if pid == os.getpid() or not _pid_is_worktree_watcher(pid, worktree):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    for _ in range(20):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _kill_lingering(worktree: Path, session_name: str | None) -> None:
    _kill_worktree_watcher(worktree)
    if session_name:
        _run(["tmux", "kill-session", "-t", session_name], capture=True)
    time.sleep(1)


def _remove_existing_worktree(worktree: Path) -> None:
    if not worktree.exists():
        return
    session_name = _read_session_name(worktree)
    if (worktree / "zf.yaml").exists():
        _run(["zf", "stop"], cwd=worktree, capture=True)
    _kill_lingering(worktree, session_name)
    _run(["git", "worktree", "remove", "--force", str(worktree)], capture=True)
    if worktree.exists():
        shutil.rmtree(worktree, ignore_errors=True)
    _run(["git", "worktree", "prune"], capture=True)


def _rev_parse(ref: str, *, cwd: Path = REPO_ROOT) -> str | None:
    proc = _run(["git", "rev-parse", "--verify", ref], cwd=cwd, capture=True)
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip().splitlines()[-1]


def _candidate_base_ref() -> str:
    return "origin/dev" if _rev_parse("origin/dev") else "HEAD"


def _create_controlled_candidate_ref(worktree: Path, candidate_ref: str) -> None:
    base_ref = _candidate_base_ref()
    scratch = worktree.parent / f"{worktree.name}-candidate-src"
    _run(["git", "worktree", "remove", "--force", str(scratch)], capture=True)
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    proc = _run(
        ["git", "worktree", "add", "--detach", str(scratch), base_ref],
        capture=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout or f"git worktree add failed: {scratch}")
    try:
        doc = scratch / "docs" / "star-smoke-candidate.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "\n".join([
                "# Star Smoke Candidate",
                "",
                "This file is created by tests/e2e/run_star.py.",
                "It gives real-provider fanout_reader scenarios a small",
                "controlled candidate diff to review.",
                "",
            ]),
            encoding="utf-8",
        )
        proc = _run(["git", "add", "docs/star-smoke-candidate.md"], cwd=scratch, capture=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stdout or "git add failed")
        proc = _run(
            [
                "git",
                "-c",
                "user.name=ZaoFu E2E",
                "-c",
                "user.email=zaofu-e2e@example.invalid",
                "commit",
                "--allow-empty",
                "-m",
                "test: star smoke candidate",
            ],
            cwd=scratch,
            capture=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stdout or "git commit failed")
        commit = _rev_parse("HEAD", cwd=scratch)
        if not commit:
            raise RuntimeError("could not resolve smoke candidate commit")
        proc = _run(
            ["git", "branch", "-f", candidate_ref, commit],
            cwd=worktree,
            capture=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stdout or f"git branch -f {candidate_ref} failed")
    finally:
        _run(["git", "worktree", "remove", "--force", str(scratch)], capture=True)
        if scratch.exists():
            shutil.rmtree(scratch, ignore_errors=True)
        _run(["git", "worktree", "prune"], capture=True)


def _write_json(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _init_state(worktree: Path) -> None:
    state = worktree / ".zf"
    for child in ("artifacts", "briefings", "logs", "memory"):
        (state / child).mkdir(parents=True, exist_ok=True)
    (state / "events.jsonl").write_text("", encoding="utf-8")
    _write_json(state / "kanban.json", [])
    _write_json(state / "feature_list.json", [])
    (state / "session.yaml").write_text(
        'session_id: ""\nruntime_state: initialized\nlatest_event_offset: 0\n',
        encoding="utf-8",
    )


def start_harness(worktree: Path) -> int:
    # Current `zf start` runs the watcher in foreground by default.  The
    # real-provider smoke runner needs the harness process in the background so
    # it can emit the scenario trigger after loop.started.  Keep this helper as
    # a compatibility checkpoint for older runner call sites; start_watcher()
    # below is the single process that spawns workers and drives the loop.
    print(f"[start] deferred to background watcher in {worktree}")
    return 0


def start_watcher(worktree: Path) -> int:
    print("[watcher] zf start (background foreground-watcher)")
    log_path = worktree / ".zf" / "logs" / "watcher.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    events_path = worktree / ".zf" / "events.jsonl"
    before = _count_event(events_path, "loop.started")
    stdout = open(log_path, "wb")
    proc = subprocess.Popen(
        ["zf", "start"],
        cwd=worktree,
        stdout=stdout,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=_env(),
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if _count_event(events_path, "loop.started") > before:
            print(f"[watcher] ready (pid={proc.pid})")
            stdout.close()
            return proc.pid
        returncode = getattr(proc, "poll", lambda: None)()
        if returncode is not None:
            reason = _watcher_preflight_failure(log_path) or f"zf start exited early with code {returncode}"
            print(f"[watcher] ERROR: {reason}", file=sys.stderr)
            stdout.close()
            return -abs(int(returncode) or 1)
        reason = _watcher_preflight_failure(log_path)
        if reason:
            print(f"[watcher] ERROR: {reason}", file=sys.stderr)
            stdout.close()
            try:
                proc.terminate()
            except Exception:
                pass
            return -1
        time.sleep(2)
    print("[watcher] WARNING: did not see loop.started within 90s", file=sys.stderr)
    stdout.close()
    return proc.pid


def _watcher_preflight_failure(log_path: Path) -> str:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lowered = text.lower()
    if "workflow preflight failed" not in lowered:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    tail = "\n".join(lines[-12:])
    return f"workflow preflight failed before loop.started\n{tail}"


def stop_harness(worktree: Path, session_name: str | None) -> None:
    print("[stop] zf stop")
    _run(["zf", "stop"], cwd=worktree)
    _kill_lingering(worktree, session_name)
