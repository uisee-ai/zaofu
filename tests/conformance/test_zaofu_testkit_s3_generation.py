from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.flow_verification_identity import (
    latest_flow_verification_for_candidate,
)
from zf.runtime.task_pipeline_contexts import (
    task_pipeline_generation_contexts,
    task_pipeline_generation_is_current,
)
from zf.runtime.task_pipeline_runtime_selection import operation_matches_generation
from zf.runtime import task_pipeline_runtime


def _generation(
    event_id: str,
    *,
    run_id: str = "run-1",
    generation: str = "generation-1",
    task_ids: list[str] | None = None,
) -> ZfEvent:
    return ZfEvent(
        id=event_id,
        type="task.pipeline.generation.admitted",
        correlation_id=run_id,
        payload={
            "schema_version": "task-pipeline-generation.v1",
            "workflow_run_id": run_id,
            "generation_id": generation,
            "task_map_generation": generation,
            "task_ids": task_ids or ["TASK-1"],
        },
    )


def test_generation_currentness_is_scoped_to_run_and_latest_admission() -> None:
    events = [
        _generation("old", generation="generation-old"),
        _generation("new", generation="generation-new"),
        _generation("other-run", run_id="run-2", generation="generation-other"),
    ]

    assert task_pipeline_generation_is_current(
        events, workflow_run_id="run-1", generation_id="generation-new"
    )
    assert not task_pipeline_generation_is_current(
        events, workflow_run_id="run-1", generation_id="generation-old"
    )
    assert not task_pipeline_generation_is_current(
        events, workflow_run_id="run-2", generation_id="generation-new"
    )


def test_invalid_later_generation_admission_cannot_shadow_valid_context() -> None:
    """Malformed admissions are ignored rather than becoming latest context."""
    events = [
        _generation("valid", generation="generation-valid"),
        ZfEvent(
            id="invalid-later",
            type="task.pipeline.generation.admitted",
            correlation_id="run-1",
            payload={
                "schema_version": "task-pipeline-generation.v0",
                "workflow_run_id": "run-1",
                "generation_id": "generation-invalid",
            },
        ),
    ]

    assert task_pipeline_generation_is_current(
        events,
        workflow_run_id="run-1",
        generation_id="generation-valid",
    )
    assert not task_pipeline_generation_is_current(
        events,
        workflow_run_id="run-1",
        generation_id="generation-invalid",
    )


def test_generation_context_projection_keeps_latest_context_per_task() -> None:
    events = [
        _generation("old", generation="generation-old", task_ids=["TASK-1"]),
        _generation(
            "new",
            generation="generation-new",
            task_ids=["TASK-1", "TASK-2"],
        ),
    ]

    contexts = task_pipeline_generation_contexts(events)

    assert contexts["TASK-1"]["generation_id"] == "generation-new"
    assert contexts["TASK-2"]["generation_id"] == "generation-new"
    assert contexts["TASK-1"]["generation_admitted_event_id"] == "new"


def test_operation_generation_selector_rejects_stale_run_and_generation() -> None:
    contexts = {
        "TASK-1": {
            "workflow_run_id": "run-1",
            "task_map_generation": "generation-new",
        }
    }

    base = {
        "task_id": "TASK-1",
        "workflow_run_id": "run-1",
        "task_map_generation": "generation-new",
        "task_pipeline_stage": "verify",
    }
    assert operation_matches_generation(base, contexts)
    assert not operation_matches_generation(
        {**base, "task_map_generation": "generation-old"}, contexts
    )
    assert not operation_matches_generation(
        {**base, "workflow_run_id": "run-2"}, contexts
    )
    assert not operation_matches_generation(
        {**base, "task_pipeline_stage": ""}, contexts
    )


@pytest.mark.parametrize(
    ("label", "changes", "expected"),
    [
        ("first-dispatch", {"operation_generation": 1}, True),
        ("retry-same-generation", {"operation_generation": 2}, True),
        ("resume-same-generation", {"operation_generation": 99}, True),
        ("stale-generation", {"task_map_generation": "generation-old"}, False),
        ("stale-run", {"workflow_run_id": "run-old"}, False),
        ("unknown-task", {"task_id": "TASK-UNKNOWN"}, False),
        ("missing-stage", {"task_pipeline_stage": ""}, False),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_dispatch_retry_resume_identity_matrix(
    label: str,
    changes: dict[str, object],
    expected: bool,
) -> None:
    """Retries may change operation generation, never the admitted identity."""

    del label
    contexts = {
        "TASK-1": {
            "workflow_run_id": "run-1",
            "task_map_generation": "generation-new",
        }
    }
    operation = {
        "task_id": "TASK-1",
        "workflow_run_id": "run-1",
        "task_map_generation": "generation-new",
        "task_pipeline_stage": "verify",
        "operation_generation": 1,
    }
    operation.update(changes)
    assert operation_matches_generation(operation, contexts) is expected


def test_candidate_verification_identity_rejects_stale_target_commit() -> None:
    candidate = ZfEvent(
        id="candidate-new",
        type="candidate.ready",
        payload={
            "workflow_run_id": "run-1",
            "pdd_id": "PDD-1",
            "task_map_generation": "generation-new",
            "candidate_ref": "candidate/PDD-1",
            "candidate_head_commit": "head-new",
        },
    )
    stale = ZfEvent(
        id="verify-stale",
        type="verify.passed",
        payload={
            "workflow_run_id": "run-1",
            "pdd_id": "PDD-1",
            "task_map_generation": "generation-new",
            "candidate_ref": "candidate/PDD-1",
            "candidate_head_commit": "head-old",
            "status": "passed",
        },
    )

    assert latest_flow_verification_for_candidate([candidate, stale], candidate_event=candidate) is None


def test_candidate_verification_identity_accepts_current_commit_and_rejects_replay() -> None:
    candidate = ZfEvent(
        id="candidate-new",
        type="candidate.ready",
        payload={
            "workflow_run_id": "run-1",
            "pdd_id": "PDD-1",
            "task_map_generation": "task-map-generation-new-full",
            "candidate_ref": "candidate/PDD-1",
            "candidate_head_commit": "head-new",
        },
    )
    verification = ZfEvent(
        id="verify-current",
        type="verify.passed",
        payload={
            "workflow_run_id": "run-1",
            "pdd_id": "PDD-1",
            "task_map_generation": "generation-new",
            "candidate_ref": "candidate/PDD-1",
            "candidate_head_commit": "head-new",
            "status": "passed",
        },
    )

    assert latest_flow_verification_for_candidate(
        [candidate, verification], candidate_event=candidate
    ) is verification
    consumed = ZfEvent(
        type="flow.discovery.requested",
        payload={"verification_event_id": verification.id},
    )
    assert latest_flow_verification_for_candidate(
        [candidate, verification, consumed], candidate_event=candidate
    ) is None


@pytest.mark.parametrize(
    ("candidate_key", "verification_key", "verification_commit", "expected"),
    [
        ("target_commit", "target_commit", "head-new", True),
        ("target_commit", "candidate_head_commit", "head-new", True),
        ("target_commit", "target_commit", "head-old", False),
    ],
    ids=["target-alias-same", "cross-alias-same", "target-alias-stale"],
)
def test_candidate_verification_target_commit_aliases_are_strict(
    candidate_key: str,
    verification_key: str,
    verification_commit: str,
    expected: bool,
) -> None:
    """Resume/replay identity accepts aliases only when the commit matches."""
    candidate = ZfEvent(
        id="candidate-target-alias",
        type="candidate.ready",
        payload={
            "workflow_run_id": "run-target-alias",
            "pdd_id": "PDD-TARGET-ALIAS",
            "task_map_generation": "generation-target-alias",
            "candidate_ref": "candidate/PDD-TARGET-ALIAS",
            candidate_key: "head-new",
        },
    )
    verification = ZfEvent(
        id="verify-target-alias",
        type="verify.passed",
        payload={
            "workflow_run_id": "run-target-alias",
            "pdd_id": "PDD-TARGET-ALIAS",
            "task_map_generation": "generation-target-alias",
            "candidate_ref": "candidate/PDD-TARGET-ALIAS",
            verification_key: verification_commit,
            "status": "passed",
        },
    )

    result = latest_flow_verification_for_candidate(
        [candidate, verification], candidate_event=candidate
    )
    assert (result is verification) is expected


@pytest.mark.parametrize(
    ("operation_generation", "dispatch_base_commit"),
    [(1, "commit-generation-a"), (2, "commit-generation-a")],
    ids=["first-dispatch", "retry-keeps-frozen-commit"],
)
def test_runtime_reconcile_passes_generation_and_frozen_commit_to_dispatch(
    tmp_path: Path,
    operation_generation: int,
    dispatch_base_commit: str,
) -> None:
    """The blocking reconciler must preserve generation identity on retry."""
    context = {
        "workflow_run_id": "run-s3-runtime",
        "task_map_generation": "map-s3-runtime",
        "generation_id": "generation-s3-runtime",
        "dispatch_base_commit": dispatch_base_commit,
        "flow_kind": "issue",
    }
    task = SimpleNamespace(id="TASK-S3-RUNTIME", status="backlog")
    captured: list[tuple[dict[str, object], dict[str, object]]] = []
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            workflow=SimpleNamespace(flow_metadata_by_kind={}),
        ),
        state_dir=tmp_path / "state",
        project_root=tmp_path / "project",
        event_log=SimpleNamespace(read_all=lambda: []),
        event_writer=SimpleNamespace(append=lambda _event: None),
        task_store=SimpleNamespace(
            get=lambda task_id: task if task_id == task.id else None,
        ),
        _dispatch_globally_paused=lambda: False,
    )
    runtime.state_dir.mkdir()
    runtime.project_root.mkdir()

    def capture_dispatch(
        _runtime: object,
        *,
        policy: object,
        task: object,
        assignment: dict[str, object],
        generation_context: dict[str, object],
        operation_rows: list[dict[str, object]],
        attempt_rows: list[dict[str, object]],
    ) -> SimpleNamespace:
        del policy, task, operation_rows, attempt_rows
        captured.append((dict(assignment), dict(generation_context)))
        return SimpleNamespace(
            action="task_pipeline_dispatch",
            task_id="TASK-S3-RUNTIME",
        )

    with (
        patch.object(
            task_pipeline_runtime,
            "task_pipeline_generation_contexts",
            return_value={task.id: context},
        ),
        patch.object(
            task_pipeline_runtime,
            "task_pipeline_policy_partitions",
            return_value=[{
                "task_ids": (task.id,),
                "policy": {"mode": "blocking"},
            }],
        ),
        patch(
            "zf.runtime.task_pipeline_terminal.reconcile_task_pipeline_terminals",
            return_value=[],
        ),
        patch(
            "zf.runtime.task_pipeline_terminal.reconcile_task_pipeline_freeze",
            return_value=[],
        ),
        patch(
            "zf.runtime.task_pipeline_entry.reconcile_task_pipeline_entries",
            return_value=([], set()),
        ),
        patch(
            "zf.runtime.task_pipeline_recovery.reconcile_task_pipeline_redrives",
            return_value=[],
        ),
        patch(
            "zf.runtime.task_attempt_runtime.task_attempt_store",
            return_value=SimpleNamespace(current_rows=lambda: []),
        ),
        patch(
            "zf.runtime.workflow_operation.reduce_workflow_operations",
            return_value={},
        ),
        patch(
            "zf.runtime.task_pipeline_rework.derive_impl_rework_requests",
            return_value=[],
        ),
        patch("zf.runtime.task_pipeline_rework.reconcile_task_ref_repair_replays"),
        patch(
            "zf.runtime.task_pipeline_acceptance.reconcile_task_pipeline_acceptance_routes",
            return_value=[],
        ),
        patch(
            "zf.runtime.task_pipeline_integration.reconcile_task_pipeline_integration",
            return_value=[],
        ),
        patch.object(task_pipeline_runtime, "terminal_dependency_ids", return_value=set()),
        patch.object(task_pipeline_runtime, "task_pipeline_preferred_roles", return_value={}),
        patch.object(
            task_pipeline_runtime.TaskPipelineReconciler,
            "reconcile",
            return_value={
                "dispatchable": {
                    "impl": [{
                        "task_id": task.id,
                        "stage": "impl",
                        "role_instance": "impl-1",
                        "operation_generation": operation_generation,
                    }],
                },
            },
        ),
        patch(
            "zf.runtime.task_pipeline_semantic_exhaustion.reconcile_task_pipeline_semantic_exhaustion"
        ),
        patch(
            "zf.runtime.task_pipeline_dispatch.dispatch_task_pipeline_stage",
            side_effect=capture_dispatch,
        ),
        patch("zf.runtime.task_pipeline_projection.write_task_pipeline_projection"),
    ):
        decisions = task_pipeline_runtime.reconcile_task_pipeline_runtime(runtime)

    assert len(decisions) == 1
    assert len(captured) == 1
    assignment, passed_context = captured[0]
    assert assignment["operation_generation"] == operation_generation
    assert passed_context["generation_id"] == "generation-s3-runtime"
    assert passed_context["task_map_generation"] == "map-s3-runtime"
    assert passed_context["dispatch_base_commit"] == dispatch_base_commit
