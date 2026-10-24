"""B3 (R20): per-lane review scope discipline in the affinity child briefing.

R20: review-lanes reviewing the 213-file candidate hit worker.context.warning →
context.compact.failed → 2400s timeout → spurious review.rejected, because each
lane reviewed the WHOLE candidate instead of its affinity slice. The briefing now
carries an explicit "your slice only — do not read the full candidate" section
for affinity_stage_slots children (review + verify lanes), keeping per-lane
context bounded. The other lanes + the synth role cover the rest.
"""
from __future__ import annotations

import pytest

from zf.runtime.affinity_review_scope import (
    affinity_scope_briefing_lines,
    affinity_scope_identity_errors,
)


def test_affinity_payload_returns_scope_section():
    lines = affinity_scope_briefing_lines({
        "assignment_strategy": "affinity_stage_slots",
        "lane_id": "lane0",
        "affinity_tag": "CJMIN-R20-PI-CORE",
    })
    text = "\n".join(lines)
    assert "AFFINITY SLICE ONLY" in text
    assert "lane0" in text and "CJMIN-R20-PI-CORE" in text
    assert "Do NOT read" in text and "full candidate" in text


def test_non_affinity_returns_empty():
    assert affinity_scope_briefing_lines({"assignment_strategy": "broadcast"}) == []
    assert affinity_scope_briefing_lines({}) == []
    assert affinity_scope_briefing_lines(None) == []


def test_affinity_without_lane_or_tag_returns_empty():
    assert affinity_scope_briefing_lines({"assignment_strategy": "affinity_stage_slots"}) == []


def test_falls_back_to_task_id_when_no_affinity_tag():
    lines = affinity_scope_briefing_lines({
        "assignment_strategy": "affinity_stage_slots",
        "lane_id": "lane1",
        "task_id": "CJMIN-R20-STATE-CONFIG",
    })
    assert "CJMIN-R20-STATE-CONFIG" in "\n".join(lines)


def test_affinity_identity_errors_require_lane_role_and_stage_slot():
    errors = affinity_scope_identity_errors({
        "assignment_strategy": "affinity_stage_slots",
    })

    assert errors == [
        "missing_lane_id",
        "missing_role_instance",
        "missing_stage_slot",
    ]


def test_affinity_identity_errors_accept_complete_identity():
    errors = affinity_scope_identity_errors(
        {
            "assignment_strategy": "affinity_stage_slots",
            "lane_id": "lane1",
            "stage_slot": "review",
        },
        role_instance="review-lane-1",
    )

    assert errors == []


@pytest.mark.parametrize("product_required", [False, True])
def test_runtime_candidate_briefing_reconciles_affinity_and_product_scope(
    tmp_path, product_required,
):
    from tests.test_reader_fanout_runtime import _state
    from tests.test_product_acceptance import _package, RUN_ID, GENERATION, TARGET, CANDIDATE
    from tests.test_typed_result_admission import _contract
    from zf.core.config.schema import RoleConfig, FanoutAggregateConfig
    from zf.runtime.fanout import FanoutContext
    from zf.runtime.task_contract_snapshot import write_task_contract_snapshot

    state, _, _, runtime = _state(tmp_path)
    contract = _contract()
    contract.update(workflow_run_id=RUN_ID, task_map_generation=GENERATION)
    descriptor = write_task_contract_snapshot(state, contract)
    payload = {
        **contract, "contract_snapshot_ref": descriptor["ref"],
        "contract_snapshot_digest": descriptor["sha256"], "target_commit": TARGET,
        "candidate_ref": CANDIDATE, "output_profile_id": "candidate-verify",
        "assignment_strategy": "affinity_stage_slots", "lane_id": "lane0",
        "affinity_tag": "api", "stage_slot": "verify",
    }
    if product_required:
        package, _ = _package(state)
        payload.update(plan_artifact_package_ref=package["ref"],
                       plan_artifact_package_digest=package["sha256"])
    path = runtime._write_fanout_briefing(
        role=RoleConfig(name="verify", instance_id="verify-1", backend="mock", role_kind="reader"),
        context=FanoutContext(fanout_id="verify", stage_id="verify", topology="fanout_reader",
                              trace_id=RUN_ID, trigger_event_id="evt-candidate", target_ref=CANDIDATE),
        child_id="api", run_id="attempt-api", child_payload=payload,
        aggregate=FanoutAggregateConfig(mode="wait_for_all", child_success_event="verify.child.completed",
            child_failure_event="verify.child.failed", success_event="verify.approved"),
    )
    briefing = path.read_text()
    if product_required:
        assert "Do NOT read or review the full candidate or other lanes" not in briefing
        assert "mandatory product journeys" in briefing
        assert "Task requirement rows remain scoped" in briefing
    else:
        assert "AFFINITY SLICE ONLY" in briefing
