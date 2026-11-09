"""Final acceptance ownership must not swallow explicit module verification."""

from types import SimpleNamespace

import pytest

from zf.core.task.schema import Task, TaskContract
from zf.runtime.sidecar_refs import hydrate_sidecar_ref
from zf.runtime.task_pipeline_briefing import verification_result_template
from zf.runtime.task_pipeline_targets import prepare_contract_snapshot
from zf.runtime.verification_result import (
    VerificationResultError,
    validate_verification_acceptance,
)


def _prepare(tmp_path, *, local_owner="task_verify", producer="MODULE"):
    task = Task(id="MODULE", title="module", contract=TaskContract(
        behavior="implement module before final assembly",
        verification="check module",
        verification_tiers=["runtime"],
        acceptance_criteria=[{
            "id": "AC-FINAL", "statement": "real assembled system works",
            "mandatory": True, "verification_owner": "candidate_verify",
            "verification_tier": "e2e",
            "verification_command_ids": ["LOCAL", "FINAL"],
        }],
        validation={"commands": [
            {"id": "LOCAL", "command": "check module", "owner": local_owner,
             "tier": "runtime", "producer_task_id": producer},
            {"id": "FINAL", "command": "check assembled system",
             "owner": "candidate_verify", "tier": "e2e"},
        ]},
    ))
    runtime = SimpleNamespace(state_dir=tmp_path, config=SimpleNamespace(
        runtime=SimpleNamespace(git=SimpleNamespace(task_ref_prefix="task")),
    ))
    snapshot, descriptor = prepare_contract_snapshot(
        runtime, task=task, workspace=SimpleNamespace(base_commit="a" * 40),
        generation_context={"workflow_run_id": "run-1",
                            "task_map_generation": "map-1",
                            "generation_admitted_event_id": "evt-plan"},
    )
    assert hydrate_sidecar_ref(tmp_path, descriptor).payload == snapshot
    return snapshot


@pytest.mark.parametrize("producer", ["", "MODULE"])
def test_task_pipeline_keeps_local_check_before_candidate_acceptance(tmp_path, producer):
    snapshot = _prepare(tmp_path, producer=producer)
    assert snapshot["verification_owner"] == "task_verify"
    assert snapshot["verification_tier"] == "task_non_smoke"
    result = verification_result_template(snapshot)
    assert [p["command_id"] for p in result["probe_receipts"]] == ["LOCAL"]
    criterion = result["requirement_results"][0]
    assert criterion["status"] == "not_applicable"
    assert criterion["verification_owner"] == "candidate_verify"
    assert criterion["evidence_refs"] == []
    assert snapshot["acceptance_criteria"][0]["verification_command_ids"] == ["LOCAL", "FINAL"]
    assert snapshot["verification_commands"][1]["owner"] == "candidate_verify"
    validate_verification_acceptance(result, snapshot)
    # The exact same deferred row cannot close final candidate acceptance.
    with pytest.raises(VerificationResultError, match="mandatory"):
        validate_verification_acceptance(
            {**result, "verification_owner": "candidate_verify"}, snapshot,
        )


@pytest.mark.parametrize("owner,producer", [
    ("task_verify", "UPSTREAM"),
    ("impl_self_check", "MODULE"),
    ("candidate_verify", "MODULE"),
    ("human", "MODULE"),
])
def test_candidate_stage_is_not_demoted_by_other_checks(tmp_path, owner, producer):
    snapshot = _prepare(tmp_path, local_owner=owner, producer=producer)
    assert snapshot["verification_owner"] == "candidate_verify"
    assert snapshot["verification_tier"] == "real_e2e"
    result = verification_result_template(snapshot)
    assert result["requirement_results"][0]["status"] == "passed"
    assert "FINAL" in [p["command_id"] for p in result["probe_receipts"]]
