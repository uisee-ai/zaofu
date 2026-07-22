from zf.core.events.model import ZfEvent
from zf.runtime.candidate_result_binding import candidate_task_source_commits


def test_candidate_task_commits_include_incremental_lineage_and_rework_run_alias() -> None:
    events = [
        ZfEvent(
            type="run.started",
            correlation_id="run-1",
            payload={"run_id": "run-1"},
        ),
        ZfEvent(
            type="task.ref.updated",
            task_id="T1",
            correlation_id="run-1",
            payload={"task_id": "T1", "source_commit": "1" * 40},
        ),
        ZfEvent(
            type="candidate.ready",
            correlation_id="run-1",
            payload={
                "workflow_run_id": "run-1",
                "candidate_base_commit": "0" * 40,
                "candidate_head_commit": "a" * 40,
                "completed_task_ids": ["T1"],
            },
        ),
        ZfEvent(
            type="dev.build.done",
            task_id="T2",
            payload={
                "workflow_run_id": "run-1",
                "run_id": "attempt-rework-1",
                "source_commit": "2" * 40,
            },
        ),
        ZfEvent(
            type="task.ref.updated",
            task_id="T2",
            payload={
                "run_id": "attempt-rework-1",
                "task_id": "T2",
                "source_commit": "2" * 40,
            },
        ),
        ZfEvent(
            type="candidate.ready",
            correlation_id="run-1",
            payload={
                "workflow_run_id": "run-1",
                "candidate_base_commit": "a" * 40,
                "candidate_head_commit": "b" * 40,
                "completed_task_ids": ["T2"],
            },
        ),
    ]

    assert candidate_task_source_commits(
        events,
        workflow_run_id="run-1",
        candidate_head_commit="b" * 40,
    ) == {"T1": "1" * 40, "T2": "2" * 40}
