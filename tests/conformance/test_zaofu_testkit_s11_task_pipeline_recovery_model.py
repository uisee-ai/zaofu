"""Generated Task Pipeline recovery/replay model.

The model keeps operation identity stable across a mechanical redrive while
fencing old task-map generations, workflow runs and semantic generations.  The
last test exercises the real Task Pipeline recovery reconciler with a persisted
TaskAttempt and Run Manager respawn receipt.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from hypothesis import given, settings, strategies as st

from tests.testkit.model_evidence import ModelEvidence
from zf.core.config.schema import (
    ProjectConfig,
    WorkflowConfig,
    WorkflowTaskAttemptConfig,
    ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.events.writer import EventWriter
from zf.core.state.task_attempts import (
    TASK_ATTEMPT_IDENTITY_OPERATION_V2,
    TaskAttemptStore,
)
from zf.runtime.task_attempt_runtime import reconcile_task_attempts
from zf.runtime.task_pipeline_contexts import task_pipeline_generation_contexts
from zf.runtime.task_pipeline_recovery import reconcile_task_pipeline_redrives
from zf.runtime.task_pipeline_runtime_selection import operation_matches_generation
from zf.runtime.workflow_operation import (
    WorkflowOperationService,
    reduce_workflow_operations,
)


_MODEL_SETTINGS = settings(
    max_examples=20,
    deadline=None,
    database=None,
    derandomize=True,
)


@st.composite
def _generation_history(draw: st.DrawFn) -> list[tuple[str, str, str, int]]:
    return draw(st.lists(
        st.tuples(
            st.sampled_from(["run-a", "run-b"]),
            st.sampled_from(["TASK-A", "TASK-B"]),
            st.sampled_from(["map-1", "map-2", "map-3"]),
            st.integers(min_value=1, max_value=3),
        ),
        min_size=1,
        max_size=8,
    ))


def _generation_event(index: int, run_id: str, task_id: str, map_generation: str) -> ZfEvent:
    return ZfEvent(
        id=f"generation-{index}",
        type="task.pipeline.generation.admitted",
        actor="zf-cli",
        correlation_id=run_id,
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "workflow_run_id": run_id,
            "task_ids": [task_id],
            "task_map_generation": map_generation,
            "generation_id": f"{run_id}-{map_generation}",
        },
    )


@_MODEL_SETTINGS
@given(_generation_history())
def test_generated_recovery_model_fences_old_runs_maps_and_generations(
    generated: list[tuple[str, str, str, int]],
) -> None:
    events: list[ZfEvent] = []
    latest_by_task: dict[str, tuple[str, str, int]] = {}
    for index, (run_id, task_id, map_generation, operation_generation) in enumerate(generated):
        events.append(_generation_event(index, run_id, task_id, map_generation))
        latest_by_task[task_id] = (run_id, map_generation, operation_generation)
        operation_id = f"op-{run_id}-{task_id}-{operation_generation}-{index}"
        request_hash = f"hash-{index}"
        events.extend([
            ZfEvent(
                id=f"request-{index}",
                type="workflow.operation.requested",
                actor="zf-cli",
                task_id=task_id,
                payload={
                    "workflow_run_id": run_id,
                    "operation_id": operation_id,
                    "request_hash": request_hash,
                    "task_id": task_id,
                    "task_pipeline_stage": "impl",
                    "operation_generation": operation_generation,
                    "task_map_generation": map_generation,
                },
            ),
        ])

    contexts = task_pipeline_generation_contexts(events)
    operations = reduce_workflow_operations(events)
    selected = [
        operation
        for operation in operations.values()
        if operation_matches_generation(operation, contexts)
    ]
    expected_scopes = {
        (task_id, run_id, map_generation)
        for task_id, (run_id, map_generation, _operation_generation) in latest_by_task.items()
    }
    selected_scopes = {
        (
            str(operation["task_id"]),
            str(operation["workflow_run_id"]),
            str(operation["task_map_generation"]),
        )
        for operation in selected
    }
    assert selected_scopes == expected_scopes
    for task_id, (run_id, _map_generation, _operation_generation) in latest_by_task.items():
        assert any(
            operation["task_id"] == task_id
            and operation["workflow_run_id"] == run_id
            for operation in selected
        )
    with TemporaryDirectory(prefix="zf-task-pipeline-recovery-model-") as root:
        evidence = ModelEvidence(
            root=Path(root),
            model_name="TaskPipelineRecoveryGenerationFence",
            source=Path(__file__),
            settings=str(_MODEL_SETTINGS),
            reference=lambda: {"generated": generated},
        )
        with evidence.guard("generation_fence", {"generated": generated}):
            assert all(operation["task_pipeline_stage"] == "impl" for operation in selected)


def _runtime(tmp_path: Path) -> SimpleNamespace:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    event_log = EventLog(state_dir / "events.jsonl")
    return SimpleNamespace(
        state_dir=state_dir,
        project_root=tmp_path,
        config=ZfConfig(
            project=ProjectConfig(name="task-pipeline-recovery-model"),
            workflow=WorkflowConfig(
                task_attempt=WorkflowTaskAttemptConfig(mode="enforce", max_attempts=3),
            ),
        ),
        event_log=event_log,
        event_writer=EventWriter(event_log),
    )


def test_real_recovery_caller_reopens_same_operation_once_and_preserves_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _runtime(tmp_path)
    service = WorkflowOperationService(
        state_dir=runtime.state_dir,
        event_log=runtime.event_log,
        event_writer=runtime.event_writer,
    )
    ensured = service.ensure_operation(
        workflow_run_id="run-1",
        operation_id="op-TASK-A-impl-g2",
        operation_type="task-stage",
        request={
            "task_pipeline_stage": "impl",
            "operation_generation": 2,
            "task_map_generation": "map-2",
            "prompt": "implement",
        },
        task_id="TASK-A",
    )
    attempt = TaskAttemptStore(runtime.state_dir / "task_attempts.json").ensure_for_dispatch(
        run_id="run-1",
        task_id="TASK-A",
        dispatch_id="dispatch-1",
        role="impl",
        instance_id="impl-1",
        operation_id="op-TASK-A-impl-g2",
        briefing_ref="briefings/TASK-A.md",
        created_at="2026-08-03T00:00:00+00:00",
        lease_expires_at="2026-08-03T00:01:00+00:00",
        max_attempts=3,
        identity_version=TASK_ATTEMPT_IDENTITY_OPERATION_V2,
        placement_epoch=1,
    ).attempt
    service.mark_started(
        operation_id=ensured.operation_id,
        request_hash=ensured.request_hash,
        workflow_run_id="run-1",
        task_id="TASK-A",
        dispatch_id="dispatch-1",
        role_instance="impl-1",
        active_attempt_id=attempt["attempt_id"],
        lease_id=attempt["lease_id"],
    )
    monkeypatch.setattr("zf.runtime.task_attempt_runtime._expired", lambda _value: True)
    assert reconcile_task_attempts(runtime) >= 1
    expired = TaskAttemptStore(runtime.state_dir / "task_attempts.json").get(attempt["attempt_id"])
    assert expired is not None and expired["recovery_owner"] == "run_manager"

    request = runtime.event_writer.append(ZfEvent(
        type="worker.respawn.requested",
        actor="run-manager",
        task_id="TASK-A",
        payload={
            "operation_id": ensured.operation_id,
            "attempt_id": attempt["attempt_id"],
            "recovery_decision_owner": "run_manager",
            "recovery_effect_owner": "workflow_runtime_coordinator",
        },
    ))
    runtime.event_writer.append(ZfEvent(
        type="worker.respawn.completed",
        actor="impl-1",
        task_id="TASK-A",
        causation_id=request.id,
        payload={"reason": "respawned"},
    ))
    contexts = {
        "TASK-A": {
            "workflow_run_id": "run-1",
            "task_map_generation": "map-2",
        },
    }
    first = reconcile_task_pipeline_redrives(runtime, generation_contexts=contexts)
    second = reconcile_task_pipeline_redrives(runtime, generation_contexts=contexts)
    assert [decision.action for decision in first] == [
        "task_pipeline_operation_redrive_admitted"
    ]
    assert second == []
    operation = reduce_workflow_operations(runtime.event_log.read_all())[ensured.operation_id]
    assert operation["status"] == "requested"
    assert operation["operation_generation"] == 2
    assert operation["task_map_generation"] == "map-2"
    assert operation["redrive_count"] == 1
    assert operation["redrive_source_attempt_ids"] == [attempt["attempt_id"]]
