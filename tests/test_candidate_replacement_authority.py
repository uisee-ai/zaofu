import pytest

from tests.test_candidate_verification_authority import _runtime, _seed_frozen_candidate
from tests.test_plan_artifact_package import _package
from zf.core.events import ZfEvent
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.candidate_verification_authority import (
    CandidateVerificationAuthorityError,
    prepare_candidate_verification_authority,
)
from zf.runtime.plan_artifact_package import write_plan_artifact_package
from zf.runtime.task_contract_snapshot import (
    descriptor_from_payload,
    hydrate_task_contract_snapshot,
)


@pytest.mark.parametrize(
    "fault",
    [
        "",
        "other_run",
        "wrong_base",
        "no_replacement",
        "absent_successor",
        "still_active",
        "unverified_cosuccessor",
        "wrong_digest",
    ],
)
def test_frozen_history_only_exempts_exact_verified_replacements(tmp_path, fault):
    runtime = _runtime(tmp_path)
    state = runtime.state_dir
    run = "run-candidate-authority"
    base = "a" * 40
    task = {
        "task_id": "TASK-1",
        "supersedes_task_ids": ["OLD"],
        "base_commit": base,
        "source_refs": ["git:" + base],
    }
    if fault == "no_replacement":
        task["supersedes_task_ids"] = []
    if fault == "absent_successor":
        task["task_id"] = "OTHER"
    tasks = [task]
    if fault == "still_active":
        tasks.append({"task_id": "OLD"})
    if fault == "unverified_cosuccessor":
        tasks.append({**task, "task_id": "MISSING"})
    task_map = write_immutable_json_sidecar(
        state,
        {"tasks": tasks},
        root="fixtures/task_map",
        kind="task_map",
        schema_version="task-map.v1",
        created_by="test",
    )
    package = _package(state, generation="generation-1")
    package["workflow_run_id"] = run
    next(p for p in package["produced"] if p["logical_name"] == "task_map").update(
        ref=task_map["ref"], sha256=task_map["sha256"]
    )
    pd = write_plan_artifact_package(state, package)
    fields = {
        "plan_artifact_package_id": pd["package_id"],
        "plan_artifact_package_ref": pd["ref"],
        "plan_artifact_package_digest": pd["sha256"],
        "task_map_ref": task_map["ref"],
        "task_map_digest": "0" * 64 if fault == "wrong_digest" else task_map["sha256"],
    }
    runtime.event_log.append(
        ZfEvent(
            type="task.ref.updated",
            origin="kernel",
            task_id="OLD",
            correlation_id=run,
            payload={"workflow_run_id": run, "source_commit": "f" * 40},
        )
    )
    runtime.event_log.append(
        ZfEvent(
            type="candidate.ready",
            origin="kernel",
            payload={
                "workflow_run_id": "unrelated" if fault == "other_run" else run,
                "candidate_ref": "refs/heads/candidate/demo",
                "task_map_generation": "previous",
                "candidate_head_commit": "1" * 40 if fault == "wrong_base" else base,
                "schema_version": "candidate-freeze-receipt.v1",
                "freeze_receipt_digest": "8" * 64,
                "completed_task_ids": ["OLD"],
            },
        )
    )
    _seed_frozen_candidate(runtime, package_fields=fields, additional_completed=["OLD"])

    def prepare():
        return prepare_candidate_verification_authority(
            runtime,
            workflow_run_id=run,
            task_id="FLOW-ANCHOR",
            payload={"candidate_event_id": "evt-candidate-ready"},
        )

    if fault:
        with pytest.raises(
            CandidateVerificationAuthorityError, match="OLD.*lacks current admitted"
        ):
            prepare()
    else:
        aggregate = hydrate_task_contract_snapshot(
            state, descriptor_from_payload(prepare())
        )
        assert aggregate["completed_task_ids"] == ["TASK-1"]
        assert [r["source_task_id"] for r in aggregate["acceptance_criteria"]] == [
            "TASK-1"
        ]
