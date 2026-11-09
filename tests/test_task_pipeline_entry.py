from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

from zf.core.config.schema import ProjectConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.runtime.task_pipeline_entry import (
    reconcile_task_pipeline_entries,
    task_pipeline_entry_mode,
    task_pipeline_external_evidence_bindings,
)
from zf.runtime.writer_task_materialization import writer_task_allowed_paths


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _repo(root: Path) -> str:
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("target\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "target")
    return _git(root, "rev-parse", "HEAD")


def _external_task(receipt: Path, target: str) -> Task:
    return Task(
        id="TASK-EXTERNAL",
        title="collect human evidence",
        contract=TaskContract(
            feature_id="FEATURE-1",
            scope=[],
            evidence_contract={
                "required_manual_evidence": str(receipt),
                "continuation_checkpoint": f"git:{target}",
            },
            acceptance_criteria=[{
                "id": "AC8",
                "statement": "independent comprehension",
                "mandatory": True,
                "verification_owner": "human",
                "verification_tier": "manual_evidence",
            }],
            validation={
                "commands": [{
                    "id": "manual",
                    "command": "record-human-evidence",
                    "owner": "human",
                    "tier": "manual_evidence",
                }],
            },
        ),
    )


def test_entry_mode_distinguishes_external_verify_only_and_standard(
    tmp_path: Path,
) -> None:
    external = _external_task(tmp_path / "receipt.json", "a" * 40)
    verify_only = Task(
        id="TASK-AUDIT",
        contract=TaskContract(evidence_contract={"runtime_only": True}),
    )

    assert task_pipeline_entry_mode(external) == "external_gate"
    assert task_pipeline_entry_mode(verify_only) == "verify_only"
    assert task_pipeline_entry_mode(Task(id="TASK-WRITER")) == "standard"


def test_explicit_empty_allowed_paths_survives_loader_projection() -> None:
    assert writer_task_allowed_paths(
        {"allowed_paths": ["prose scope accidentally projected as a path"]},
        {"allowed_paths": [], "scope": "read-only candidate audit"},
        fallback="fallback/**",
    ) == []


def test_external_gate_waits_without_worker_and_admits_operator_evidence_once(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    target = _repo(project)
    state_dir = project / ".zf"
    state_dir.mkdir()
    receipt = tmp_path / "human-receipt.json"
    task = _external_task(receipt, target)
    store = TaskStore(state_dir / "kanban.json")
    store.add(task)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log, default_origin="kernel")
    runtime = SimpleNamespace(
        project_root=project,
        state_dir=state_dir,
        config=ZfConfig(
            project=ProjectConfig(name="external-gate", state_dir=str(state_dir)),
        ),
        task_store=store,
        event_log=log,
        event_writer=writer,
    )
    context = {
        "workflow_run_id": "run-1",
        "task_map_generation": "map-1",
        "dispatch_base_commit": target,
        "generation_admitted_event_id": "evt-generation",
    }

    first, satisfied = reconcile_task_pipeline_entries(
        runtime,
        generation_contexts={task.id: context},
    )
    second, replay_satisfied = reconcile_task_pipeline_entries(
        runtime,
        generation_contexts={task.id: context},
    )

    assert satisfied == replay_satisfied == set()
    assert first[0].action == "task_pipeline_external_gate_waiting"
    assert second[0].action == "task_pipeline_external_gate_waiting"
    escalations = [event for event in log.read_all() if event.type == "human.escalate"]
    assert len(escalations) == 1
    assert not [
        event for event in log.read_all()
        if event.type == "task.pipeline.stage.dispatched"
    ]

    # The actual producer must preserve the consumer's task-scoped gate:
    # unrelated work may recover while this task and its dependents wait.
    from zf.runtime.run_manager import _pending_human_decisions
    from zf.runtime.run_manager_action_admission import action_gate_reason

    store.add(Task(id="TASK-INDEPENDENT"))
    store.add(Task(id="TASK-DIRECT", blocked_by=[task.id]))
    store.add(Task(id="TASK-TRANSITIVE", blocked_by=["TASK-DIRECT"]))
    pending = _pending_human_decisions(log.read_all())
    assert action_gate_reason(
        state_dir, log.read_all(), {"task_id": "TASK-INDEPENDENT"}, pending,
    ) == ""
    for blocked_task in (task.id, "TASK-DIRECT", "TASK-TRANSITIVE", ""):
        assert action_gate_reason(
            state_dir, log.read_all(), {"task_id": blocked_task}, pending,
        ) == "human_decision_pending"

    receipt.write_text('{"status":"passed"}\n', encoding="utf-8")
    receipt.chmod(0o444)
    file_digest = hashlib.sha256(receipt.read_bytes()).hexdigest()
    receipt_digest = "c" * 64
    escalation = escalations[0]
    writer.append(ZfEvent(
        type="human.resolved",
        actor="operator",
        task_id=task.id,
        correlation_id="run-1",
        payload={
            "schema_version": "task-pipeline-external-gate-resolution.v1",
            "workflow_run_id": "run-1",
            "task_map_generation": "map-1",
            "decision_token": escalation.payload["decision_token"],
            "escalation_event_id": escalation.id,
            "action": "provide_required_evidence",
            "target_commit": target,
            "evidence_ref": {
                "path": str(receipt),
                "sha256": file_digest,
            },
            "receipt_digest": receipt_digest,
        },
    ))

    admitted, satisfied = reconcile_task_pipeline_entries(
        runtime,
        generation_contexts={task.id: context},
    )
    replay, replay_satisfied = reconcile_task_pipeline_entries(
        runtime,
        generation_contexts={task.id: context},
    )

    assert admitted[0].action == "task_pipeline_external_gate_satisfied"
    assert replay == []
    assert satisfied == replay_satisfied == {task.id}
    assert len([
        event for event in log.read_all()
        if event.type == "task.pipeline.external_gate.satisfied"
    ]) == 1
    assert _git(project, "rev-parse", "refs/heads/task/TASK-EXTERNAL") == target
    consumer = Task(
        id="TASK-AUDIT",
        blocked_by=[task.id],
        contract=TaskContract(evidence_contract={
            "required_external_digest_env": "AC8_RECEIPT_DIGEST",
        }),
    )
    assert task_pipeline_external_evidence_bindings(
        runtime,
        task=consumer,
        context=context,
    ) == [{
        "env": "AC8_RECEIPT_DIGEST",
        "value": receipt_digest,
        "path": str(receipt),
        "sha256": file_digest,
        "source_event_id": next(
            event.id for event in log.read_all()
            if event.type == "task.pipeline.external_gate.satisfied"
        ),
    }]


def test_external_gate_rejects_mutable_or_digest_mismatched_file(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    target = _repo(project)
    state_dir = project / ".zf"
    state_dir.mkdir()
    receipt = tmp_path / "human-receipt.json"
    receipt.write_text("{}\n", encoding="utf-8")
    task = _external_task(receipt, target)
    store = TaskStore(state_dir / "kanban.json")
    store.add(task)
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log, default_origin="kernel")
    runtime = SimpleNamespace(
        project_root=project,
        state_dir=state_dir,
        config=ZfConfig(project=ProjectConfig(name="gate", state_dir=str(state_dir))),
        task_store=store,
        event_log=log,
        event_writer=writer,
    )
    context = {
        "workflow_run_id": "run-1",
        "task_map_generation": "map-1",
        "dispatch_base_commit": target,
        "generation_admitted_event_id": "evt-generation",
    }
    reconcile_task_pipeline_entries(
        runtime,
        generation_contexts={task.id: context},
    )
    escalation = next(
        event for event in log.read_all() if event.type == "human.escalate"
    )
    writer.append(ZfEvent(
        type="human.resolved",
        actor="operator",
        task_id=task.id,
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "task_map_generation": "map-1",
            "decision_token": escalation.payload["decision_token"],
            "escalation_event_id": escalation.id,
            "action": "provide_required_evidence",
            "target_commit": target,
            "evidence_ref": {"path": str(receipt), "sha256": "f" * 64},
            "receipt_digest": "e" * 64,
        },
    ))

    decisions, satisfied = reconcile_task_pipeline_entries(
        runtime,
        generation_contexts={task.id: context},
    )

    assert satisfied == set()
    assert decisions[0].reason == "required_manual_evidence_resolution_invalid"
    assert not [
        event for event in log.read_all()
        if event.type == "task.pipeline.external_gate.satisfied"
    ]
