"""Generated Task Pipeline Impl rework-generation admission model."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings, strategies as st

from tests.testkit.model_evidence import ModelEvidence
from zf.core.events import ZfEvent
from zf.runtime.task_pipeline_rework import derive_impl_rework_requests


_MODEL_SETTINGS = settings(
    max_examples=24,
    deadline=None,
    database=None,
    derandomize=True,
)


def _case_events(
    *,
    source_generation: int,
    operation_generation: int,
    context_generation: int,
    status: str,
    same_run: bool,
    same_map: bool,
    stale_result: bool,
) -> tuple[list[ZfEvent], dict[str, dict[str, str]], list[dict[str, object]]]:
    source = ZfEvent(
        type="dev.build.done",
        id="build-1",
        actor="dev",
        task_id="TASK-PIPE-MODEL",
        correlation_id="run-1",
        payload={
            "task_pipeline_stage": "impl",
            "workflow_run_id": "run-1",
            "task_map_generation": "map-1",
            "operation_id": "op-1",
            "operation_generation": source_generation,
            "call_result_admitted_event_id": "call-result-1",
        },
    )
    rejection = ZfEvent(
        type="task.ref.rejected",
        id="reject-1",
        actor="zf-cli",
        task_id="TASK-PIPE-MODEL",
        correlation_id="run-1",
        payload={
            "trigger_event_id": "build-1",
            "rejection_kind": "stale_contract_result" if stale_result else "task_ref_admission",
            "reason": "generated model rejection",
        },
    )
    context = {
        "workflow_run_id": "run-1",
        "task_map_generation": "map-1",
    }
    operation = {
        "operation_id": "op-1",
        "status": status,
        "semantic_verdict": "passed",
        "task_pipeline_stage": "impl",
        "operation_generation": operation_generation,
        "workflow_run_id": "run-1",
        "task_map_generation": "map-1",
        "call_result_admitted_event_id": "call-result-1",
    }
    return [source, rejection], {"TASK-PIPE-MODEL": context}, [operation]


@st.composite
def _generation_cases(draw: st.DrawFn) -> tuple[int, int, int, str, bool, bool, bool]:
    return (
        draw(st.integers(min_value=0, max_value=3)),
        draw(st.integers(min_value=0, max_value=3)),
        draw(st.integers(min_value=0, max_value=3)),
        draw(st.sampled_from(["settled", "pending"])),
        draw(st.booleans()),
        draw(st.booleans()),
        draw(st.booleans()),
    )


@_MODEL_SETTINGS
@given(_generation_cases())
def test_generated_task_pipeline_rework_generation_guard(
    generated: tuple[int, int, int, str, bool, bool, bool],
) -> None:
    (
        source_generation,
        operation_generation,
        context_generation,
        status,
        same_run,
        same_map,
        stale_result,
    ) = generated
    events, contexts, operations = _case_events(
        source_generation=source_generation,
        operation_generation=operation_generation,
        context_generation=context_generation,
        status=status,
        same_run=same_run,
        same_map=same_map,
        stale_result=stale_result,
    )
    # The source/context generation is encoded in the source and operation
    # fixture separately below; keep the context explicitly independent.
    contexts["TASK-PIPE-MODEL"]["task_map_generation"] = (
        "map-1" if same_map and context_generation == 1 else "map-other"
    )
    contexts["TASK-PIPE-MODEL"]["workflow_run_id"] = (
        "run-1" if same_run else "run-other"
    )

    with TemporaryDirectory(prefix="zf-task-pipeline-rework-model-") as temporary:
        evidence = ModelEvidence(
            root=Path(temporary),
            model_name="TaskPipelineReworkGenerationMachine",
            source=Path(__file__),
            settings=str(_MODEL_SETTINGS),
            reference=lambda: {"generated": generated},
        )
        with evidence.guard("task_pipeline_generation_guard", {"generated": generated}):
            requests = derive_impl_rework_requests(
                events=events,
                generation_contexts=contexts,
                operation_rows=operations,
            )
            expected = (
                source_generation >= 1
                and source_generation == operation_generation
                and context_generation == 1
                and status == "settled"
                and same_run
                and same_map
                and not stale_result
            )
            assert bool(requests) is expected
            if expected:
                request = requests["TASK-PIPE-MODEL"]
                assert request["operation_generation"] == source_generation
                assert request["operation_id"] == "op-1"
