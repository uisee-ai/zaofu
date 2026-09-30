"""Shared isolated admission fixture for blocking Task Pipeline tests."""

from pathlib import Path

import pytest

from tests.test_plan_artifact_package import _package
from tests.test_scheduler_task_attempt import _RecordingTransport
from tests.test_task_pipeline_generation import _init_master_repo
from tests.test_task_pipeline_reconciler import _policy
from zf.core.config.schema import (
    ProjectConfig, RoleConfig, RoleLifecycleConfig, RuntimeConfig,
    WorkdirConfig, WorkflowConfig, WorkflowTaskAttemptConfig, ZfConfig,
)
from zf.core.events.model import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task, TaskContract
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.plan_artifact_package import (
    package_event_payload, write_plan_artifact_package,
)


def _blocking_runtime_at(
    project: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Orchestrator, _RecordingTransport, ZfEvent]:
    head = _init_master_repo(project)
    state = project / "runtime-state"
    state.mkdir()
    SessionStore(state / "session.yaml").create(project_root=str(project))
    role = RoleConfig(
        name="impl", instance_id="impl-1", backend="mock", role_kind="writer",
        lifecycle=RoleLifecycleConfig(mode="on_demand"),
    )
    config = ZfConfig(
        project=ProjectConfig(name="blocking-dispatch", state_dir=str(state)),
        roles=[role],
        runtime=RuntimeConfig(workdirs=WorkdirConfig(enabled=True, mode="worktree")),
        workflow=WorkflowConfig(
            flow_metadata={
                "task_pipeline": {**_policy(), "mode": "blocking"},
                "result_protocol": {
                    "mode": "blocking",
                    "semantic_submit_profiles": {
                        "implementation": "blocking", "task-verify": "blocking",
                    },
                },
            },
            task_attempt=WorkflowTaskAttemptConfig(mode="enforce", max_attempts=3),
        ),
    )
    transport = _RecordingTransport(state)
    # No process exists: activation is explicitly outside this test's proof.
    monkeypatch.setattr(transport, "is_alive", lambda _instance: False)
    monkeypatch.setattr(Orchestrator, "_ensure_role_active", lambda *a, **kw: False)
    runtime = Orchestrator(state, config, transport, project_root=project)
    package = _package(state, generation="map-current")
    descriptor = write_plan_artifact_package(state, package)
    package_identity = {
        "plan_artifact_package_id": descriptor["package_id"],
        "plan_artifact_package_ref": descriptor["ref"],
        "plan_artifact_package_digest": descriptor["sha256"],
    }
    runtime.event_writer.append(ZfEvent(
        type="plan.artifact_package.admitted", correlation_id="run-1",
        payload=package_event_payload(package, descriptor, status="admitted"),
    ))
    task = Task(
        id="TASK-BLOCKING", title="test actual blocking dispatch",
        contract=TaskContract(
            scope=["app/**"], acceptance_criteria=["the bounded fixture is handled"],
            verification="true",
            evidence_contract={
                "workflow_run_id": "run-1", "task_map_generation": "map-current",
                "source_refs": package_identity,
            },
        ),
    )
    runtime.task_store.add(task)
    admission = runtime.event_writer.append(ZfEvent(
        type="task.pipeline.generation.admitted", correlation_id="run-1",
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "workflow_run_id": "run-1", "generation_id": "generation-current",
            "task_map_generation": "map-current", "task_ids": [task.id],
            "dispatch_base_commit": head, **package_identity,
        },
    ))
    return runtime, transport, admission
