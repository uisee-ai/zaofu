"""Backlog projection for failed self-eval runs."""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events import EventWriter, ZfEvent
from zf.core.events.factory import event_log_from_project
from zf.core.self_eval.contract import load_self_eval_contract
from zf.core.self_eval.runner import SelfEvalRunResult
from zf.core.task.schema import Task, TaskContract, TaskEvidence
from zf.core.task.store import TaskStore


@dataclass(frozen=True)
class SelfEvalBacklogResult:
    task_id: str
    key: str
    action: str


def write_failure_backlog(
    *,
    contract_path: Path,
    result: SelfEvalRunResult,
    state_dir: Path,
    config: ZfConfig | None = None,
    actor: str = "zf-self-eval",
) -> SelfEvalBacklogResult | None:
    """Upsert a backlog task for a failed self-eval run.

    Passing self-eval runs do not create tasks. The idempotency key is stable
    for the contract path, goal, and metric name so repeated failures update
    the same open backlog item instead of flooding the board.
    """
    if result.ok:
        return None

    contract = load_self_eval_contract(contract_path)
    key = _backlog_key(contract_path, contract.goal, contract.metric.name)
    title = _title(contract.goal)
    store = TaskStore(state_dir / "kanban.json")
    existing = _active_task_by_key(store, key)

    task_contract = TaskContract(
        behavior=_behavior(contract.goal, result.reason, contract.metric.name),
        verification=_verification_command(contract_path, result.output_dir),
        verification_tiers=["runtime"],
        scope=list(contract.scope.allow),
        exclusions=list(contract.scope.exclude),
        acceptance="self-eval status=passed and guard=passed",
        owner_role="dev",
        rework_to="dev",
        handoff_artifacts=[
            str(result.iterations_path),
            str(result.summary_path),
        ],
    )
    evidence = TaskEvidence(
        output_summary=result.reason,
        files_touched=[
            str(result.iterations_path),
            str(result.summary_path),
        ],
    )

    task = store.ensure(
        key=key,
        title=title,
        priority=2,
        skills_required=[
            "test-driven-development",
            "debugging-and-error-recovery",
        ],
        contract=task_contract,
        evidence=evidence,
    )

    action = "updated" if existing is not None else "created"
    _emit_projection_event(
        state_dir=state_dir,
        config=config,
        actor=actor,
        action=action,
        task=task,
        contract_path=contract_path,
        result=result,
        metric_name=contract.metric.name,
    )
    return SelfEvalBacklogResult(task_id=task.id, key=key, action=action)


def _active_task_by_key(store: TaskStore, key: str) -> Task | None:
    for task in store.list_all():
        if task.key == key:
            return task
    return None


def _backlog_key(contract_path: Path, goal: str, metric_name: str) -> str:
    resolved = str(contract_path.expanduser().resolve())
    digest = hashlib.sha256(
        f"{resolved}\n{goal}\n{metric_name}".encode("utf-8"),
    ).hexdigest()[:16]
    return f"self-eval:{digest}"


def _title(goal: str) -> str:
    prefix = "Fix self-eval failure: "
    clean_goal = " ".join(goal.split())
    limit = 120 - len(prefix)
    if len(clean_goal) > limit:
        clean_goal = clean_goal[: limit - 1].rstrip() + "..."
    return prefix + clean_goal


def _behavior(goal: str, reason: str, metric_name: str) -> str:
    return "\n".join([
        "Resolve the failed self-eval run and make the same contract pass.",
        f"Goal: {goal}",
        f"Metric: {metric_name}",
        f"Failure reason: {reason or 'unknown'}",
    ])


def _verification_command(contract_path: Path, output_dir: Path) -> str:
    return (
        "PYTHONPATH=src python3 -m zf.cli.main self-eval run "
        f"--contract {shlex.quote(str(contract_path))} "
        f"--output {shlex.quote(str(output_dir))}"
    )


def _emit_projection_event(
    *,
    state_dir: Path,
    config: ZfConfig | None,
    actor: str,
    action: str,
    task: Task,
    contract_path: Path,
    result: SelfEvalRunResult,
    metric_name: str,
) -> None:
    event_type = "task.updated" if action == "updated" else "task.created"
    log = event_log_from_project(state_dir, config=config)
    EventWriter(log).append(ZfEvent(
        type=event_type,
        actor=actor,
        task_id=task.id,
        payload={
            "source": "self-eval",
            "action": action,
            "key": task.key,
            "contract_path": str(contract_path),
            "output_dir": str(result.output_dir),
            "iterations_path": str(result.iterations_path),
            "summary_path": str(result.summary_path),
            "status": result.status,
            "score": result.score,
            "metric": metric_name,
            "reason": result.reason,
        },
    ))
