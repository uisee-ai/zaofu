"""Observe an owned experiment's durable wait and bound its controller process."""

from pathlib import Path
import subprocess
import time
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.events.log import EventLog
from zf.core.state.session import SessionStore, ZfNotInitialized


def child_wait(worktree: Path) -> dict:
    """Read canonical child evidence only; never answer its human gate."""
    if worktree.is_symlink() or not (worktree / "zf.yaml").is_file():
        return {}
    try:
        root = worktree.resolve()
        config = load_config(root / "zf.yaml")
        state = (root / config.project.state_dir).resolve()
        state.relative_to(root)
        session = SessionStore(state / "session.yaml").load()
        if session is None or Path(session.project_root).resolve() != root:
            return {}
        log = EventLog(state / "events.jsonl")
        try:
            events = log.read_all()
        finally:
            log.close()
        from zf.runtime.run_manager import _pending_human_decisions
        gates = [gate for gate in _pending_human_decisions(events)
                 if gate.get("blocking_scope", "run") not in {"task", "side", "release"}]
        if not gates:
            return {}
        return {
            "status": "waiting", "failure_class": "child_owner_wait",
            "reproduction_status": "incomplete",
            "reason": "child workflow requires an unresolved owner decision",
            "child_state_dir": str(state), "pending_decisions": gates,
            "evidence_paths": [str(state / "events.jsonl")],
        }
    except (OSError, ValueError, TypeError, ConfigError, ZfNotInitialized, yaml.YAMLError):
        return {}


def run_supervised(command, *, worktree: Path, timeout_s: int,
                   popen=subprocess.Popen, monotonic=time.monotonic) -> tuple:
    """Poll without holding canonical locks; cleanup has its own short bound."""
    deadline = monotonic() + timeout_s
    proc = popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    diagnosis = {}
    try:
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                diagnosis = {"status": "incomplete", "failure_class": "diagnosis_timeout",
                             "reason": "diagnosis work deadline exhausted", "reproduction_status": "incomplete"}
                break
            try:
                out, err = proc.communicate(timeout=min(5, remaining))
                return subprocess.CompletedProcess(command, proc.returncode, out, err), diagnosis
            except subprocess.TimeoutExpired:
                diagnosis = child_wait(worktree)
                if diagnosis:
                    break
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    # Descendants can inherit pipes; do not wait indefinitely for their EOF.
    try:
        out, err = proc.communicate(timeout=1)
    except subprocess.TimeoutExpired:
        out, err = "", "controller stopped; descendant pipe still open"
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            stream.close()
    return subprocess.CompletedProcess(command,
        75 if diagnosis.get("failure_class") == "child_owner_wait" else 124, out, err), diagnosis
