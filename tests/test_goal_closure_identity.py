from __future__ import annotations

import pytest

from zf.core.events.model import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.goal_closure_identity import (
    GoalClosureIdentityError,
    build_closure_identity,
    validate_goal_closure_dispatch_snapshots,
)


def test_closure_identity_ignores_fanout_execution_noise(tmp_path) -> None:  # noqa: ANN001
    events = [ZfEvent(
        type="run.goal.started",
        correlation_id="run-1",
        payload={"run_id": "run-1"},
    )]
    source = ZfEvent(
        type="flow.discovery.completed",
        correlation_id="run-1",
        payload={},
    )
    common = {
        "workflow_run_id": "run-1",
        "goal_id": "GOAL-1",
        "task_map_generation": "generation-1",
        "candidate_head_commit": "a" * 40,
        "open_p0_p1_gap_count": 0,
        "evidence_refs": ["artifacts/verify.json"],
    }

    first = build_closure_identity(
        events,
        source_event=source,
        payload={**common, "fanout_id": "fanout-1", "stage_id": "scan-a"},
        state_dir=tmp_path,
        flow_kind="prd",
    )
    replay = build_closure_identity(
        events,
        source_event=source,
        payload={**common, "fanout_id": "fanout-2", "stage_id": "scan-b"},
        state_dir=tmp_path,
        flow_kind="prd",
    )

    assert replay["closure_identity"] == first["closure_identity"]
    assert replay["closure_fact_digest"] == first["closure_fact_digest"]


def test_closure_identity_uses_same_legacy_generation_as_claim_pin(tmp_path) -> None:  # noqa: ANN001
    from zf.runtime.goal_claim_set import canonical_task_map_generation

    task_map_ref = "artifacts/GOAL-1/task_map.json"
    events = [ZfEvent(
        type="task_map.ready",
        correlation_id="run-1",
        payload={
            "workflow_run_id": "run-1",
            "goal_id": "GOAL-1",
            "task_map_ref": task_map_ref,
        },
    )]
    identity = build_closure_identity(
        events,
        source_event=ZfEvent(
            type="flow.discovery.completed",
            correlation_id="run-1",
            payload={},
        ),
        payload={
            "workflow_run_id": "run-1",
            "goal_id": "GOAL-1",
            "candidate_head_commit": "a" * 40,
        },
        state_dir=tmp_path,
        flow_kind="issue",
    )

    assert identity["task_map_generation"] == canonical_task_map_generation(
        task_map_ref=task_map_ref,
    )


def test_goal_closure_dispatch_snapshots_bind_identity(tmp_path) -> None:  # noqa: ANN001
    contract = write_immutable_json_sidecar(
        tmp_path,
        {
            "schema_version": "goal-closure-contract-snapshot.v1",
            "workflow_run_id": "run-1",
            "goal_id": "GOAL-1",
            "task_map_generation": "generation-1",
        },
        root="goal-closure/contracts",
        kind="goal_closure_contract_snapshot",
        schema_version="goal-closure-contract-snapshot.v1",
        created_by="test",
    )
    target = write_immutable_json_sidecar(
        tmp_path,
        {
            "schema_version": "goal-closure-target-snapshot.v1",
            "workflow_run_id": "run-1",
            "goal_id": "GOAL-1",
            "task_map_generation": "generation-1",
            "target_commit": "c" * 40,
            "closure_identity": "closure-1",
        },
        root="goal-closure/targets",
        kind="goal_closure_target_snapshot",
        schema_version="goal-closure-target-snapshot.v1",
        created_by="test",
    )
    payload = {
        "workflow_run_id": "run-1",
        "goal_id": "GOAL-1",
        "task_map_generation": "generation-1",
        "target_commit": "c" * 40,
        "closure_identity": "closure-1",
        "contract_snapshot_ref": contract["ref"],
        "contract_snapshot_digest": contract["sha256"],
        "target_snapshot_ref": target["ref"],
        "target_snapshot_digest": target["sha256"],
    }

    validate_goal_closure_dispatch_snapshots(tmp_path, payload)

    with pytest.raises(GoalClosureIdentityError, match="target_commit mismatch"):
        validate_goal_closure_dispatch_snapshots(
            tmp_path,
            {**payload, "target_commit": "d" * 40},
        )


def test_same_task_map_generation_matches_both_encodings() -> None:
    # ZF-REVIEW-141-GEN:短显式格式 vs ref/digest 全量哈希 = 同一代际
    from zf.runtime.goal_closure_bridge import _same_task_map_generation

    full = "caf71b8aa0b7766afa05c39f79e43f6173c015a194e8dbd4edf6017b18e134aa"
    short = "task-map-caf71b8aa0b7766afa05"
    assert _same_task_map_generation(short, full)
    assert _same_task_map_generation(full, short)
    assert _same_task_map_generation(full, full)
    assert not _same_task_map_generation(short, "deadbeef" * 8)
    # 短于 12 位的退回严格相等,避免弱前缀误判
    assert not _same_task_map_generation("task-map-caf7", full)
