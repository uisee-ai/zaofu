from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.schema import (
    GitIsolationConfig,
    ProjectConfig,
    RuntimeConfig,
    WorkdirConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.role_sessions import RoleSessionRegistry
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.candidates import CandidateRebuilder
from zf.runtime.candidate_incremental import (
    CandidateIncrementalError,
    _rolling_smoke_environment_error,
    _rolling_smoke_command,
    _select_integration_base,
    _successor_contract_binds_base,
    _update_candidate_ref,
)


def test_rolling_smoke_npm_prefix_runs_from_package_root(tmp_path) -> None:
    app = tmp_path / "app"
    app.mkdir()

    command, cwd = _rolling_smoke_command(
        "npm --prefix app exec tsc -- --noEmit",
        tmp_path,
    )

    assert cwd == app.resolve()
    assert command == "npm exec tsc -- --noEmit"


def test_rolling_smoke_reports_missing_npm_environment(tmp_path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    (app / "package.json").write_text("{}\n", encoding="utf-8")

    error = _rolling_smoke_environment_error(
        "npm --prefix app exec tsc -- --noEmit",
        app,
    )

    assert error == f"candidate_environment_not_ready: dependencies missing at {app}"
from zf.runtime.sidecar_refs import hydrate_sidecar_ref, write_sidecar_json
from zf.runtime.task_refs import TaskRefManager
from zf.runtime.task_pipeline_terminal import (
    _release_archived_task_stage_slot,
    reconcile_task_pipeline_freeze,
    reconcile_task_pipeline_terminals,
)
from zf.runtime.tmux import TmuxError
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    load_workflow_operation,
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _setup(
    tmp_path: Path,
    *,
    rolling_smoke: bool = True,
    provision_node_modules: bool = False,
):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Integration Test")
    _git(tmp_path, "config", "user.email", "integration@example.com")
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md", ".gitignore")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "branch", "-M", "main")
    base = _git(tmp_path, "rev-parse", "HEAD")
    if provision_node_modules:
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "tool.txt").write_text(
            "runtime dependency\n",
            encoding="utf-8",
        )

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="incremental", state_dir=str(state_dir)),
        runtime=RuntimeConfig(
            workdirs=WorkdirConfig(
                enabled=True,
                mode="worktree",
                provision_paths=["node_modules"] if provision_node_modules else [],
            ),
            git=GitIsolationConfig(
                candidate_base_ref="main",
                auto_ship_on_candidate_complete=True,
            ),
        ),
    )
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    task = Task(id="TASK-A", title="Task A", key="F-11111111:TASK-A")
    task.contract.contract_revision = "contract-r1"
    task.contract.scope = ["feature.txt"]
    task.contract.validation = {
        "commands": [{
            "id": "rolling-file",
            "command": "test -f feature.txt",
            "tier": "runtime",
            "rolling_smoke": rolling_smoke,
        }]
    }
    TaskStore(state_dir / "kanban.json").add(task)
    writer.append(ZfEvent(
        type="task.created",
        task_id=task.id,
        payload={"feature_id": "F-11111111"},
    ))

    _git(tmp_path, "checkout", "-q", "-b", "worker/task-a", "main")
    (tmp_path / "feature.txt").write_text("implemented\n", encoding="utf-8")
    _git(tmp_path, "add", "feature.txt")
    _git(tmp_path, "commit", "-q", "-m", "feat: task a")
    task_commit = _git(tmp_path, "rev-parse", "HEAD")
    ref_result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(ZfEvent(
        type="dev.build.done",
        actor="impl-1",
        task_id=task.id,
        payload={
            "source_commit": task_commit,
            "source_branch": "worker/task-a",
            "feature_id": "F-11111111",
            "files_touched": ["feature.txt"],
        },
    ))
    assert ref_result is not None and ref_result.status == "updated"
    return state_dir, config, log, writer, base, task_commit


def _integrate(
    root: Path,
    state_dir: Path,
    config: ZfConfig,
    log: EventLog,
    writer: EventWriter,
    base: str,
):
    return CandidateRebuilder(
        state_dir=state_dir,
        project_root=root,
        config=config,
        event_log=log,
    ).integrate_task_pipeline_task(
        task_id="TASK-A",
        workflow_run_id="run-1",
        task_map_generation="map-g1",
        operation_generation=1,
        pipeline_key="pipeline-A",
        dispatch_base_commit=base,
        contract_revision="contract-r1",
        event_writer=writer,
        causation_id="generation-event",
    )


def test_incremental_integration_emits_receipt_without_legacy_delivery_events(
    tmp_path: Path,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(tmp_path)

    result = _integrate(tmp_path, state_dir, config, log, writer, base)
    events = log.read_all()
    integrated = [
        event for event in events
        if event.type == "integration.queue.integrated"
    ]

    assert result.status == "integrated"
    assert len(integrated) == 1
    assert not [
        event for event in events
        if event.type in {"candidate.integration.completed", "candidate.ready"}
    ]
    receipt_ref = integrated[0].payload["receipt_ref"]
    receipt = hydrate_sidecar_ref(state_dir, receipt_ref).payload
    assert receipt["schema_version"] == "task-integration-receipt.v1"
    assert receipt["status"] == "integrated"
    assert receipt["new_candidate_head"] == _git(
        tmp_path,
        "rev-parse",
        "refs/heads/candidate/F-11111111",
    )
    assert receipt["rolling_smoke_receipt_refs"]
    assert TaskStore(state_dir / "kanban.json").get("TASK-A").status == "backlog"


def test_incremental_integration_replay_is_idempotent(tmp_path: Path) -> None:
    state_dir, config, log, writer, base, _ = _setup(tmp_path)

    first = _integrate(tmp_path, state_dir, config, log, writer, base)
    second = _integrate(tmp_path, state_dir, config, log, writer, base)
    events = log.read_all()

    assert first.payload["receipt_digest"] == second.payload["receipt_digest"]
    assert len([
        event for event in events
        if event.type == "integration.queue.integrated"
    ]) == 1
    assert len([
        event for event in events if event.type == "candidate.updated"
    ]) == 1


def test_incremental_redrive_reconciles_admitted_candidate_advancement(
    tmp_path: Path,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(tmp_path)
    marker = tmp_path / "allow-task-b"
    task = Task(id="TASK-B", title="Task B", key="F-11111111:TASK-B")
    task.contract.contract_revision = "contract-r1"
    task.contract.scope = ["second.txt"]
    task.contract.validation = {
        "commands": [{
            "id": "rolling-task-b",
            "command": f"test -f second.txt && test -f {marker}",
            "tier": "runtime",
            "rolling_smoke": True,
        }]
    }
    TaskStore(state_dir / "kanban.json").add(task)
    writer.append(ZfEvent(
        type="task.created",
        task_id=task.id,
        payload={"feature_id": "F-11111111"},
    ))
    _git(tmp_path, "checkout", "-q", "-b", "worker/task-b", base)
    (tmp_path / "second.txt").write_text("task b\n", encoding="utf-8")
    _git(tmp_path, "add", "second.txt")
    _git(tmp_path, "commit", "-q", "-m", "feat: task b")
    task_b_commit = _git(tmp_path, "rev-parse", "HEAD")
    ref_result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(ZfEvent(
        type="dev.build.done",
        actor="impl-2",
        task_id=task.id,
        payload={
            "source_commit": task_b_commit,
            "source_branch": "worker/task-b",
            "base_git_head": base,
            "feature_id": "F-11111111",
            "files_touched": ["second.txt"],
        },
    ))
    assert ref_result is not None and ref_result.status == "updated"
    rebuilder = CandidateRebuilder(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=log,
    )
    first_b = rebuilder.integrate_task_pipeline_task(
        task_id=task.id,
        workflow_run_id="run-1",
        task_map_generation="map-g1",
        operation_generation=1,
        pipeline_key="pipeline-B",
        dispatch_base_commit=base,
        contract_revision="contract-r1",
        event_writer=writer,
        causation_id="generation-event",
    )
    assert first_b.status == "needs_review"
    operation_id = str(first_b.payload["operation_id"])

    task_a = _integrate(tmp_path, state_dir, config, log, writer, base)
    marker.touch()
    repair = writer.append(ZfEvent(
        type="repair.action.requested",
        actor="operator",
        task_id=task.id,
        payload={
            "action_id": "repair-task-b",
            "kind": "retry_integration_queue_entry",
            "queue_entry_id": operation_id,
            "idempotency_key": "repair-task-b",
        },
    ))
    operation = load_workflow_operation(log, operation_id)
    assert operation is not None
    WorkflowOperationService(
        state_dir=state_dir,
        event_log=log,
        event_writer=writer,
    ).admit_redrive(
        operation_id=operation_id,
        request_hash=str(operation["request_hash"]),
        workflow_run_id="run-1",
        task_id=task.id,
        source_attempt_id=repair.id,
        recovery_decision_event_id=repair.id,
        reason="controlled candidate retry",
        recovery_decision_owner="controlled_action",
    )

    retried = rebuilder.integrate_task_pipeline_task(
        task_id=task.id,
        workflow_run_id="run-1",
        task_map_generation="map-g1",
        operation_generation=1,
        pipeline_key="pipeline-B",
        dispatch_base_commit=base,
        contract_revision="contract-r1",
        event_writer=writer,
        causation_id=repair.id,
    )

    assert retried.status == "integrated"
    receipt = hydrate_sidecar_ref(
        state_dir,
        retried.payload["receipt_ref"],
    ).payload
    assert receipt["expected_candidate_head"] == base
    assert receipt["previous_candidate_head"] == task_a.payload["candidate_head"]
    assert receipt["candidate_head_reconciled"] is True
    assert len(receipt["candidate_advancement_receipt_refs"]) == 1
    candidate = state_dir / "candidates" / "F-11111111" / "worktree"
    assert (candidate / "feature.txt").read_text(encoding="utf-8") == "implemented\n"
    assert (candidate / "second.txt").read_text(encoding="utf-8") == "task b\n"
    assert not [
        event for event in log.read_all()
        if event.type == "workflow.operation.blocked"
        and event.task_id == task.id
        and event.payload.get("reason") == "request_hash_divergence"
    ]


def test_incremental_integration_adopts_authorized_successor_base(
    tmp_path: Path,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(tmp_path)
    first = _integrate(tmp_path, state_dir, config, log, writer, base)
    previous_candidate = str(first.payload["candidate_head"])

    _git(
        tmp_path,
        "checkout",
        "-q",
        "-b",
        "worker/replacement-base",
        previous_candidate,
    )
    (tmp_path / "prerequisite.txt").write_text("verified base\n", encoding="utf-8")
    _git(tmp_path, "add", "prerequisite.txt")
    _git(tmp_path, "commit", "-q", "-m", "feat: verified replacement base")
    replacement_base = _git(tmp_path, "rev-parse", "HEAD")

    _git(tmp_path, "checkout", "-q", "-b", "worker/task-b")
    (tmp_path / "gap.txt").write_text("gap closure\n", encoding="utf-8")
    _git(tmp_path, "add", "gap.txt")
    _git(tmp_path, "commit", "-q", "-m", "feat: close successor gap")
    gap_commit = _git(tmp_path, "rev-parse", "HEAD")

    successor = Task(id="TASK-B", title="Task B", key="F-11111111:TASK-B")
    successor.contract.contract_revision = "contract-r2"
    successor.contract.scope = ["gap.txt"]
    successor.contract.source_ref = f"git:{replacement_base}"
    successor.contract.evidence_contract = {
        "supersedes_task_ids": ["TASK-A"],
    }
    successor.contract.validation = {
        "commands": [{
            "id": "rolling-successor-base",
            "command": "test -f prerequisite.txt && test -f gap.txt",
            "tier": "runtime",
            "rolling_smoke": True,
        }]
    }
    TaskStore(state_dir / "kanban.json").add(successor)
    writer.append(ZfEvent(
        type="task.created",
        task_id=successor.id,
        payload={"feature_id": "F-11111111"},
    ))
    ref_result = TaskRefManager(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
    ).process_dev_build_done(ZfEvent(
        type="dev.build.done",
        actor="impl-2",
        task_id=successor.id,
        payload={
            "source_commit": gap_commit,
            "source_branch": "worker/task-b",
            "base_git_head": replacement_base,
            "feature_id": "F-11111111",
            "files_touched": ["gap.txt"],
        },
    ))
    assert ref_result is not None and ref_result.status == "updated"

    result = CandidateRebuilder(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=log,
    ).integrate_task_pipeline_task(
        task_id=successor.id,
        workflow_run_id="run-2",
        task_map_generation="map-g2",
        operation_generation=1,
        pipeline_key="pipeline-B",
        dispatch_base_commit=replacement_base,
        contract_revision="contract-r2",
        event_writer=writer,
        causation_id="generation-event-2",
    )

    assert result.status == "integrated"
    receipt = hydrate_sidecar_ref(state_dir, result.payload["receipt_ref"]).payload
    assert receipt["previous_candidate_head"] == previous_candidate
    assert receipt["integration_base_head"] == replacement_base
    assert receipt["successor_base_adopted"] is True
    assert receipt["supersedes_task_ids"] == ["TASK-A"]
    assert receipt["adopted_base_commits"] == [replacement_base]
    candidate = state_dir / "candidates" / "F-11111111" / "worktree"
    assert (candidate / "prerequisite.txt").read_text(encoding="utf-8") == "verified base\n"
    assert (candidate / "gap.txt").read_text(encoding="utf-8") == "gap closure\n"


def test_incremental_integration_rejects_unbound_newer_dispatch_base(
    tmp_path: Path,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(tmp_path)
    first = _integrate(tmp_path, state_dir, config, log, writer, base)
    previous_candidate = str(first.payload["candidate_head"])

    _git(tmp_path, "checkout", "-q", "--detach", previous_candidate)
    (tmp_path / "unbound.txt").write_text("not authorized\n", encoding="utf-8")
    _git(tmp_path, "add", "unbound.txt")
    _git(tmp_path, "commit", "-q", "-m", "feat: unbound continuation")
    unbound_base = _git(tmp_path, "rev-parse", "HEAD")
    rebuilder = CandidateRebuilder(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=log,
    )

    with pytest.raises(
        CandidateIncrementalError,
        match="requires a successor contract bound",
    ):
        _select_integration_base(
            rebuilder,
            task_id="TASK-A",
            task_source_commit=unbound_base,
            expected_candidate_head=previous_candidate,
            dispatch_base_commit=unbound_base,
        )

    assert _git(
        tmp_path,
        "rev-parse",
        "refs/heads/candidate/F-11111111",
    ) == previous_candidate


def test_incremental_integration_accepts_admitted_generation_continuation(
    tmp_path: Path,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(tmp_path)
    first = _integrate(tmp_path, state_dir, config, log, writer, base)
    previous_candidate = str(first.payload["candidate_head"])

    _git(tmp_path, "checkout", "-q", "--detach", previous_candidate)
    (tmp_path / "evidence.txt").write_text("accepted evidence\n", encoding="utf-8")
    _git(tmp_path, "add", "evidence.txt")
    _git(tmp_path, "commit", "-q", "-m", "test: refresh accepted evidence")
    continuation_base = _git(tmp_path, "rev-parse", "HEAD")

    task_map_descriptor = write_sidecar_json(
        state_dir,
        "artifacts/test/continuation-task-map.json",
        {
            "schema_version": "task-map.v1",
            "metadata": {
                "writer_dispatch_base_commit": continuation_base,
            },
            "tasks": [{
                "task_id": "TASK-A",
                "source_ref": "docs/feature.md",
            }],
        },
        kind="task_map",
        schema_version="task-map.v1",
        created_by="test",
    )
    package_common = {
        "schema_version": "plan-artifact-package.v1",
        "workflow_run_id": "run-2",
        "flow_kind": "prd",
        "package_slot": "execution_plan",
        "producer_stage_id": "prd-plan",
        "run_contract_ref": "artifacts/test/run-contract.json",
        "run_contract_sha256": "a" * 64,
        "run_contract_digest": "b" * 64,
        "required_ports": ["task_map"],
        "produced": [{
            "logical_name": "task_map",
            "artifact_kind": "task_map",
            "schema_version": "task-map.v1",
            "producer_stage_id": "prd-plan",
            "ref": task_map_descriptor["ref"],
            "sha256": task_map_descriptor["sha256"],
        }],
        "inherited": [],
    }
    prior_package = write_sidecar_json(
        state_dir,
        "artifacts/test/prior-plan-package.json",
        {
            **package_common,
            "plan_revision": "map-g1",
            "task_map_generation": "map-g1",
        },
        kind="plan_artifact_package",
        schema_version="plan-artifact-package.v1",
        created_by="test",
    )
    package_descriptor = write_sidecar_json(
        state_dir,
        "artifacts/test/continuation-plan-package.json",
        {
            **package_common,
            "plan_revision": "map-g2",
            "task_map_generation": "map-g2",
            "supersedes_package_ref": prior_package["ref"],
            "supersedes_package_digest": prior_package["sha256"],
        },
        kind="plan_artifact_package",
        schema_version="plan-artifact-package.v1",
        created_by="test",
    )
    store = TaskStore(state_dir / "kanban.json")
    task = store.get("TASK-A")
    assert task is not None
    task.contract.evidence_contract = {
        "workflow_run_id": "run-2",
        "source_refs": {
            "task_map_ref": task_map_descriptor["ref"],
            "task_map_generation": "map-g2",
            "plan_artifact_package_ref": package_descriptor["ref"],
            "plan_artifact_package_digest": package_descriptor["sha256"],
        },
    }
    store.update(task.id, contract=task.contract)
    trigger = writer.append(ZfEvent(
        type="task_map.ready",
        actor="zf-cli",
        origin="kernel",
        correlation_id="run-2",
        payload={
            "workflow_run_id": "run-2",
            "target_ref": previous_candidate,
            "task_map_ref": task_map_descriptor["ref"],
            "task_map_generation": "map-g2",
            "plan_artifact_package_ref": package_descriptor["ref"],
            "plan_artifact_package_digest": package_descriptor["sha256"],
        },
    ))
    writer.append(ZfEvent(
        type="task.pipeline.generation.admitted",
        actor="zf-cli",
        origin="kernel",
        correlation_id="run-2",
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "generation_id": "generation-2",
            "workflow_run_id": "run-2",
            "trigger_event_id": trigger.id,
            "task_map_ref": task_map_descriptor["ref"],
            "task_map_generation": "map-g2",
            "plan_artifact_package_ref": package_descriptor["ref"],
            "plan_artifact_package_digest": package_descriptor["sha256"],
            "dispatch_base_commit": continuation_base,
            "task_ids": ["TASK-A"],
        },
    ))

    selected = _select_integration_base(
        CandidateRebuilder(
            state_dir=state_dir,
            project_root=tmp_path,
            config=config,
            event_log=log,
        ),
        task_id="TASK-A",
        task_source_commit=continuation_base,
        expected_candidate_head=previous_candidate,
        dispatch_base_commit=continuation_base,
    )

    assert selected == (
        continuation_base,
        True,
        [],
        [continuation_base],
    )


def test_successor_base_accepts_immutable_plan_package_binding(
    tmp_path: Path,
) -> None:
    state_dir, config, log, _writer, base_commit, _ = _setup(tmp_path)
    task_map_descriptor = write_sidecar_json(
        state_dir,
        "artifacts/test/successor-task-map.json",
        {
            "schema_version": "task-map.v1",
            "tasks": [{
                "task_id": "TASK-B",
                "base_commit": base_commit,
                "source_refs": ["docs/gap.md", f"git:{base_commit}"],
                "evidence_contract": {
                    "supersedes_task_ids": ["TASK-A"],
                },
            }],
        },
        kind="task_map",
        schema_version="task-map.v1",
        created_by="test",
    )
    package_descriptor = write_sidecar_json(
        state_dir,
        "artifacts/test/successor-plan-package.json",
        {
            "schema_version": "plan-artifact-package.v1",
            "workflow_run_id": "run-2",
            "flow_kind": "refactor",
            "package_slot": "execution_plan",
            "producer_stage_id": "gap-impl",
            "run_contract_ref": "artifacts/test/run-contract.json",
            "run_contract_sha256": "a" * 64,
            "run_contract_digest": "b" * 64,
            "plan_revision": "map-g2",
            "task_map_generation": "map-g2",
            "required_ports": ["task_map"],
            "produced": [{
                "logical_name": "task_map",
                "artifact_kind": "task_map",
                "schema_version": "task-map.v1",
                "producer_stage_id": "gap-impl",
                "ref": task_map_descriptor["ref"],
                "sha256": task_map_descriptor["sha256"],
            }],
            "inherited": [],
        },
        kind="plan_artifact_package",
        schema_version="plan-artifact-package.v1",
        created_by="test",
    )
    successor = Task(id="TASK-B", title="Task B")
    successor.contract.source_ref = "docs/gap.md"
    successor.contract.evidence_contract = {
        "workflow_run_id": "run-2",
        "supersedes_task_ids": ["TASK-A"],
        "source_refs": {
            "task_map_ref": task_map_descriptor["ref"],
            "task_map_generation": "map-g2",
            "plan_artifact_package_ref": package_descriptor["ref"],
            "plan_artifact_package_digest": package_descriptor["sha256"],
        },
    }
    TaskStore(state_dir / "kanban.json").add(successor)
    rebuilder = CandidateRebuilder(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=log,
    )

    assert _successor_contract_binds_base(
        rebuilder,
        task_id="TASK-B",
        expected_source_ref=f"git:{base_commit}",
        supersedes_task_ids=["TASK-A"],
    )


def test_incremental_integration_ignores_untracked_provisioned_environment(
    tmp_path: Path,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(
        tmp_path,
        provision_node_modules=True,
    )

    result = _integrate(tmp_path, state_dir, config, log, writer, base)
    candidate_worktree = state_dir / "candidates" / "F-11111111" / "worktree"

    assert (candidate_worktree / "node_modules").is_symlink()
    assert _git(candidate_worktree, "status", "--porcelain") == "?? node_modules"
    assert result.status == "integrated"


def test_candidate_head_cas_conflict_never_overwrites_current_head(
    tmp_path: Path,
) -> None:
    _state_dir, _config, _log, _writer, base, task_commit = _setup(tmp_path)
    branch = "candidate/F-11111111"
    _update_candidate_ref(
        tmp_path,
        branch=branch,
        new_head=task_commit,
        expected_head=base,
        branch_exists=False,
    )

    with pytest.raises(
        CandidateIncrementalError,
        match="candidate_head_cas_mismatch",
    ):
        _update_candidate_ref(
            tmp_path,
            branch=branch,
            new_head=base,
            expected_head=base,
            branch_exists=True,
        )

    assert _git(tmp_path, "rev-parse", f"refs/heads/{branch}") == task_commit


def test_incremental_integration_fails_closed_without_marked_rolling_smoke(
    tmp_path: Path,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(
        tmp_path,
        rolling_smoke=False,
    )

    result = _integrate(tmp_path, state_dir, config, log, writer, base)
    events = log.read_all()

    assert result.status == "needs_review"
    assert [
        event for event in events
        if event.type == "integration.queue.needs_review"
    ]
    assert not [
        event for event in events
        if event.type in {
            "integration.queue.integrated",
            "candidate.updated",
            "candidate.integration.completed",
            "candidate.ready",
        }
    ]


def test_integration_receipt_drives_task_terminal_and_exact_candidate_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(tmp_path)
    generation = writer.append(ZfEvent(
        type="task.pipeline.generation.admitted",
        origin="kernel",
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "generation_id": "generation-1",
            "workflow_run_id": "run-1",
            "flow_kind": "issue",
            "request_kind": "issue",
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "fanout_id": "fanout-plan-1",
            "task_map_generation": "map-g1",
            "plan_artifact_package_id": "planpkg-1",
            "plan_artifact_package_ref": "artifacts/plan-packages/planpkg-1.json",
            "plan_artifact_package_digest": "planpkg-digest-1",
            "task_map_ref": "artifacts/plan/task-map.json",
            "task_map_digest": "task-map-digest-1",
            "source_index_ref": "artifacts/plan/source-index.json",
            "dispatch_base_commit": base,
            "task_ids": ["TASK-A"],
        },
        correlation_id="run-1",
    ))
    _integrate(tmp_path, state_dir, config, log, writer, base)
    sessions = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(tmp_path)
    )
    binding = sessions.bind_task_stage_session(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="impl",
        rework_affinity_id="map-g1:impl",
        role_instance="impl-1",
        role_config_digest="config-sha",
        workspace_generation=1,
        placement_epoch=1,
        backend="mock",
    )
    sessions.activate_task_stage_session(
        binding_key=binding["binding_key"], role_instance="impl-1"
    )
    context = {
        **generation.payload,
        "generation_admitted_event_id": generation.id,
    }
    terminated: list[str] = []
    role = SimpleNamespace(instance_id="impl-1")
    runtime = SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=log,
        event_writer=writer,
        task_store=TaskStore(state_dir / "kanban.json"),
        transport=SimpleNamespace(
            is_alive=lambda _instance: True,
            terminate=lambda instance: terminated.append(instance),
        ),
        _find_role_by_instance=lambda instance: (
            role if instance == role.instance_id else None
        ),
        _set_worker_state=lambda *_args, **_kwargs: None,
        _emit_role_lifecycle_event=lambda *_args, **_kwargs: None,
    )
    registry_loads = 0

    class CountingRoleSessionRegistry(RoleSessionRegistry):
        def __init__(self, *args, **kwargs):
            nonlocal registry_loads
            registry_loads += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        "zf.runtime.task_pipeline_terminal.RoleSessionRegistry",
        CountingRoleSessionRegistry,
    )

    terminal = reconcile_task_pipeline_terminals(
        runtime,
        generation_contexts={"TASK-A": context},
    )
    frozen = reconcile_task_pipeline_freeze(
        runtime,
        generation_contexts={"TASK-A": context},
    )
    replay_terminal = reconcile_task_pipeline_terminals(
        runtime,
        generation_contexts={"TASK-A": context},
    )
    replay_freeze = reconcile_task_pipeline_freeze(
        runtime,
        generation_contexts={"TASK-A": context},
    )
    events = log.read_all()

    assert [decision.action for decision in terminal] == [
        "task_pipeline_task_done",
        "task_pipeline_sessions_archived",
    ]
    assert [decision.action for decision in frozen] == [
        "task_pipeline_candidate_frozen"
    ]
    assert replay_terminal == []
    assert replay_freeze == []
    assert registry_loads == 1
    assert runtime.task_store.get("TASK-A").status == "done"
    assert len([event for event in events if event.type == "task.done"]) == 1
    archived = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(tmp_path)
    )
    assert archived.task_stage_binding(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="impl",
        rework_affinity_id="map-g1:impl",
    )["status"] == "archived"
    assert (
        archived.instance_meta()["impl-1"]["active_task_stage_binding_key"]
        == ""
    )
    assert str(archived.get("impl-1")) != binding["session_id"]
    assert terminated == ["impl-1"]
    assert len([
        event for event in events
        if event.type == "task.pipeline.sessions.archived"
    ]) == 1
    ready = [event for event in events if event.type == "candidate.ready"]
    assert len(ready) == 1
    assert ready[0].payload["commit"] == _git(
        tmp_path,
        "rev-parse",
        "refs/heads/candidate/F-11111111",
    )
    assert ready[0].payload["fanout_id"] == "fanout-plan-1"
    assert ready[0].payload["flow_kind"] == "issue"
    assert ready[0].payload["request_kind"] == "issue"
    assert ready[0].payload["pdd_id"] == "F-11111111"
    assert ready[0].payload["feature_id"] == "F-11111111"
    assert ready[0].payload["candidate_base_commit"] == base
    assert ready[0].payload["candidate_head_commit"] == ready[0].payload["commit"]
    assert ready[0].payload["diff_ref"] == (
        f"{base}..{ready[0].payload['commit']}"
    )
    assert ready[0].payload["completed_task_ids"] == ["TASK-A"]
    assert ready[0].payload["plan_artifact_package_id"] == "planpkg-1"
    assert ready[0].payload["task_map_ref"] == "artifacts/plan/task-map.json"
    assert ready[0].payload["source_index_ref"] == (
        "artifacts/plan/source-index.json"
    )
    freeze = hydrate_sidecar_ref(
        state_dir,
        ready[0].payload["freeze_receipt_ref"],
    ).payload
    assert freeze["schema_version"] == "candidate-freeze-receipt.v1"
    assert freeze["fanout_id"] == "fanout-plan-1"
    assert freeze["flow_kind"] == "issue"
    assert freeze["candidate_base_commit"] == base
    assert freeze["task_ids"] == ["TASK-A"]
    assert freeze["plan_artifact_package_id"] == "planpkg-1"
    assert freeze["task_map_ref"] == "artifacts/plan/task-map.json"
    assert freeze["source_index_ref"] == "artifacts/plan/source-index.json"


def test_candidate_freeze_requires_admitted_supersession_for_cancelled_task(
    tmp_path: Path,
) -> None:
    state_dir, config, log, writer, base, _ = _setup(tmp_path)
    task_store = TaskStore(state_dir / "kanban.json")
    task_store.add(Task(id="TASK-B", title="Task B"))
    writer.append(ZfEvent(type="task.created", task_id="TASK-B"))
    generation = writer.append(ZfEvent(
        type="task.pipeline.generation.admitted",
        origin="kernel",
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "generation_id": "generation-1",
            "workflow_run_id": "run-1",
            "flow_kind": "prd",
            "request_kind": "prd",
            "pdd_id": "F-11111111",
            "feature_id": "F-11111111",
            "fanout_id": "fanout-plan-1",
            "task_map_generation": "map-g1",
            "task_map_ref": "artifacts/plan/task-map.json",
            "dispatch_base_commit": base,
            "task_ids": ["TASK-A", "TASK-B"],
        },
        correlation_id="run-1",
    ))
    task_store.update(
        "TASK-B",
        status="cancelled",
        blocked_reason="superseded by amended Task Map",
    )
    superseded = writer.append(ZfEvent(
        type="task.superseded",
        actor="zf-cli",
        origin="kernel",
        task_id="TASK-B",
        payload={
            "source": "writer_task_map_adoption",
            "superseded_by_task_map_ref": (
                ".zf/artifacts/plan/amended-task-map.json"
            ),
            "superseded_task_ids": ["TASK-B"],
            "status": "cancelled",
        },
        correlation_id="run-1",
    ))
    _integrate(tmp_path, state_dir, config, log, writer, base)
    sessions = RoleSessionRegistry(
        state_dir / "role_sessions.yaml", project_root=str(tmp_path)
    )
    binding = sessions.bind_task_stage_session(
        workflow_run_id="run-1",
        task_id="TASK-B",
        stage="impl",
        rework_affinity_id="map-g1:impl",
        role_instance="impl-b",
        role_config_digest="config-sha",
        workspace_generation=1,
        placement_epoch=1,
        backend="mock",
    )
    sessions.activate_task_stage_session(
        binding_key=binding["binding_key"], role_instance="impl-b"
    )
    context = {
        **generation.payload,
        "generation_admitted_event_id": generation.id,
    }
    terminated: list[str] = []
    role = SimpleNamespace(instance_id="impl-b")
    runtime = SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        config=config,
        event_log=log,
        event_writer=writer,
        task_store=task_store,
        transport=SimpleNamespace(
            is_alive=lambda _instance: True,
            terminate=lambda instance: terminated.append(instance),
        ),
        _find_role_by_instance=lambda instance: (
            role if instance == role.instance_id else None
        ),
        _set_worker_state=lambda *_args, **_kwargs: None,
        _emit_role_lifecycle_event=lambda *_args, **_kwargs: None,
    )

    reconcile_task_pipeline_terminals(
        runtime,
        generation_contexts={"TASK-A": context, "TASK-B": context},
    )
    assert reconcile_task_pipeline_freeze(
        runtime,
        generation_contexts={"TASK-A": context, "TASK-B": context},
    ) == []
    assert sessions.task_stage_binding(
        workflow_run_id="run-1",
        task_id="TASK-B",
        stage="impl",
        rework_affinity_id="map-g1:impl",
    )["status"] == "active"

    writer.append(ZfEvent(
        type="task.pipeline.generation.admitted",
        origin="kernel",
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "generation_id": "generation-2",
            "workflow_run_id": "run-1",
            "task_map_generation": "map-g2",
            "task_map_ref": "artifacts/plan/amended-task-map.json",
            "dispatch_base_commit": base,
            "task_ids": ["TASK-C"],
        },
        correlation_id="run-1",
    ))
    archived = reconcile_task_pipeline_terminals(
        runtime,
        generation_contexts={"TASK-A": context, "TASK-B": context},
    )
    frozen = reconcile_task_pipeline_freeze(
        runtime,
        generation_contexts={"TASK-A": context, "TASK-B": context},
    )

    assert [decision.action for decision in archived] == [
        "task_pipeline_sessions_archived"
    ]
    assert frozen == []
    assert terminated == ["impl-b"]
    ready = [event for event in log.read_all() if event.type == "candidate.ready"]
    assert ready == []
    archived_event = next(
        event for event in log.read_all()
        if event.type == "task.pipeline.sessions.archived"
        and event.task_id == "TASK-B"
    )
    assert archived_event.causation_id == superseded.id


@pytest.mark.parametrize(
    ("alive_after_failure", "released", "failure_count"),
    [(False, True, 0), (True, False, 1)],
)
def test_archived_slot_release_reprobes_after_safe_terminate_rejection(
    tmp_path: Path,
    alive_after_failure: bool,
    released: bool,
    failure_count: int,
) -> None:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    writer = EventWriter(log)
    registry = RoleSessionRegistry(
        state_dir / "role_sessions.yaml",
        project_root=str(tmp_path),
    )
    binding = registry.bind_task_stage_session(
        workflow_run_id="run-1",
        task_id="TASK-A",
        stage="verify",
        rework_affinity_id="map-g1:verify",
        role_instance="verify-1",
        role_config_digest="config-sha",
        workspace_generation=1,
        placement_epoch=1,
        backend="mock",
    )
    registry.activate_task_stage_session(
        binding_key=binding["binding_key"],
        role_instance="verify-1",
    )
    probes = iter((True, alive_after_failure))

    class _Transport:
        def is_alive(self, _instance: str) -> bool:
            return next(probes)

        def terminate(self, _instance: str) -> None:
            raise TmuxError("target no longer proves role ownership")

    runtime = SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        event_writer=writer,
        transport=_Transport(),
        _find_role_by_instance=lambda instance: (
            SimpleNamespace(instance_id=instance)
        ),
        _set_worker_state=lambda *_args, **_kwargs: None,
        _emit_role_lifecycle_event=lambda *_args, **_kwargs: None,
    )

    assert _release_archived_task_stage_slot(
        runtime,
        registry=registry,
        role_instance="verify-1",
        binding_key=binding["binding_key"],
        task_id="TASK-A",
    ) is released
    failures = [
        event for event in log.read_all()
        if event.type == "kernel.housekeeping.failed"
    ]
    assert len(failures) == failure_count
    active_key = str(
        RoleSessionRegistry(
            state_dir / "role_sessions.yaml",
            project_root=str(tmp_path),
        ).instance_meta().get("verify-1", {}).get(
            "active_task_stage_binding_key"
        ) or ""
    )
    assert (active_key == "") is released
