"""Plan protocol failure must never become an implementation-ready retry."""

from pathlib import Path

import pytest

from tests.test_workflow_resume_batch import _projection
from zf.core.events.model import ZfEvent


@pytest.mark.parametrize(
    "failure_class",
    [
        "plan_artifact_package_protocol",
        "plan_candidate_review_identity",
        "plan_candidate_preflight",
    ],
)
def test_reader_plan_failure_with_inherited_task_map_stays_with_stage_replan(
    tmp_path: Path,
    failure_class: str,
) -> None:
    identity = {
        "fanout_id": "fanout-prd-plan-revision",
        "stage_id": "prd-plan",
        "pdd_id": "F-PLAN",
        "workflow_run_id": "run-revision",
    }
    failure = {
        **identity,
        "status": "failed",
        "failure_event": "prd.plan.failed",
        "failure_class": failure_class,
        "attempt_domain": "protocol_repair",
        "task_map_ref": "artifacts/new-draft/task_map.json",
        "plan_artifact_package_ref": "artifacts/plan-packages/old.json",
        "plan_artifact_package_digest": "a" * 64,
        "source_commit": "b" * 40,
        "candidate_base_commit": "b" * 40,
        "failed_task_ids": ["FMS-SCAFFOLD"],
    }
    projection = _projection(
        tmp_path,
        [
            ZfEvent(
                type="fanout.started",
                id="evt-plan-start",
                payload={
                    **identity,
                    "topology": "fanout_reader",
                },
            ),
            ZfEvent(
                type="fanout.aggregate.completed",
                id="evt-plan-rejected",
                payload=failure,
            ),
            ZfEvent(
                type="prd.plan.failed",
                id="evt-plan-failed",
                causation_id="evt-plan-rejected",
                payload=failure,
            ),
        ],
    )
    assert projection["batch_checkpoints"] == []
    assert projection["summary"]["batch_pending"] == 0
