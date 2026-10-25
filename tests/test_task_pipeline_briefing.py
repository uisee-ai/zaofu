from __future__ import annotations

import json
import pytest

from zf.runtime.stage_execution_card import (
    prepare_profiled_stage_result,
    prepare_writer_execution_card,
    render_review_subject_lines,
)
from zf.runtime.task_pipeline_briefing import (
    integration_acceptance_result_template,
    verification_result_template,
)


def test_impl_output_contract_exposes_structured_scope_blockers(tmp_path) -> None:
    _, _, _, lines = prepare_writer_execution_card(
        state_dir=tmp_path,
        task_item={
            "semantic_result_submit_mode": "blocking",
            "operation_id": "op-impl-1",
            "attempt_id": "attempt-1",
            "result_scratch_ref": "tmp/result-submit/op-impl-1/result.json",
        },
        task_payload={},
        completion_payload={},
        run_id="run-1",
        cli_command="zf",
        completion_command="zf emit dev.build.done",
        blocked_command="zf emit dev.blocked",
    )

    result = json.loads(
        (tmp_path / "tmp/result-submit/op-impl-1/result.json").read_text(
            encoding="utf-8"
        )
    )
    assert result["failure_class"] == "none"
    assert result["blocker_kind"] == "none"
    assert "upstream_contract_gap" in "\n".join(lines)
    assert "task_context_stale" in "\n".join(lines)
    assert "Self-check command IDs are a closed set" in "\n".join(lines)


@pytest.mark.parametrize("profile", ["task-verify", "candidate-verify"])
def test_verify_output_contract_forbids_invented_command_ids(tmp_path, profile) -> None:
    _, lines = prepare_profiled_stage_result(
        state_dir=tmp_path,
        child_payload={
            "operation_id": "op-verify-1",
            "attempt_id": "attempt-1",
            "output_profile_id": profile,
            "output_profile_revision": "1",
        },
        success_payload={"verification_result": {
            "product_acceptance_report": {"journey_results": []},
        }} if profile == "candidate-verify" else {"verification_result": {}},
        run_id="run-1",
        cli_command="zf",
    )

    assert "Verification command IDs are a closed set" in "\n".join(lines)
    assert "zf result validate --operation op-verify-1" in "\n".join(lines)
    assert "does not consume the submit capability" in "\n".join(lines)
    if profile == "candidate-verify":
        assert "every mandatory product journey/assertion" in "\n".join(lines)
        assert "exact ref and sha256" in "\n".join(lines)


def test_task_base_recovery_reviews_target_not_previous_candidate() -> None:
    lines = render_review_subject_lines(
        candidate_ref="refs/heads/candidate/TASK-ROOT",
        candidate_head="old-candidate",
        candidate_prefix="candidate",
        subject_pdd_id="TASK-ROOT",
        verification_reader=True,
        artifact_delivery=False,
        handoff_kind="task_base_recovery",
        target_ref="new-task-base",
        target_commit="new-task-base",
    )

    text = "\n".join(lines)
    assert "EVALUATE THE RECOVERY TARGET" in text
    assert "target_commit" in text
    assert "previous candidate provenance" in text
    assert "EVALUATE THE CANDIDATE" not in text


def test_verify_template_binds_every_admitted_acceptance_criterion() -> None:
    result = verification_result_template({
        "verification_commands": [{
            "command_id": "cmd-unit",
            "command": "pytest tests/test_unit.py -q",
            "command_digest": "a" * 64,
            "owner": "task_verify",
        }, {
            "command_id": "cmd-e2e",
            "command": "npm run test:e2e",
            "command_digest": "b" * 64,
            "owner": "candidate_verify",
        }],
        "acceptance_criteria": [
            {
                "acceptance_id": "AC-1",
                "verification_owner": "task_verify",
                "verification_tier": "browser",
                "verification_command_ids": ["cmd-unit", "cmd-e2e"],
            },
            {
                "acceptance_id": "AC-2",
                "verification_owner": "task_verify",
                "verification_tier": "runtime",
            },
        ],
    })

    assert result["schema_version"] == "verification-result.v1"
    assert result["verdict"] == "passed"
    assert [
        row["acceptance_id"] for row in result["requirement_results"]
    ] == ["AC-1", "AC-2"]
    assert [
        row["verification_tier"] for row in result["requirement_results"]
    ] == ["browser", "runtime"]
    assert all(
        row["evidence_refs"] for row in result["requirement_results"]
    )
    assert result["probe_receipts"] == [{
        "probe_id": "verify-cmd-unit",
        "command_id": "cmd-unit",
        "command": "pytest tests/test_unit.py -q",
        "command_digest": "a" * 64,
        "target_commit": "<exact target_commit from Stage Context>",
        "status": "passed",
        "exit_code": 0,
        "evidence_refs": ["<durable exact command output or event ref>"],
    }]
    assert result["requirement_results"][0]["reproduction_commands"] == [
        "pytest tests/test_unit.py -q"
    ]


def test_verify_template_runs_producer_command_only_on_its_task() -> None:
    command = {
        "command_id": "cmd-e2e",
        "command": "npm run test:e2e",
        "command_digest": "b" * 64,
        "owner": "task_verify",
        "producer_task_id": "TASK-EVIDENCE",
    }
    criterion = {
        "acceptance_id": "AC-1",
        "verification_owner": "task_verify",
        "verification_tier": "real_e2e",
        "verification_command_ids": ["cmd-e2e"],
    }

    consumer = verification_result_template({
        "task_id": "TASK-CONSUMER",
        "verification_commands": [command],
        "acceptance_criteria": [criterion],
    })
    producer = verification_result_template({
        "task_id": "TASK-EVIDENCE",
        "verification_commands": [command],
        "acceptance_criteria": [criterion],
    })

    assert consumer["probe_receipts"] == [{
        "probe_id": "independent-task-verify",
        "status": "passed",
        "evidence_refs": ["<durable command/test evidence ref>"],
    }]
    assert consumer["requirement_results"][0]["reproduction_commands"] == []
    assert [item["command_id"] for item in producer["probe_receipts"]] == [
        "cmd-e2e"
    ]


def test_verify_template_defers_acceptance_owned_by_candidate_verify() -> None:
    result = verification_result_template({
        "task_id": "TASK-CONSUMER",
        "verification_commands": [{
            "command_id": "cmd-local",
            "command": "npm run test:unit",
            "command_digest": "a" * 64,
            "owner": "task_verify",
            "producer_task_id": "TASK-CONSUMER",
        }, {
            "command_id": "cmd-candidate",
            "command": "npm run test:e2e",
            "command_digest": "b" * 64,
            "owner": "candidate_verify",
            "producer_task_id": "TASK-RELEASE",
        }],
        "acceptance_criteria": [{
            "acceptance_id": "AC-LOCAL",
            "verification_owner": "task_verify",
            "verification_tier": "runtime",
            "verification_command_ids": ["cmd-local"],
        }, {
            "acceptance_id": "AC-GLOBAL",
            "verification_owner": "candidate_verify",
            "verification_tier": "e2e",
            "verification_command_ids": ["cmd-candidate"],
        }],
    })

    local, global_result = result["requirement_results"]
    assert local["status"] == "passed"
    assert local["evidence_refs"]
    assert local["reproduction_commands"] == ["npm run test:unit"]
    assert global_result == {
        "acceptance_id": "AC-GLOBAL",
        "status": "not_applicable",
        "verification_owner": "candidate_verify",
        "verification_tier": "e2e",
        "evidence_refs": [],
        "findings": [],
        "reproduction_commands": [],
    }


def test_candidate_verify_template_runs_only_local_non_human_commands() -> None:
    result = verification_result_template({
        "task_id": "TASK-RELEASE",
        "verification_owner": "candidate_verify",
        "verification_tier": "runtime",
        "verification_commands": [{
            "command_id": "cmd-static",
            "command": "npm run typecheck",
            "command_digest": "a" * 64,
            "owner": "impl_self_check",
            "producer_task_id": "TASK-RELEASE",
        }, {
            "command_id": "cmd-release",
            "command": "npm run release:audit",
            "command_digest": "b" * 64,
            "owner": "candidate_verify",
            "producer_task_id": "TASK-RELEASE",
        }, {
            "command_id": "cmd-upstream-e2e",
            "command": "npm run upstream:e2e",
            "command_digest": "c" * 64,
            "owner": "task_verify",
            "producer_task_id": "TASK-UPSTREAM",
        }, {
            "command_id": "cmd-human",
            "command": "record-human-receipt",
            "command_digest": "d" * 64,
            "owner": "human",
            "producer_task_id": "TASK-RELEASE",
        }],
        "acceptance_criteria": [{
            "acceptance_id": "AC-RELEASE",
            "verification_owner": "candidate_verify",
            "verification_tier": "runtime",
            "verification_command_ids": [
                "cmd-static",
                "cmd-release",
                "cmd-upstream-e2e",
                "cmd-human",
            ],
        }],
    })

    assert result["verification_owner"] == "candidate_verify"
    assert [row["command_id"] for row in result["probe_receipts"]] == [
        "cmd-static",
        "cmd-release",
    ]
    assert result["requirement_results"][0]["reproduction_commands"] == [
        "npm run typecheck",
        "npm run release:audit",
    ]


def test_acceptance_template_pins_independent_verify_evidence() -> None:
    result = integration_acceptance_result_template({
        "verification_result_ref": "artifacts/verify/TASK-A.json",
    })

    assert result["schema_version"] == (
        "task-integration-acceptance-result.v1"
    )
    assert result["verdict"] == "admit"
    assert result["evidence_refs"] == ["artifacts/verify/TASK-A.json"]
    assert result["feedback"] == []
    assert result["delta_intent"] == {}
