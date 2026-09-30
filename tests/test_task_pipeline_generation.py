from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.schema import RoleConfig, RoleLifecycleConfig
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.runtime.task_pipeline_dispatch import dispatch_task_pipeline_stage
from zf.runtime.task_pipeline_identity import task_pipeline_operation_identity
from zf.runtime.task_pipeline_fanout import (
    suppress_admitted_blocking_task_pipeline_generation,
    task_pipeline_enabled,
)
from zf.runtime.task_pipeline_reconciler import (
    task_pipeline_any_blocking,
    task_pipeline_policy,
)
from zf.runtime.task_pipeline_runtime import (
    TaskPipelineWaiting,
    TaskPipelineRuntimeError,
    _ensure_task_pipeline_role_ready,
    admit_task_pipeline_generation,
    preflight_task_pipeline_generation,
)
from zf.runtime.task_pipeline_terminal import task_pipeline_workspace_base


_PACKAGE_ID = "planpkg-" + "b" * 64
_PACKAGE_REF = "artifacts/plan-packages/" + "b" * 64 + ".json"
_PACKAGE_DIGEST = "b" * 64


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _init_master_repo(root: Path) -> str:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    _git(root, "branch", "-M", "master")
    return _git(root, "rev-parse", "HEAD")


def _commit_file(root: Path, relative: str, content: str, message: str) -> str:
    (root / relative).write_text(content, encoding="utf-8")
    _git(root, "add", relative)
    _git(root, "commit", "-q", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _workspace_base_runtime(
    project_root: Path,
    *,
    candidate_head: str,
    candidate_generation: str = "map-new",
) -> SimpleNamespace:
    event = ZfEvent(
        type="candidate.updated",
        payload={
            "incremental": True,
            "workflow_run_id": "run-1",
            "task_map_generation": candidate_generation,
            "candidate_head": candidate_head,
        },
    )
    return SimpleNamespace(
        project_root=project_root,
        event_log=SimpleNamespace(read_all=lambda: [event]),
    )


def test_workspace_base_uses_newer_candidate_across_task_map_generations(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    dispatch_base = _init_master_repo(project_root)
    candidate_head = _commit_file(
        project_root,
        "candidate.txt",
        "candidate\n",
        "candidate",
    )
    runtime = _workspace_base_runtime(
        project_root,
        candidate_head=candidate_head,
    )

    selected = task_pipeline_workspace_base(
        runtime,
        task=SimpleNamespace(id="TASK-LATE"),
        generation_context={
            "workflow_run_id": "run-1",
            "task_map_generation": "map-old",
            "dispatch_base_commit": dispatch_base,
        },
    )

    assert selected == candidate_head


def test_workspace_base_preserves_newer_successor_dispatch_base(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _init_master_repo(project_root)
    candidate_head = _commit_file(
        project_root,
        "candidate.txt",
        "candidate\n",
        "candidate",
    )
    successor_base = _commit_file(
        project_root,
        "successor.txt",
        "successor\n",
        "successor",
    )
    runtime = _workspace_base_runtime(
        project_root,
        candidate_head=candidate_head,
    )

    selected = task_pipeline_workspace_base(
        runtime,
        task=SimpleNamespace(id="TASK-SUCCESSOR"),
        generation_context={
            "workflow_run_id": "run-1",
            "task_map_generation": "map-new",
            "dispatch_base_commit": successor_base,
        },
    )

    assert selected == successor_base


def test_workspace_base_rejects_divergent_candidate_history(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    root_head = _init_master_repo(project_root)
    candidate_head = _commit_file(
        project_root,
        "candidate.txt",
        "candidate\n",
        "candidate",
    )
    _git(project_root, "checkout", "-q", "-b", "successor", root_head)
    divergent_base = _commit_file(
        project_root,
        "successor.txt",
        "successor\n",
        "successor",
    )
    runtime = _workspace_base_runtime(
        project_root,
        candidate_head=candidate_head,
    )

    with pytest.raises(TaskPipelineRuntimeError, match="base diverged"):
        task_pipeline_workspace_base(
            runtime,
            task=SimpleNamespace(id="TASK-DIVERGED"),
            generation_context={
                "workflow_run_id": "run-1",
                "task_map_generation": "map-new",
                "dispatch_base_commit": divergent_base,
            },
        )


def test_provider_readiness_is_waiting_not_plan_admission_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _ReadinessError(RuntimeError):
        failure_class = "provider_launch_not_submitted"

    runtime = SimpleNamespace(
        _ensure_role_active=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _ReadinessError("provider readiness failed: respawning")
        )
    )
    monkeypatch.setattr(
        "zf.runtime.transport.transport_error_diagnostics",
        lambda exc: {"failure_class": exc.failure_class},
    )

    with pytest.raises(TaskPipelineWaiting) as caught:
        _ensure_task_pipeline_role_ready(
            runtime,
            SimpleNamespace(instance_id="issue-fix-lane-0"),
            task_id="ISSUE-READY-1",
            spawn_cwd=tmp_path,
            skill_runtime_root=tmp_path,
        )

    assert caught.value.reason == "waiting_for_provider_readiness"
    assert "respawning" in caught.value.detail


def test_non_readiness_role_activation_error_stays_fail_closed(
    tmp_path: Path,
) -> None:
    class _ContractError(RuntimeError):
        pass

    runtime = SimpleNamespace(
        _ensure_role_active=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _ContractError("workspace contract is stale")
        )
    )
    with pytest.raises(_ContractError, match="workspace contract is stale"):
        _ensure_task_pipeline_role_ready(
            runtime,
            SimpleNamespace(instance_id="issue-fix-lane-0"),
            task_id="ISSUE-CONTRACT-1",
            spawn_cwd=tmp_path,
            skill_runtime_root=tmp_path,
        )


def _runtime(project_root: Path, *, base_ref: str) -> SimpleNamespace:
    return SimpleNamespace(
        project_root=project_root,
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={
                    "task_pipeline": {
                        "mode": "blocking",
                        "profile_id": "test-v4",
                        "profile_digest": "a" * 64,
                    }
                },
                flow_metadata_by_kind={},
            ),
            runtime=SimpleNamespace(
                git=SimpleNamespace(candidate_base_ref=base_ref),
            ),
        ),
    )


def _preflight(runtime: SimpleNamespace):
    return preflight_task_pipeline_generation(
        runtime,
        trigger_event=ZfEvent(
            type="task_map.ready",
            correlation_id="run-1",
            payload={"task_map_generation": "map-g1"},
        ),
        trace_id="run-1",
        loaded=SimpleNamespace(
            workflow_run_id="run-1",
            task_map_generation="map-g1",
            dispatch_base_commit="",
            plan_artifact_package_id=_PACKAGE_ID,
            plan_artifact_package_ref=_PACKAGE_REF,
            plan_artifact_package_digest=_PACKAGE_DIGEST,
        ),
        task_items=[{"task_id": "TASK-1"}],
    )


def test_default_main_base_freezes_master_head_as_exact_commit(
    tmp_path: Path,
) -> None:
    head = _init_master_repo(tmp_path / "project")

    prepared = _preflight(_runtime(tmp_path / "project", base_ref="main"))

    assert prepared is not None
    assert prepared.dispatch_base_commit == head
    assert prepared.plan_artifact_package_id == _PACKAGE_ID
    assert prepared.plan_artifact_package_ref == _PACKAGE_REF
    assert prepared.plan_artifact_package_digest == _PACKAGE_DIGEST


def test_multi_flow_config_selects_prd_pipeline_without_local_main(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    head = _init_master_repo(project_root)
    policies = {
        "issue": {
            "task_pipeline": {
                "mode": "blocking",
                "profile_id": "issue-v4",
                "profile_digest": "1" * 64,
            }
        },
        "prd": {
            "task_pipeline": {
                "mode": "blocking",
                "profile_id": "prd-v4",
                "profile_digest": "2" * 64,
            }
        },
        "refactor": {
            "task_pipeline": {
                "mode": "blocking",
                "profile_id": "refactor-v4",
                "profile_digest": "3" * 64,
            }
        },
    }
    runtime = SimpleNamespace(
        project_root=project_root,
        config=SimpleNamespace(
            workflow=SimpleNamespace(
                flow_metadata={},
                flow_metadata_by_kind=policies,
            ),
            runtime=SimpleNamespace(
                git=SimpleNamespace(candidate_base_ref="main"),
            ),
        ),
    )
    trigger = ZfEvent(
        type="task_map.ready",
        correlation_id="run-prd",
        payload={
            "workflow_run_id": "run-prd",
            "task_map_generation": "map-prd",
            "flow_kind": "prd",
        },
    )
    loaded = SimpleNamespace(
        flow_kind="prd",
        workflow_run_id="run-prd",
        task_map_generation="map-prd",
        dispatch_base_commit="",
        plan_artifact_package_id=_PACKAGE_ID,
        plan_artifact_package_ref=_PACKAGE_REF,
        plan_artifact_package_digest=_PACKAGE_DIGEST,
    )

    prepared = preflight_task_pipeline_generation(
        runtime,
        trigger_event=trigger,
        trace_id="run-prd",
        loaded=loaded,
        task_items=[{"task_id": "TASK-PRD"}],
    )

    assert task_pipeline_policy(runtime.config) is None
    assert task_pipeline_any_blocking(runtime.config) is True
    assert task_pipeline_enabled(runtime, flow_kind="prd") is True
    assert prepared is not None
    assert prepared.profile_id == "prd-v4"
    assert prepared.profile_digest == "2" * 64
    assert prepared.dispatch_base_commit == head


def test_explicit_missing_base_fails_preflight(tmp_path: Path) -> None:
    _init_master_repo(tmp_path / "project")

    with pytest.raises(TaskPipelineRuntimeError, match="missing-baseline"):
        _preflight(_runtime(tmp_path / "project", base_ref="missing-baseline"))


def test_missing_plan_package_identity_fails_generation_preflight(
    tmp_path: Path,
) -> None:
    _init_master_repo(tmp_path / "project")
    runtime = _runtime(tmp_path / "project", base_ref="main")

    with pytest.raises(
        TaskPipelineRuntimeError,
        match="requires Plan Artifact Package identity",
    ):
        preflight_task_pipeline_generation(
            runtime,
            trigger_event=ZfEvent(
                type="task_map.ready",
                correlation_id="run-1",
                payload={"task_map_generation": "map-g1"},
            ),
            trace_id="run-1",
            loaded=SimpleNamespace(
                workflow_run_id="run-1",
                task_map_generation="map-g1",
                dispatch_base_commit="",
            ),
            task_items=[{"task_id": "TASK-1"}],
        )


def test_generation_admission_persists_plan_package_identity(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _init_master_repo(project_root)
    runtime = _runtime(project_root, base_ref="main")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    runtime.event_log = EventLog(state_dir / "events.jsonl")
    runtime.event_writer = EventWriter(runtime.event_log)
    loaded = SimpleNamespace(
        workflow_run_id="run-1",
        task_map_generation="map-g1",
        dispatch_base_commit="",
        task_map_ref="artifacts/plan/task-map.json",
        source_index_ref="artifacts/plan/source-index.json",
        plan_artifact_package_id=_PACKAGE_ID,
        plan_artifact_package_ref=_PACKAGE_REF,
        plan_artifact_package_digest=_PACKAGE_DIGEST,
    )
    trigger = ZfEvent(
        type="task_map.ready",
        correlation_id="run-1",
        payload={
            "task_map_generation": "map-g1",
            "fanout_id": "fanout-plan-1",
            "flow_kind": "issue",
            "request_kind": "issue",
            "pdd_id": "ISSUE-1",
            "feature_id": "ISSUE-1",
        },
    )
    admitted = ZfEvent(
        type="task_map.admitted",
        correlation_id="run-1",
        payload={"task_map_digest": "c" * 64},
    )

    event = admit_task_pipeline_generation(
        runtime,
        trigger_event=trigger,
        task_map_admitted_event=admitted,
        stage_id="impl",
        trace_id="run-1",
        loaded=loaded,
        task_items=[{"task_id": "TASK-1"}],
    )

    assert event is not None
    assert event.payload["plan_artifact_package_id"] == _PACKAGE_ID
    assert event.payload["plan_artifact_package_ref"] == _PACKAGE_REF
    assert event.payload["plan_artifact_package_digest"] == _PACKAGE_DIGEST
    assert event.payload["fanout_id"] == "fanout-plan-1"
    assert event.payload["flow_kind"] == "issue"
    assert event.payload["request_kind"] == "issue"
    assert event.payload["pdd_id"] == "ISSUE-1"
    assert event.payload["feature_id"] == "ISSUE-1"
    assert event.payload["dispatch_base_commit"] == _git(
        project_root,
        "rev-parse",
        "HEAD",
    )


def test_equivalent_generation_replay_is_suppressed_before_writer_admission(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    _init_master_repo(project_root)
    runtime = _runtime(project_root, base_ref="main")
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    runtime.event_log = EventLog(state_dir / "events.jsonl")
    runtime.event_writer = EventWriter(runtime.event_log)
    prepared = _preflight(runtime)
    assert prepared is not None
    runtime.event_writer.append(ZfEvent(
        type="task.pipeline.generation.admitted",
        origin="kernel",
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "generation_id": prepared.generation_id,
            "workflow_run_id": prepared.workflow_run_id,
            "task_map_generation": prepared.task_map_generation,
            "task_ids": list(prepared.task_ids),
        },
    ))
    replay = ZfEvent(
        id="evt-task-map-replay",
        type="task_map.ready",
        correlation_id="run-1",
        payload={"task_map_generation": "map-g1"},
    )

    first = suppress_admitted_blocking_task_pipeline_generation(
        runtime,
        trigger_event=replay,
        stage_id="impl",
        preflight=prepared,
        correlation_id="run-1",
    )
    second = suppress_admitted_blocking_task_pipeline_generation(
        runtime,
        trigger_event=replay,
        stage_id="impl",
        preflight=prepared,
        correlation_id="run-1",
    )

    assert first is True
    assert second is True
    suppressed = [
        event
        for event in runtime.event_log.read_all()
        if event.type == "fanout.retrigger.suppressed"
    ]
    assert len(suppressed) == 1
    assert suppressed[0].payload["reason"] == (
        "task_pipeline_generation_already_admitted"
    )


def test_stage_dispatch_carries_admitted_plan_package_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    expected_operation = task_pipeline_operation_identity(
        workflow_run_id="run-1",
        task_id="TASK-1",
        task_map_generation="map-g1",
        stage="impl",
        stage_revision="implementation-result.v1",
        operation_generation=1,
    )
    captured: dict[str, object] = {}

    class _WorkspaceManager:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def prepare(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                enabled=True,
                mode="worktree",
                project_path=str(project_root),
                workdir=str(workspace_root),
                branch="writer/task-1",
                base_commit="d" * 40,
            )

    def _prepare_call_operation(
        _runtime: object,
        *,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> SimpleNamespace:
        captured.update(payload)
        return SimpleNamespace(
            operation_id=expected_operation.operation_id,
            should_dispatch=False,
        )

    monkeypatch.setattr(
        "zf.runtime.task_workspaces.TaskWorkspaceManager",
        _WorkspaceManager,
    )
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_terminal.task_pipeline_workspace_base",
        lambda *_args, **_kwargs: "d" * 40,
    )
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_runtime._activate_task_stage_binding",
        lambda *_args, **_kwargs: {"binding_key": "binding-1"},
    )
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_runtime._role_config_digest",
        lambda *_args, **_kwargs: "role-config-digest",
    )
    monkeypatch.setattr(
        "zf.runtime.task_pipeline_targets.prepare_contract_snapshot",
        lambda *_args, **_kwargs: (
            {
                "task_ref": "refs/zf/tasks/TASK-1",
                "contract_revision": "contract-r1",
                "allowed_paths": ["app/**"],
                "plan_artifact_package_id": _PACKAGE_ID,
                "plan_artifact_package_ref": _PACKAGE_REF,
                "plan_artifact_package_digest": _PACKAGE_DIGEST,
            },
            {
                "ref": "artifacts/task-contracts/task-1.json",
                "sha256": "e" * 64,
            },
        ),
    )
    monkeypatch.setattr(
        "zf.runtime.call_result_runtime.prepare_call_operation",
        _prepare_call_operation,
    )
    role = RoleConfig(
        instance_id="fix-lane-0",
        name="fix",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
        role_kind="writer",
        skills=["base-worker"],
        backend="claude-code",
    )
    runtime = SimpleNamespace(
        state_dir=tmp_path / ".zf",
        project_root=project_root,
        config=SimpleNamespace(),
        _find_role_by_instance=lambda _value: role,
    )

    result = dispatch_task_pipeline_stage(
        runtime,
        policy={},
        task=SimpleNamespace(
            id="TASK-1",
            title="fix",
            skills_required=["can-domain"],
            contract=SimpleNamespace(),
        ),
        assignment={
            "stage": "impl",
            "role_instance": "fix-lane-0",
            "operation_generation": 1,
        },
        generation_context={
            "workflow_run_id": "run-1",
            "task_map_generation": "map-g1",
            "task_map_ref": "artifacts/plan/task-map.json",
            "source_index_ref": "artifacts/plan/source-index.json",
            "generation_admitted_event_id": "evt-generation",
            "plan_artifact_package_id": _PACKAGE_ID,
            "plan_artifact_package_ref": _PACKAGE_REF,
            "plan_artifact_package_digest": _PACKAGE_DIGEST,
        },
        operation_rows=[],
        attempt_rows=[],
    )

    assert result is None
    assert captured["plan_artifact_package_id"] == _PACKAGE_ID
    assert captured["plan_artifact_package_ref"] == _PACKAGE_REF
    assert captured["skills"] == ["base-worker", "can-domain"]
    assert captured["plan_artifact_package_digest"] == _PACKAGE_DIGEST
    assert captured["task_contract_snapshot_ref"] == (
        "artifacts/task-contracts/task-1.json"
    )
    assert captured["task_contract_snapshot_digest"] == "e" * 64
    assert captured["contract_snapshot_ref"] == captured[
        "task_contract_snapshot_ref"
    ]
    assert captured["contract_snapshot_digest"] == captured[
        "task_contract_snapshot_digest"
    ]
