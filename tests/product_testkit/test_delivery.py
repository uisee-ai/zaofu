from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

import pytest


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _result(*, command_ref: str, command_digest: str, acceptance_digest: str) -> dict:
    return {
        "schema_version": "verification-result.v1",
        "run_id": "run-1",
        "candidate_ref": "refs/heads/candidate",
        "target_commit": "a" * 40,
        "verdict": "pass",
        "commands": [{"command_id": "unit", "mandatory": True, "passed": True}],
        "cleanup": [],
        "missing_commands": [],
        "domain_coverage": {"selected": ["P-18"], "executed": ["P-18"], "missing": []},
        "artifact_index": [{"ref": command_ref, "digest": command_digest}],
        "product_acceptance_report": {
            "verdict": "passed",
            "workflow_run_id": "run-1",
            "candidate_ref": "refs/heads/candidate",
            "target_commit": "a" * 40,
            "journey_results": [{
                "journey_id": "journey-1",
                "status": "passed",
                "evidence_refs": [{"ref": "journey.json", "sha256": acceptance_digest}],
                "assertion_results": [],
            }],
        },
    }


def _fully_indexed_result(tmp_path: Path) -> dict:
    command = tmp_path / "unit.stdout.log"
    command.write_text("ok\n", encoding="utf-8")
    journey = tmp_path / "journey.json"
    journey.write_text('{"status":"passed"}\n', encoding="utf-8")
    result = _result(
        command_ref=command.name,
        command_digest=_sha256(command),
        acceptance_digest=_sha256(journey),
    )
    result["artifact_index"].append({"ref": journey.name, "digest": _sha256(journey)})
    return result


def test_delivery_readiness_requires_acceptance_evidence_in_artifact_index(tmp_path: Path):
    command = tmp_path / "unit.stdout.log"
    command.write_text("ok\n", encoding="utf-8")
    journey = tmp_path / "journey.json"
    journey.write_text('{"status":"passed"}\n', encoding="utf-8")
    result = _result(
        command_ref=command.name,
        command_digest=_sha256(command),
        acceptance_digest=_sha256(journey),
    )
    delivery = importlib.import_module("tests.product_testkit.delivery")

    decision = delivery.evaluate_delivery_readiness(
        result,
        evidence_root=tmp_path,
        expected_run_id="run-1",
        expected_candidate_ref="refs/heads/candidate",
        expected_target_commit="a" * 40,
        acceptance_required=True,
    )

    assert decision["allowed"] is False
    assert decision["reasons"] == ["acceptance_evidence_not_indexed:journey.json"]


def test_delivery_readiness_never_turns_green_tests_into_merge_authorization(tmp_path: Path):
    result = _fully_indexed_result(tmp_path)
    product_testkit = importlib.import_module("tests.product_testkit")

    decision = product_testkit.evaluate_delivery_readiness(
        result,
        evidence_root=tmp_path,
        expected_run_id="run-1",
        expected_candidate_ref="refs/heads/candidate",
        expected_target_commit="a" * 40,
        acceptance_required=True,
    )

    assert decision["allowed"] is True
    assert decision["reasons"] == []
    assert decision["merge_authorized"] is False


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda result: result.update(schema_version="wrong"), "verification_schema_invalid"),
        (lambda result: result.update(run_id="other"), "run_id_mismatch"),
        (lambda result: result.update(candidate_ref="other"), "candidate_ref_mismatch"),
        (lambda result: result.update(target_commit="b" * 40), "target_commit_mismatch"),
        (lambda result: result.update(verdict="fail"), "verification_not_passed:fail"),
        (lambda result: result.update(missing_commands=["unit"]), "missing_commands"),
        (
            lambda result: result["domain_coverage"].update(missing=["P-18"]),
            "domain_coverage_incomplete",
        ),
        (
            lambda result: result["commands"][0].update(passed=False),
            "mandatory_command_failed",
        ),
        (
            lambda result: result.update(cleanup=[{"command_id": "cleanup.0", "passed": False}]),
            "cleanup_failed",
        ),
    ],
)
def test_delivery_readiness_rejects_each_mechanical_gate_failure(tmp_path: Path, mutate, reason: str):
    result = _fully_indexed_result(tmp_path)
    mutate(result)
    delivery = importlib.import_module("tests.product_testkit.delivery")

    decision = delivery.evaluate_delivery_readiness(
        result,
        evidence_root=tmp_path,
        expected_run_id="run-1",
        expected_candidate_ref="refs/heads/candidate",
        expected_target_commit="a" * 40,
        acceptance_required=True,
    )

    assert decision["allowed"] is False
    assert decision["reasons"] == [reason]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda result: result.pop("product_acceptance_report"), "product_acceptance_missing"),
        (
            lambda result: result["product_acceptance_report"].update(verdict="rejected"),
            "product_acceptance_not_passed:rejected",
        ),
        (
            lambda result: result["product_acceptance_report"].update(workflow_run_id="other"),
            "acceptance_run_id_mismatch",
        ),
        (
            lambda result: result["product_acceptance_report"].update(candidate_ref="other"),
            "acceptance_candidate_ref_mismatch",
        ),
        (
            lambda result: result["product_acceptance_report"].update(target_commit="b" * 40),
            "acceptance_target_commit_mismatch",
        ),
        (
            lambda result: result["product_acceptance_report"]["journey_results"][0]["evidence_refs"][0].update(
                sha256="f" * 64
            ),
            "acceptance_evidence_digest_mismatch:journey.json",
        ),
    ],
)
def test_delivery_readiness_rejects_invalid_product_acceptance(tmp_path: Path, mutate, reason: str):
    result = _fully_indexed_result(tmp_path)
    mutate(result)
    delivery = importlib.import_module("tests.product_testkit.delivery")

    decision = delivery.evaluate_delivery_readiness(
        result,
        evidence_root=tmp_path,
        expected_run_id="run-1",
        expected_candidate_ref="refs/heads/candidate",
        expected_target_commit="a" * 40,
        acceptance_required=True,
    )

    assert decision["allowed"] is False
    assert decision["reasons"] == [reason]


def test_delivery_readiness_reports_tampered_artifact_as_primary_failure(tmp_path: Path):
    result = _fully_indexed_result(tmp_path)
    (tmp_path / "unit.stdout.log").write_text("tampered\n", encoding="utf-8")
    delivery = importlib.import_module("tests.product_testkit.delivery")

    decision = delivery.evaluate_delivery_readiness(
        result,
        evidence_root=tmp_path,
        expected_run_id="run-1",
        expected_candidate_ref="refs/heads/candidate",
        expected_target_commit="a" * 40,
        acceptance_required=True,
    )

    assert decision["allowed"] is False
    assert decision["reasons"] == ["artifact_index_invalid:artifact digest mismatch"]


def test_delivery_readiness_records_explicit_nonblocking_acceptance_mode(tmp_path: Path):
    result = _fully_indexed_result(tmp_path)
    result.pop("product_acceptance_report")
    delivery = importlib.import_module("tests.product_testkit.delivery")

    decision = delivery.evaluate_delivery_readiness(
        result,
        evidence_root=tmp_path,
        expected_run_id="run-1",
        expected_candidate_ref="refs/heads/candidate",
        expected_target_commit="a" * 40,
        acceptance_required=False,
    )

    assert decision["allowed"] is True
    assert decision["acceptance_required"] is False
    assert decision["merge_authorized"] is False
