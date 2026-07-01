"""Deterministic scripted E2E runner for ZaoFu harness contracts.

This layer exercises the same .zf projections as real provider runs without
starting tmux or calling Claude/Codex. It is intentionally small: scenarios
emit known events, update TaskStore/FeatureStore, and record cost entries so
phase reports and robustness scorecards can be tested in normal CI.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from zf.core.cost.tracker import CostTracker
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore


SCRIPTED_BACKEND = "scripted"


@dataclass(frozen=True)
class ScriptedRunResult:
    scenario: str
    state_dir: Path
    events_path: Path
    task_ids: tuple[str, ...]
    feature_id: str
    task_done_count: int
    feature_done_count: int
    cost_entries: int


def prepare_state(worktree: Path, *, clean: bool = True) -> Path:
    """Create a minimal .zf state directory for deterministic E2E runs."""
    if clean and worktree.exists():
        shutil.rmtree(worktree)
    state_dir = worktree / ".zf"
    for child in ("artifacts", "briefings", "logs", "memory"):
        (state_dir / child).mkdir(parents=True, exist_ok=True)
    for file_name, content in {
        "events.jsonl": "",
        "cost.jsonl": "",
        "kanban.json": "[]\n",
        "feature_list.json": "[]\n",
        "role_sessions.yaml": "{}\n",
        "session.yaml": (
            "session_id: scripted\n"
            "runtime_state: running\n"
            "latest_event_offset: 0\n"
        ),
    }.items():
        (state_dir / file_name).write_text(content, encoding="utf-8")
    return state_dir


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _archive_done_count(archive_dir: Path) -> int:
    count = 0
    for path in sorted(archive_dir.glob("*.json")) if archive_dir.exists() else []:
        try:
            rows = json.loads(path.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError:
            continue
        if isinstance(rows, list):
            count += sum(
                1
                for row in rows
                if isinstance(row, dict) and row.get("status") == "done"
            )
    return count


def _emit(
    log: EventLog,
    event_type: str,
    *,
    actor: str = "scripted-runner",
    task_id: str | None = None,
    payload: dict | None = None,
) -> ZfEvent:
    event = ZfEvent(
        type=event_type,
        actor=actor,
        task_id=task_id,
        payload=payload or {},
    )
    log.append(event)
    return event


def _record_usage(
    log: EventLog,
    cost: CostTracker,
    *,
    role: str,
    instance_id: str,
    task_id: str,
    backend: str = SCRIPTED_BACKEND,
) -> None:
    cost.record_usage(
        role=role,
        instance_id=instance_id,
        input_tokens=100,
        output_tokens=20,
        model="default",
        backend=backend,
    )
    _emit(
        log,
        "agent.usage",
        actor=instance_id,
        task_id=task_id,
        payload={
            "backend": backend,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    )


def _judge_payload() -> dict:
    return {
        "summary": "scripted judge verified full lifecycle",
        "checks": [
            {
                "command": "scripted lifecycle assertion",
                "exit_code": 0,
                "passed": True,
                "tier": tier,
                "artifact_refs": [".zf/events.jsonl"],
                "evidence_refs": [".zf/kanban.json", ".zf/feature_list.json"],
            }
            for tier in ("static", "e2e")
        ],
        "scores": {
            "correctness": {"score": 1, "passed": True},
            "completeness": {"score": 1, "passed": True},
            "regression_risk": {"score": 1, "passed": True},
            "evidence_quality": {"score": 1, "passed": True},
        },
        "artifact_refs": [".zf/events.jsonl"],
        "evidence_refs": [".zf/kanban.json", ".zf/feature_list.json"],
    }


def _assign(
    log: EventLog,
    tasks: TaskStore,
    *,
    task_id: str,
    role: str,
    instance_id: str,
    from_status: str = "backlog",
) -> None:
    tasks.update(
        task_id,
        status="in_progress",
        assigned_to=instance_id,
    )
    _emit(
        log,
        "task.assigned",
        actor="orchestrator",
        task_id=task_id,
        payload={
            "role": role,
            "instance_id": instance_id,
            "from": from_status,
            "to": "in_progress",
        },
    )
    _emit(
        log,
        "task.dispatched",
        actor="orchestrator",
        task_id=task_id,
        payload={"role": role, "instance_id": instance_id},
    )


def _close_task(
    log: EventLog,
    tasks: TaskStore,
    *,
    task_id: str,
    assigned_to: str,
) -> None:
    tasks.update(
        task_id,
        status="done",
        assigned_to=assigned_to,
    )
    _emit(
        log,
        "task.status_changed",
        actor="orchestrator",
        task_id=task_id,
        payload={"from": "in_progress", "to": "done"},
    )


def _run_task_lifecycle(
    *,
    log: EventLog,
    cost: CostTracker,
    tasks: TaskStore,
    task_id: str,
    task_index: int,
    dev_instances: tuple[str, ...],
    test_instances: tuple[str, ...],
    scenario: str,
) -> None:
    dev_instance = dev_instances[task_index % len(dev_instances)]
    test_instance = test_instances[task_index % len(test_instances)]

    _assign(
        log,
        tasks,
        task_id=task_id,
        role="arch",
        instance_id="arch",
    )
    _record_usage(log, cost, role="arch", instance_id="arch", task_id=task_id)
    _emit(
        log,
        "arch.proposal.done",
        actor="arch",
        task_id=task_id,
        payload={"attempt": 1, "artifact": "pdd"},
    )

    _assign(
        log,
        tasks,
        task_id=task_id,
        role="critic",
        instance_id="critic",
        from_status="in_progress",
    )
    _record_usage(log, cost, role="critic", instance_id="critic", task_id=task_id)
    if scenario == "critic_rejected_once" and task_index == 0:
        _emit(
            log,
            "gate.failed",
            actor="critic",
            task_id=task_id,
            payload={
                "gate": "design_critique",
                "role": "critic",
                "reason": "scripted design critique",
            },
        )
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="arch",
            instance_id="arch",
            from_status="in_progress",
        )
        _record_usage(log, cost, role="arch", instance_id="arch", task_id=task_id)
        _emit(
            log,
            "arch.proposal.done",
            actor="arch",
            task_id=task_id,
            payload={"attempt": 2, "reworked_from": "critic"},
        )
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="critic",
            instance_id="critic",
            from_status="in_progress",
        )
        _record_usage(
            log, cost, role="critic", instance_id="critic", task_id=task_id,
        )
    _emit(
        log,
        "design.critique.done",
        actor="critic",
        task_id=task_id,
        payload={"status": "approved"},
    )

    _assign(
        log,
        tasks,
        task_id=task_id,
        role="dev",
        instance_id=dev_instance,
        from_status="in_progress",
    )
    _record_usage(log, cost, role="dev", instance_id=dev_instance, task_id=task_id)
    build_event = _emit(
        log,
        "dev.build.done",
        actor=dev_instance,
        task_id=task_id,
        payload={"attempt": 1},
    )
    if scenario == "duplicate_event":
        _emit(
            log,
            "dev.build.done",
            actor=dev_instance,
            task_id=task_id,
            payload={"attempt": 1, "duplicate_of": build_event.id},
        )

    if scenario == "worker_timeout_recovery" and task_index == 0:
        _emit(
            log,
            "worker.stuck",
            actor="watcher",
            task_id=task_id,
            payload={
                "role": "dev",
                "instance_id": dev_instance,
                "reason": "scripted timeout",
            },
        )
        replacement = dev_instances[(task_index + 1) % len(dev_instances)]
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="dev",
            instance_id=replacement,
            from_status="in_progress",
        )
        _record_usage(
            log,
            cost,
            role="dev",
            instance_id=replacement,
            task_id=task_id,
        )
        _emit(
            log,
            "dev.build.done",
            actor=replacement,
            task_id=task_id,
            payload={"attempt": 2, "recovered_from": dev_instance},
        )
        dev_instance = replacement

    _assign(
        log,
        tasks,
        task_id=task_id,
        role="review",
        instance_id="review",
        from_status="in_progress",
    )
    _record_usage(log, cost, role="review", instance_id="review", task_id=task_id)
    if scenario == "review_rejected_once" and task_index == 0:
        _emit(
            log,
            "review.rejected",
            actor="review",
            task_id=task_id,
            payload={"reason": "scripted rework"},
        )
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="dev",
            instance_id=dev_instance,
            from_status="in_progress",
        )
        _record_usage(
            log,
            cost,
            role="dev",
            instance_id=dev_instance,
            task_id=task_id,
        )
        _emit(
            log,
            "dev.build.done",
            actor=dev_instance,
            task_id=task_id,
            payload={"attempt": 2, "reason": "review rework"},
        )
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="review",
            instance_id="review",
            from_status="in_progress",
        )
    _emit(log, "review.approved", actor="review", task_id=task_id)

    _assign(
        log,
        tasks,
        task_id=task_id,
        role="test",
        instance_id=test_instance,
        from_status="in_progress",
    )
    _record_usage(
        log,
        cost,
        role="test",
        instance_id=test_instance,
        task_id=task_id,
    )
    if scenario == "test_failed_once" and task_index == 0:
        _emit(
            log,
            "test.failed",
            actor=test_instance,
            task_id=task_id,
            payload={"reason": "scripted independent test failure"},
        )
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="dev",
            instance_id=dev_instance,
            from_status="in_progress",
        )
        _record_usage(
            log,
            cost,
            role="dev",
            instance_id=dev_instance,
            task_id=task_id,
        )
        _emit(
            log,
            "dev.build.done",
            actor=dev_instance,
            task_id=task_id,
            payload={"attempt": 2, "reason": "test rework"},
        )
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="review",
            instance_id="review",
            from_status="in_progress",
        )
        _record_usage(
            log,
            cost,
            role="review",
            instance_id="review",
            task_id=task_id,
        )
        _emit(log, "review.approved", actor="review", task_id=task_id)
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="test",
            instance_id=test_instance,
            from_status="in_progress",
        )
        _record_usage(
            log,
            cost,
            role="test",
            instance_id=test_instance,
            task_id=task_id,
        )
    _emit(log, "test.passed", actor=test_instance, task_id=task_id)

    _assign(
        log,
        tasks,
        task_id=task_id,
        role="judge",
        instance_id="judge",
        from_status="in_progress",
    )
    _record_usage(
        log, cost, role="judge", instance_id="judge", task_id=task_id,
    )
    if scenario == "judge_failed_once" and task_index == 0:
        _emit(
            log,
            "judge.failed",
            actor="judge",
            task_id=task_id,
            payload={"reason": "scripted final gate failure"},
        )
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="dev",
            instance_id=dev_instance,
            from_status="in_progress",
        )
        _record_usage(
            log,
            cost,
            role="dev",
            instance_id=dev_instance,
            task_id=task_id,
        )
        _emit(
            log,
            "dev.build.done",
            actor=dev_instance,
            task_id=task_id,
            payload={"attempt": 2, "reason": "judge rework"},
        )
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="review",
            instance_id="review",
            from_status="in_progress",
        )
        _record_usage(
            log,
            cost,
            role="review",
            instance_id="review",
            task_id=task_id,
        )
        _emit(log, "review.approved", actor="review", task_id=task_id)
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="test",
            instance_id=test_instance,
            from_status="in_progress",
        )
        _record_usage(
            log,
            cost,
            role="test",
            instance_id=test_instance,
            task_id=task_id,
        )
        _emit(log, "test.passed", actor=test_instance, task_id=task_id)
        _assign(
            log,
            tasks,
            task_id=task_id,
            role="judge",
            instance_id="judge",
            from_status="in_progress",
        )
        _record_usage(
            log,
            cost,
            role="judge",
            instance_id="judge",
            task_id=task_id,
        )
    _emit(
        log,
        "judge.passed",
        actor="judge",
        task_id=task_id,
        payload=_judge_payload(),
    )
    _close_task(log, tasks, task_id=task_id, assigned_to="judge")


def run_scripted_scenario(
    worktree: Path,
    *,
    scenario: str = "happy_path",
    tasks_count: int = 1,
    clean: bool = True,
    dev_instances: tuple[str, ...] = ("dev-1", "dev-2"),
    test_instances: tuple[str, ...] = ("test-1", "test-2"),
) -> ScriptedRunResult:
    """Run a deterministic lifecycle scenario against real .zf stores."""
    if tasks_count < 1:
        raise ValueError("tasks_count must be >= 1")
    supported = {
        "happy_path",
        "critic_rejected_once",
        "review_rejected_once",
        "test_failed_once",
        "judge_failed_once",
        "duplicate_event",
        "invalid_transition",
        "worker_timeout_recovery",
    }
    if scenario not in supported:
        raise ValueError(f"unknown scripted scenario: {scenario}")

    state_dir = prepare_state(worktree, clean=clean)
    log = EventLog(state_dir / "events.jsonl")
    cost = CostTracker(state_dir / "cost.jsonl")
    tasks = TaskStore(state_dir / "kanban.json")
    features = FeatureStore(state_dir / "feature_list.json")

    _emit(log, "session.started", actor="zf-cli", payload={"scenario": scenario})
    _emit(
        log,
        "user.message",
        actor="user",
        payload={"text": f"scripted scenario {scenario}"},
    )

    feature = features.add(
        Feature(
            title=f"scripted {scenario}",
            status="active",
            user_message=f"scripted scenario {scenario}",
        )
    )
    _emit(
        log,
        "feature.created",
        actor="zf-cli",
        payload={"feature_id": feature.id, "status": feature.status},
    )

    task_ids: list[str] = []
    for index in range(tasks_count):
        task = tasks.add(
            Task(
                title=f"scripted task {index + 1}",
                key=f"scripted-{index + 1}",
                contract=TaskContract(
                    behavior="exercise scripted lifecycle",
                    verification="scripted event projection reaches done",
                    verification_tiers=["static", "e2e"],
                    scope=[".zf"],
                ),
            )
        )
        task_ids.append(task.id)
        _emit(
            log,
            "task.created",
            actor="zf-cli",
            task_id=task.id,
            payload={"feature_id": feature.id, "index": index},
        )
        _emit(
            log,
            "task.contract.update",
            actor="orchestrator",
            task_id=task.id,
            payload={
                "behavior": task.contract.behavior,
                "verification": task.contract.verification,
                "verification_tiers": list(task.contract.verification_tiers),
                "scope": list(task.contract.scope),
                "acceptance": task.contract.acceptance,
                "source": "scripted_runner",
            },
        )

    if scenario == "invalid_transition":
        first = task_ids[0]
        _emit(
            log,
            "task.invalid_transition",
            actor="orchestrator",
            task_id=first,
            payload={
                "from": "backlog",
                "to": "done",
                "reason": "cannot close before build/review/test/judge",
            },
        )
    else:
        for index, task_id in enumerate(task_ids):
            _run_task_lifecycle(
                log=log,
                cost=cost,
                tasks=tasks,
                task_id=task_id,
                task_index=index,
                dev_instances=dev_instances,
                test_instances=test_instances,
                scenario=scenario,
            )
        features.update(feature.id, status="done")
        _emit(
            log,
            "feature.status_changed",
            actor="orchestrator",
            task_id=feature.id,
            payload={"feature_id": feature.id, "from": "active", "to": "done"},
        )

    events = _read_jsonl(state_dir / "events.jsonl")
    task_done_count = sum(
        1
        for event in events
        if event.get("type") == "task.status_changed"
        and (event.get("payload") or {}).get("to") == "done"
    )
    done_features = {
        str(event.get("task_id") or (event.get("payload") or {}).get("feature_id"))
        for event in events
        if event.get("type") == "feature.status_changed"
        and (event.get("payload") or {}).get("to") == "done"
    }
    if _archive_done_count(state_dir / "feature_list") > 0:
        done_features.add(feature.id)
    feature_done_count = len(done_features)

    return ScriptedRunResult(
        scenario=scenario,
        state_dir=state_dir,
        events_path=state_dir / "events.jsonl",
        task_ids=tuple(task_ids),
        feature_id=feature.id,
        task_done_count=task_done_count,
        feature_done_count=feature_done_count,
        cost_entries=len(_read_jsonl(state_dir / "cost.jsonl")),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument(
        "--scenario",
        choices=[
            "happy_path",
            "critic_rejected_once",
            "review_rejected_once",
            "test_failed_once",
            "judge_failed_once",
            "duplicate_event",
            "invalid_transition",
            "worker_timeout_recovery",
        ],
        default="happy_path",
    )
    parser.add_argument("--tasks", type=int, default=1)
    parser.add_argument("--no-clean", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    result = run_scripted_scenario(
        args.worktree,
        scenario=args.scenario,
        tasks_count=args.tasks,
        clean=not args.no_clean,
    )
    print(
        json.dumps(
            {
                "scenario": result.scenario,
                "state_dir": str(result.state_dir),
                "task_done_count": result.task_done_count,
                "feature_done_count": result.feature_done_count,
                "cost_entries": result.cost_entries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
