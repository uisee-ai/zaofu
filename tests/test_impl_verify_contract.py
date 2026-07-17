from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskContract
from zf.runtime.canonical_recovery import (
    PRODUCT_FAILURE_CLASS,
    build_rework_cap_payload,
    classify_recovery_scope,
    recovery_series_from_event,
    rework_dispatch_count,
    valid_series_failures,
)
from zf.runtime.rework_feedback import (
    ReworkFeedbackError,
    feedback_briefing_lines,
    hydrate_rework_feedback,
    write_rework_feedback,
)
from zf.runtime.task_contract_snapshot import (
    TaskContractSnapshotError,
    build_target_snapshot,
    build_task_contract_snapshot,
    effective_contract_revision,
    hydrate_task_contract_snapshot,
    hydrate_target_snapshot,
    target_payload_fields,
    task_map_generation,
    write_task_contract_snapshot,
    write_target_snapshot,
)
from zf.runtime.verification_result import (
    VerificationResultError,
    normalize_verification_result,
    recovery_owner,
)


def _task() -> Task:
    return Task(
        id="TASK-ONE",
        title="Add one",
        contract=TaskContract(
            behavior="implement one",
            verification="pytest -q",
            verification_tiers=["runtime"],
            scope=["src/**", "tests/**"],
            plan_ref="artifacts/plan/task-map.json",
            acceptance_criteria=[
                {
                    "text": "returns one",
                    "verification_owner": "test-0",
                    "verification_tier": "runtime",
                },
                "does not mutate input",
            ],
        ),
    )


def _snapshot(task: Task) -> dict:
    return build_task_contract_snapshot(
        task,
        workflow_run_id="run-1",
        task_map_generation_id=task_map_generation(task),
        base_commit="a" * 40,
        task_ref="refs/zf/tasks/TASK-ONE",
    )


def _target(descriptor: dict, *, commit: str = "c" * 40) -> dict:
    return {
        **build_target_snapshot(descriptor, target_commit=commit),
        "target_snapshot_ref": "artifacts/target.json",
        "target_snapshot_digest": "d" * 64,
    }


def test_task_contract_snapshot_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    task = _task()
    snapshot = _snapshot(task)
    descriptor = write_task_contract_snapshot(tmp_path, snapshot)
    hydrated = hydrate_task_contract_snapshot(
        tmp_path,
        descriptor,
        expected={"task_id": task.id, "contract_revision": effective_contract_revision(task)},
    )
    assert hydrated == snapshot
    assert all(
        item["acceptance_id"]
        and item["verification_owner"]
        and item["verification_tier"]
        for item in hydrated["acceptance_criteria"]
    )
    target_body = build_target_snapshot(
        descriptor,
        target_commit="c" * 40,
        contract_snapshot=hydrated,
    )
    target_descriptor = write_target_snapshot(tmp_path, target_body)
    assert hydrate_target_snapshot(tmp_path, target_descriptor) == target_body
    assert target_payload_fields(target_descriptor)["target_snapshot_ref"]

    (tmp_path / descriptor["ref"]).write_text("{}\n", encoding="utf-8")
    with pytest.raises(TaskContractSnapshotError):
        hydrate_task_contract_snapshot(tmp_path, descriptor)


def test_reordered_criteria_do_not_reuse_snapshot_identity(tmp_path: Path) -> None:
    first = _task()
    second = _task()
    second.contract.acceptance_criteria.reverse()
    first_descriptor = write_task_contract_snapshot(tmp_path, _snapshot(first))
    second_descriptor = write_task_contract_snapshot(tmp_path, _snapshot(second))
    assert effective_contract_revision(first) != effective_contract_revision(second)
    assert first_descriptor["ref"] != second_descriptor["ref"]


def test_verification_result_separates_execution_failure_from_rejection() -> None:
    contract = _snapshot(_task())
    descriptor = {"ref": "artifacts/contract.json", "sha256": "b" * 64}
    target = _target(descriptor)
    criterion = contract["acceptance_criteria"][0]
    second = contract["acceptance_criteria"][1]
    rejected = normalize_verification_result(
        {
            "verification_result": {
                "execution_status": "completed",
                "verdict": "rejected",
                "requirement_results": [{
                    "acceptance_id": criterion["acceptance_id"],
                    "status": "failed",
                    "verification_owner": criterion["verification_owner"],
                    "verification_tier": criterion["verification_tier"],
                    "findings": [{"message": "returned zero"}],
                    "reproduction_commands": ["pytest -q"],
                    "evidence_refs": ["artifacts/reject.log"],
                }, {
                    "acceptance_id": second["acceptance_id"],
                    "status": "passed",
                    "verification_owner": second["verification_owner"],
                    "verification_tier": second["verification_tier"],
                    "findings": [],
                    "reproduction_commands": ["pytest -q"],
                    "evidence_refs": ["artifacts/pass.log"],
                }],
            },
        },
        contract_snapshot=contract,
        target_snapshot=target,
    )
    assert recovery_owner(rejected) == "implementation_owner"

    failed = normalize_verification_result(
        {"status": "failed", "reason": "provider timeout"},
        contract_snapshot=contract,
        target_snapshot=target,
    )
    assert failed["execution_status"] == "failed"
    assert failed["verdict"] == "abstained"
    assert recovery_owner(failed) == "run_manager"


def test_completed_verdict_requires_symmetric_requirement_matrix() -> None:
    contract = _snapshot(_task())
    target = _target({"ref": "artifacts/contract.json", "sha256": "b" * 64})
    with pytest.raises(VerificationResultError):
        normalize_verification_result(
            {"status": "completed", "report": {"recommendation": "approve"}},
            contract_snapshot=contract,
            target_snapshot=target,
        )


def test_rework_feedback_is_lossless_and_digest_verified(tmp_path: Path) -> None:
    source = ZfEvent(
        type="lane.stage.failed",
        id="evt-reject",
        task_id="TASK-ONE",
        payload={"reason": "returned zero"},
    )
    result = {
        "summary": "one acceptance criterion failed",
        "evidence_refs": ["artifacts/test.log"],
        "reproduction_commands": ["pytest -q"],
        "requirement_results": [{
            "acceptance_id": "ac-one",
            "status": "failed",
            "findings": [{"message": "returned zero"}],
            "reproduction_commands": ["pytest -q tests/test_one.py"],
        }],
    }
    descriptor = write_rework_feedback(
        tmp_path,
        task_id="TASK-ONE",
        failure_fingerprint="fp-one",
        source_event=source,
        source_attempt=1,
        verification_result=result,
        allowed_paths=["src/**"],
    )
    body = hydrate_rework_feedback(
        tmp_path,
        descriptor,
        expected_task_id="TASK-ONE",
        expected_fingerprint="fp-one",
    )
    assert body["failed_acceptance_ids"] == ["ac-one"]
    assert "ac-one: returned zero" in feedback_briefing_lines(body)
    assert descriptor["feedback_id"] == body["feedback_id"]
    assert descriptor["finding_ids"] == [body["findings"][0]["finding_id"]]
    assert body["requirement_results"][0]["findings"][0]["finding_id"]

    (tmp_path / descriptor["ref"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ReworkFeedbackError):
        hydrate_rework_feedback(tmp_path, descriptor)


def test_rework_finding_identity_is_stable_across_verifier_retries(
    tmp_path: Path,
) -> None:
    result = {
        "requirement_results": [{
            "acceptance_id": "ac-one",
            "status": "failed",
            "findings": [{"message": "returned zero", "severity": "high"}],
        }],
    }
    descriptors = []
    for event_id in ("evt-first", "evt-second"):
        descriptors.append(write_rework_feedback(
            tmp_path,
            task_id="TASK-ONE",
            failure_fingerprint="fp-one",
            source_event=ZfEvent(
                type="verify.failed",
                id=event_id,
                task_id="TASK-ONE",
                payload={"reason": "returned zero"},
            ),
            source_attempt=1,
            verification_result=result,
        ))
    assert descriptors[0]["feedback_id"] != descriptors[1]["feedback_id"]
    assert descriptors[0]["finding_ids"] == descriptors[1]["finding_ids"]


def _lane_failure(
    event_id: str,
    *,
    fanout_id: str,
    contract_revision: str = "rev-1",
    source_event_id: str = "",
    failure_class: str = PRODUCT_FAILURE_CLASS,
) -> ZfEvent:
    return ZfEvent(
        type="lane.stage.failed",
        id=event_id,
        task_id="TASK-ONE",
        correlation_id="run-1",
        payload={
            "task_id": "TASK-ONE",
            "workflow_run_id": "run-1",
            "contract_revision": contract_revision,
            "stage_slot": "verify",
            "failure_target": "impl",
            "fanout_id": fanout_id,
            "source_event_id": source_event_id or event_id,
            "failure_fingerprint": "fp-one",
            "failure_class": failure_class,
            "reason": "same defect",
        },
    )


def test_common_recovery_series_counts_across_generations_and_dedupes() -> None:
    first = _lane_failure("evt-1", fanout_id="fanout-1", source_event_id="child-1")
    duplicate = _lane_failure("evt-1a", fanout_id="fanout-1", source_event_id="child-1")
    second = _lane_failure("evt-2", fanout_id="fanout-2", source_event_id="child-2")
    third = _lane_failure("evt-3", fanout_id="fanout-3", source_event_id="child-3")
    infra = _lane_failure(
        "evt-infra",
        fanout_id="fanout-4",
        failure_class="verifier_execution_failure",
    )
    events = [first, duplicate, second, infra, third]
    series = recovery_series_from_event(third)
    failures = valid_series_failures(
        events,
        series,
        event_types={"lane.stage.failed"},
    )
    assert [event.id for event in failures] == ["evt-1", "evt-2", "evt-3"]
    cap = build_rework_cap_payload(
        series=series,
        failures=failures,
        max_attempts=2,
        trigger_event=third,
    )
    assert cap["semantic_triage_required"] is True
    assert cap["failure_count"] == 3


def test_common_recovery_series_never_crosses_workflow_runs() -> None:
    current = _lane_failure("evt-current", fanout_id="fanout-current")
    prior = _lane_failure("evt-prior", fanout_id="fanout-prior")
    prior.payload["workflow_run_id"] = "run-prior"
    series = recovery_series_from_event(current)
    failures = valid_series_failures(
        [prior, current],
        series,
        event_types={"lane.stage.failed"},
    )
    assert [event.id for event in failures] == ["evt-current"]


def test_common_recovery_series_resets_only_for_contract_revision() -> None:
    first = _lane_failure("evt-1", fanout_id="fanout-1")
    first.payload["task_map_generation"] = "generation-1"
    amended_map = _lane_failure("evt-2", fanout_id="fanout-2")
    amended_map.payload["task_map_generation"] = "generation-2"
    old_contract = _lane_failure(
        "evt-old-contract",
        fanout_id="fanout-old-contract",
        contract_revision="rev-0",
    )
    current = _lane_failure("evt-3", fanout_id="fanout-3")
    current.payload["task_map_generation"] = "generation-3"

    failures = valid_series_failures(
        [first, amended_map, old_contract, current],
        recovery_series_from_event(current),
        event_types={"lane.stage.failed"},
    )
    assert [event.id for event in failures] == ["evt-1", "evt-2", "evt-3"]


def test_common_recovery_series_ignores_stale_and_superseded_failures() -> None:
    stale = _lane_failure("evt-stale", fanout_id="fanout-stale")
    stale.payload["stale"] = True
    replaced = _lane_failure("evt-replaced", fanout_id="fanout-replaced")
    replaced.payload["superseded_by"] = "fanout-current"
    cancelled = _lane_failure("evt-cancelled", fanout_id="fanout-cancelled")
    current = _lane_failure("evt-current", fanout_id="fanout-current")
    superseded = ZfEvent(
        type="fanout.cancelled",
        payload={
            "fanout_id": "fanout-cancelled",
            "reason": "superseded by current generation",
        },
    )

    failures = valid_series_failures(
        [stale, replaced, cancelled, superseded, current],
        recovery_series_from_event(current),
        event_types={"lane.stage.failed"},
    )
    assert [event.id for event in failures] == ["evt-current"]


def test_rework_dispatch_count_is_separate_from_failure_count() -> None:
    failure = _lane_failure("evt-1", fanout_id="fanout-1")
    series = recovery_series_from_event(failure)
    request = ZfEvent(
        type="lane.stage.rework.requested",
        id="evt-r1",
        task_id="TASK-ONE",
        payload={
            "task_id": "TASK-ONE",
            "workflow_run_id": "run-1",
            "contract_revision": "rev-1",
            "failed_stage_slot": "verify",
            "target_stage_slot": "impl",
            "failure_fingerprint": "fp-one",
            "lane_stage_event_id": failure.id,
        },
    )
    assert rework_dispatch_count(
        [failure, request],
        series,
        event_type="lane.stage.rework.requested",
    ) == 1


def test_candidate_scope_does_not_depend_on_empty_task_id() -> None:
    assembly = ZfEvent(
        type="integration.failed",
        task_id="CJMIN-ASSEMBLY-001",
        payload={
            "verification_owner": "assembly",
            "recovery_action": "replan",
            "candidate_ref": "refs/zf/candidate/cj-min",
        },
    )
    lane = ZfEvent(
        type="test.failed",
        task_id="TASK-ONE",
        payload={"lane_id": "lane-1", "failure_scope": "task"},
    )
    assert classify_recovery_scope(assembly) == "candidate"
    assert classify_recovery_scope(lane) == "task"


def test_snapshot_tier_accepts_llm_alias_vocabulary():
    """ZF-TIER-ALIAS-01:snapshot 锻造点必须过 canonical 别名表。

    07-16 复跑实弹:planner 产出 tier 'unit'(别名表里早有映射)但
    _verification_tier 不查表 → 整张 task_map 被拒 → integration.failed
    → replan 同因两连败 → task.rework.capped。宽进严出:LLM 词汇先归一,
    再映射内部档位。
    """
    from zf.runtime.task_contract_snapshot import _verification_tier

    assert _verification_tier("unit") == "task_non_smoke"
    assert _verification_tier("integration") == "task_non_smoke"
    assert _verification_tier("lint") == "fast"
    assert _verification_tier("smoke") == "real_e2e"
    assert _verification_tier("runtime") == "task_non_smoke"  # 原有直通不回归
    import pytest
    from zf.runtime.task_contract_snapshot import TaskContractSnapshotError
    with pytest.raises(TaskContractSnapshotError):
        _verification_tier("bogus-tier")
