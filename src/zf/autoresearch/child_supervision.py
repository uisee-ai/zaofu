"""Observe an owned experiment's durable wait and bound its controller process."""

from pathlib import Path
import subprocess
import time
import yaml

from zf.core.config.loader import ConfigError, load_config
from zf.core.events.factory import event_log_from_project
from zf.core.state.session import SessionStore, ZfNotInitialized


def child_wait(worktree: Path) -> dict:
    """Read the owned child's explicit wait/stop; never answer or reopen it."""
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
        log = event_log_from_project(state, config=config)
        try:
            events = log.read_all()
        finally:
            log.close()
        from zf.runtime.run_scope import event_run_id, events_for_run, run_aliases
        aliases = run_aliases(events)
        runs = {event_run_id(e, aliases=aliases) for e in events if e.type == "run.goal.started"}
        runs.discard("")
        if len(runs) > 1:
            return {}  # A shared workspace does not identify the owned child Run.
        run_id = next(iter(runs), "")
        if run_id:
            events = events_for_run(events, run_id=run_id, _aliases=aliases)
            stopped = _child_stop(events, run_id=run_id, state=state)
            if stopped:
                return stopped
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
            "child_workflow_run_id": run_id,
            "diagnosis_owner": "owner",
            "resume_condition": "answer the bound child decision through its authorized control path",
            "evidence_paths": [str(state / "events.jsonl")],
        }
    except (OSError, ValueError, TypeError, ConfigError, ZfNotInitialized, yaml.YAMLError):
        return {}


def _child_stop(events, *, run_id: str, state: Path) -> dict:
    transitions = {"run.goal.started", "run.goal.updated", "run.goal.blocked",
                   "run.goal.completed", "run.failed", "run.cancelled"}
    event = next((e for e in reversed(events) if e.type in transitions), None)
    if event is None or event.origin != "kernel" or event.type not in {
        "run.goal.blocked", "run.failed", "run.cancelled",
    }:
        return {}
    body = event.payload
    cause_id = body.get("origin_event_id") or event.causation_id
    cause = next((e for e in events if e.id == cause_id), None)
    budget = (body.get("failure_class") == "workflow_budget_exceeded"
              and cause is not None and cause.origin == "kernel"
              and cause.type == "workflow.budget.exceeded"
              and cause.payload.get("workflow_run_id") == run_id
              and cause.payload.get("exceeded_dimensions") == body.get("exceeded_dimensions"))
    checkpoint = next((e for e in reversed(events[:events.index(event) + 1])
                       if e.type == "worker.work_record.published"), None)
    return {
        "status": "incomplete", "reproduction_status": "incomplete",
        "failure_class": "child_budget_exceeded" if budget else "child_run_stopped",
        "reason": str(body.get("reason") or "child Run stopped without a completed diagnosis"),
        "child_state_dir": str(state), "child_workflow_run_id": run_id,
        "child_event_id": event.id, "child_event_type": event.type,
        "child_cause_event_id": cause.id if cause else "",
        "child_failure_class": str(body.get("failure_class") or ""),
        "exceeded_dimensions": list(body.get("exceeded_dimensions") or []) if budget else [],
        "checkpoint_event_id": checkpoint.id if checkpoint else "",
        "diagnosis_owner": "run_manager",
        "resume_condition": "resolve the bound child stop through an authorized recovery action",
        "evidence_refs": ["event:" + e.id for e in (event, cause, checkpoint) if e is not None],
        "evidence_paths": [str(state / "events.jsonl")],
    }


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
                return subprocess.CompletedProcess(command, proc.returncode, out, err), child_wait(worktree)
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
