from __future__ import annotations

import pytest

from zf.runtime.recovery_delta import (
    RecoveryDeltaError,
    build_recovery_proposal,
    hydrate_recovery_proposal,
    validate_recovery_action,
    write_recovery_proposal,
)


def _proposal(*, classification="design_issue", action="request_replan"):
    return build_recovery_proposal(
        problem_ref="evt-failed",
        failure_scope="task",
        classification=classification,
        recommended_action=action,
        affected_task_ids=["T1"],
        current_package={"package_digest": "old"},
        required_delta={"task_map_revision_required": True},
        expected_resolution="T1 becomes satisfiable",
        verify_condition="new package passes evidence closure",
    )


def test_design_issue_cannot_resume_unchanged_children():
    with pytest.raises(RecoveryDeltaError, match="requires semantic replan"):
        validate_recovery_action(
            _proposal(),
            action="repair_failed_children",
        )


def test_semantic_replan_requires_new_package_and_postcondition():
    with pytest.raises(RecoveryDeltaError, match="did not change"):
        validate_recovery_action(
            _proposal(),
            action="request_replan",
            new_package_digest="old",
        )
    validate_recovery_action(
        _proposal(),
        action="request_replan",
        new_package_digest="new",
        postcondition_receipt={"status": "passed"},
    )


def test_worker_lifecycle_can_resume_same_contract(tmp_path):
    proposal = _proposal(classification="worker_lifecycle", action="resume")
    descriptor = write_recovery_proposal(tmp_path, proposal)

    hydrated = hydrate_recovery_proposal(tmp_path, descriptor)
    validate_recovery_action(hydrated, action="resume")
