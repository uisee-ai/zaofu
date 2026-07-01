"""Export real command/run evidence into eval-result.v1 artifacts."""

from __future__ import annotations

import csv
import json
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zf.autoresearch.eval_result import EvalResult, GateResult
from zf.core.events.log import EventLog

SUCCESS_STATUSES = {"passed", "done", "converged", "success"}


def _clamp(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 100:
        return 100.0
    return round(value, 2)


def _completion_score(tasks_done: int, expected_done: int) -> float:
    if expected_done <= 0:
        return 0.0
    return _clamp((tasks_done / expected_done) * 100.0)


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _latest_iteration_row(run_dir: Path) -> dict[str, str]:
    path = run_dir / "iterations.tsv"
    if not path.exists():
        return {}
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines(), delimiter="\t"))
    return rows[-1] if rows else {}


@dataclass(frozen=True)
class CommandEvalExport:
    result: EvalResult
    command_log: dict[str, Any]


def export_command_eval_result(
    *,
    command: str,
    cwd: Path,
    result_id: str,
    scenario_id: str,
    mode: str,
    timeout_seconds: int = 120,
    evidence_log: Path | None = None,
) -> CommandEvalExport:
    """Run a real command and convert its result into eval-result.v1."""

    started_at = time.time()
    args = shlex.split(command)
    proc = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    elapsed = round(time.time() - started_at, 3)
    passed = proc.returncode == 0
    status = "passed" if passed else "failed"
    log = {
        "command": command,
        "cwd": str(cwd),
        "returncode": proc.returncode,
        "elapsed_seconds": elapsed,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }
    if evidence_log is not None:
        evidence_log.parent.mkdir(parents=True, exist_ok=True)
        evidence_log.write_text(
            json.dumps(log, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    evidence_refs = {"command": [str(evidence_log)]} if evidence_log else {}
    result = EvalResult(
        result_id=result_id,
        scenario_id=scenario_id,
        mode=mode,
        gates=[
            GateResult(
                name="command",
                status=status,
                reason=f"returncode={proc.returncode}",
                evidence_refs=[str(evidence_log)] if evidence_log else [],
            )
        ],
        scores={
            "correctness": 100.0 if passed else 0.0,
            "regression": 90.0 if passed else 50.0,
            "stability": 90.0 if passed else 40.0,
            "harness_recovery": 80.0 if passed else 30.0,
            "context_safety": 80.0 if passed else 40.0,
            "coordination": 70.0 if passed else 50.0,
            "cost_efficiency": _clamp(100.0 - min(elapsed, 100.0)),
            "learning_value": 70.0 if passed else 30.0,
        },
        evidence_refs=evidence_refs,
        metadata={
            "source": "command",
            "command": command,
            "cwd": str(cwd),
            "returncode": proc.returncode,
            "elapsed_seconds": elapsed,
        },
    )
    return CommandEvalExport(result=result, command_log=log)


def export_run_dir_eval_result(
    *,
    run_dir: Path,
    result_id: str,
    scenario_id: str,
    mode: str,
) -> EvalResult:
    """Convert an autoresearch run_dir summary into eval-result.v1."""

    row = _latest_iteration_row(run_dir)
    events_summary = _read_json(run_dir / "events-summary.json")
    status = str(row.get("status") or "").strip().lower()
    tasks_done = _as_int(row.get("tasks_done"))
    expected_done = _as_int(row.get("expected_done"))
    fatal_type = str(row.get("fatal_type") or "").strip()
    if not status:
        status = "passed" if expected_done > 0 and tasks_done >= expected_done else "unknown"
    passed = status in SUCCESS_STATUSES
    completion = _completion_score(tasks_done, expected_done)
    gate_status = "passed" if passed else "failed"
    fatal_penalty = 25.0 if fatal_type else 0.0
    return EvalResult(
        result_id=result_id,
        scenario_id=scenario_id,
        mode=mode,
        gates=[
            GateResult(
                name="autoresearch_run",
                status=gate_status,
                reason=f"status={status}; tasks_done={tasks_done}/{expected_done}",
                evidence_refs=[
                    str(run_dir / "iterations.tsv"),
                    str(run_dir / "events-summary.json"),
                ],
            )
        ],
        scores={
            "correctness": completion if passed else min(completion, 60.0),
            "regression": 80.0 if passed else 50.0,
            "stability": _clamp((90.0 if passed else 45.0) - fatal_penalty),
            "harness_recovery": _clamp((85.0 if passed else 35.0) - fatal_penalty),
            "context_safety": _clamp(80.0 if passed else 45.0),
            "coordination": _clamp(75.0 if passed else 45.0),
            "cost_efficiency": 60.0,
            "learning_value": 60.0 if passed else 35.0,
        },
        evidence_refs={
            "run": [str(run_dir)],
            "events": [str(run_dir / "events-summary.json")],
        },
        metadata={
            "source": "run_dir",
            "run_dir": str(run_dir),
            "status": status,
            "tasks_done": tasks_done,
            "expected_done": expected_done,
            "fatal_type": fatal_type,
            "derived_metrics": events_summary.get("derived_metrics", {}),
        },
    )


def _count_events(events, event_type: str) -> int:
    return sum(1 for event in events if getattr(event, "type", "") == event_type)


def _event_payload(event) -> dict[str, Any]:
    payload = getattr(event, "payload", {}) or {}
    return payload if isinstance(payload, dict) else {}


def _usage_cost_score(events) -> float:
    usage_events = [event for event in events if getattr(event, "type", "") == "agent.usage"]
    if not usage_events:
        return 70.0
    total = 0
    for event in usage_events:
        payload = _event_payload(event)
        total += _as_int(payload.get("total_tokens") or payload.get("tokens") or payload.get("output_tokens"))
    if total <= 0:
        return 70.0
    return _clamp(100.0 - min(total / 10000.0, 70.0))


def export_state_dir_eval_result(
    *,
    state_dir: Path,
    result_id: str,
    scenario_id: str,
    mode: str,
) -> EvalResult:
    """Convert event-log truth into an eval-result.v1 artifact."""

    events_path = Path(state_dir) / "events.jsonl"
    if not events_path.exists():
        return EvalResult(
            result_id=result_id,
            scenario_id=scenario_id,
            mode=mode,
            gates=[
                GateResult(
                    name="event_log",
                    status="failed",
                    reason=f"missing events.jsonl: {events_path}",
                    evidence_refs=[str(events_path)],
                )
            ],
            scores={
                "correctness": 0.0,
                "regression": 30.0,
                "stability": 20.0,
                "harness_recovery": 10.0,
                "context_safety": 50.0,
                "coordination": 0.0,
                "cost_efficiency": 50.0,
                "learning_value": 10.0,
            },
            evidence_refs={"events": [str(events_path)]},
            metadata={"source": "state_dir", "state_dir": str(state_dir)},
        )
    log = EventLog(events_path)
    try:
        events = log.read_all()
    finally:
        log.close()

    terminal_done = (
        _count_events(events, "task.done")
        + _count_events(events, "task.done.accepted")
        + _count_events(events, "task.archived")
    )
    candidate_ready = _count_events(events, "candidate.ready")
    fanout_started = _count_events(events, "fanout.started")
    fanout_dispatched = _count_events(events, "fanout.child.dispatched")
    fanout_completed = _count_events(events, "fanout.aggregate.completed")
    fanout_cancelled = _count_events(events, "fanout.cancelled")
    fatal = sum(
        _count_events(events, event_type)
        for event_type in (
            "run.failed",
            "orchestrator.dispatch_failed",
            "worker.respawn.failed",
            "worker.stuck.recovery_failed",
            "worker_stuck.recovery_failed",
            "runtime.safe_halted",
        )
    )
    stuck = _count_events(events, "worker.stuck") + _count_events(events, "worker_stuck")
    recovered = (
        _count_events(events, "worker.stuck.recovered")
        + _count_events(events, "worker_stuck.recovered")
        + _count_events(events, "task.requeued")
    )
    loop_requests = _count_events(events, "autoresearch.loop.requested")
    reflections = _count_events(events, "autoresearch.loop.completed")
    passed = (
        fatal == 0
        and fanout_cancelled == 0
        and (
            terminal_done > 0
            or candidate_ready > 0
            or fanout_completed > 0
        )
    )
    gate_reason = (
        f"done={terminal_done}; candidate_ready={candidate_ready}; "
        f"fanout_started={fanout_started}; fanout_completed={fanout_completed}; "
        f"fanout_cancelled={fanout_cancelled}; fatal={fatal}"
    )
    correctness_base = 100.0 if passed else 35.0
    if terminal_done == 0 and candidate_ready == 0 and fanout_completed == 0:
        correctness_base = 0.0
    stability = _clamp(95.0 - fatal * 25.0 - max(stuck - recovered, 0) * 10.0)
    recovery = _clamp(70.0 + min(recovered, 3) * 10.0 - fatal * 20.0)
    coordination = _clamp(
        80.0
        + min(fanout_completed, 1) * 15.0
        - fanout_cancelled * 40.0
        - max(fanout_started - fanout_completed - fanout_cancelled, 0) * 20.0
    )
    learning = _clamp(40.0 + loop_requests * 20.0 + reflections * 20.0)
    return EvalResult(
        result_id=result_id,
        scenario_id=scenario_id,
        mode=mode,
        gates=[
            GateResult(
                name="event_log",
                status="passed" if passed else "failed",
                reason=gate_reason,
                evidence_refs=[str(events_path)],
            )
        ],
        scores={
            "correctness": _clamp(correctness_base),
            "regression": _clamp(90.0 - fatal * 25.0),
            "stability": stability,
            "harness_recovery": recovery,
            "context_safety": _clamp(85.0 - max(stuck - recovered, 0) * 10.0),
            "coordination": coordination,
            "cost_efficiency": _usage_cost_score(events),
            "learning_value": learning,
        },
        evidence_refs={"events": [str(events_path)]},
        metadata={
            "source": "state_dir",
            "state_dir": str(state_dir),
            "derived_metrics": {
                "terminal_done": terminal_done,
                "candidate_ready": candidate_ready,
                "fanout_started": fanout_started,
                "fanout_child_dispatched": fanout_dispatched,
                "fanout_aggregate_completed": fanout_completed,
                "fanout_cancelled": fanout_cancelled,
                "fatal": fatal,
                "stuck": stuck,
                "recovered": recovered,
                "autoresearch_loop_requested": loop_requests,
            },
        },
    )


__all__ = [
    "CommandEvalExport",
    "export_command_eval_result",
    "export_run_dir_eval_result",
    "export_state_dir_eval_result",
]
