"""Mutation-resistant contracts for terminal verification evidence helpers."""

from __future__ import annotations

from copy import deepcopy

import pytest

from zf.core.verification.evidence import (
    TAIL_LIMIT,
    Evidence,
    EvidenceCollector,
    _canonical_verification_tier,
    _check_expected_red,
    _check_passed,
    _check_search_text,
    _covered_verification_tiers,
    _payload_checks,
    _payload_refs,
    _payload_scores,
    _required_verification_tiers,
    _tail_text,
    _validate_judge_payload,
    command_evidence,
)


def _valid_done_evidence() -> dict:
    return {
        "payload_summary": "verified",
        "payload_checks": [
            {
                "command": "pytest -q",
                "exit_code": 0,
                "tier": "runtime",
            }
        ],
        "payload_scores": {
            "correctness": "pass",
            "completeness": "pass",
            "regression_risk": "low",
            "evidence_quality": "pass",
        },
        "artifact_refs": ["artifact.json"],
        "evidence_refs": ["events.jsonl:3"],
        "contract": {"verification_tiers": ["runtime"]},
    }


def test_evidence_collector_summary_is_exact_and_ordered() -> None:
    collector = EvidenceCollector()
    collector.results = [
        Evidence("lint", True, "clean", "t1"),
        Evidence("unit", False, "failed assertion", "t2"),
    ]

    assert collector.summary() == (
        "  [PASS] lint: clean\n  [FAIL] unit: failed assertion"
    )


def test_command_evidence_preserves_failure_inputs_and_bounded_tails() -> None:
    stdout = ("前" * (TAIL_LIMIT + 3)).encode()
    result = command_evidence(
        command="pytest -q",
        exit_code=7,
        stdout=stdout,
        stderr=b"bad-\xff",
        timed_out=True,
        error="runner failed",
    )

    assert result == {
        "command": "pytest -q",
        "exit_code": 7,
        "passed": False,
        "stdout_tail": "前" * TAIL_LIMIT,
        "stderr_tail": "bad-�",
        "timed_out": True,
        "error": "runner failed",
    }
    assert command_evidence(command="true", exit_code=0)["passed"] is True
    assert command_evidence(
        command="true", exit_code=0, error="non-empty"
    )["passed"] is False
    assert command_evidence(command="true", exit_code=0) == {
        "command": "true",
        "exit_code": 0,
        "passed": True,
        "stdout_tail": "",
        "stderr_tail": "",
        "timed_out": False,
        "error": "",
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("abc", "abc"),
        (b"a\xff", "a�"),
        ("x" * (TAIL_LIMIT + 1), "x" * TAIL_LIMIT),
    ],
)
def test_tail_text_contract(value: object, expected: str) -> None:
    assert _tail_text(value) == expected  # type: ignore[arg-type]


def test_payload_checks_merges_all_supported_sources_in_order() -> None:
    checks = [
        {"command": "top"},
        {"command": "nested-checks"},
        {"command": "nested-commands"},
        {"command": "nested-command-evidence"},
    ]
    done = {
        "payload_checks": [checks[0], "ignore"],
        "payload_evidence": {
            "checks": [checks[1]],
            "commands": [checks[2], None],
            "command_evidence": [checks[3]],
        },
    }

    assert _payload_checks(done) == checks
    assert _payload_checks({"payload_checks": {}, "payload_evidence": []}) == []


@pytest.mark.parametrize(
    ("check", "expected_passed", "expected_red"),
    [
        ({"command": "pytest", "passed": True}, True, False),
        ({"name": "unit", "exit_code": 0}, True, False),
        ({"command": "pytest", "passed": False, "exit_code": 1}, False, False),
        (
            {
                "command": "pytest red",
                "status": "RED_expected",
                "exit_code": "2",
            },
            True,
            True,
        ),
        ({"command": "", "name": " ", "passed": True}, False, False),
        (
            {"command": "pytest", "status": "red_expected", "exit_code": 1},
            False,
            False,
        ),
        ({"command": "pytest", "status": "RED_expected", "exit_code": 0}, True, False),
        (
            {
                "command": "pytest",
                "status": "RED_expected",
                "exit_code": 1,
                "timed_out": True,
            },
            False,
            False,
        ),
        (
            {
                "command": "pytest",
                "status": "RED_expected",
                "exit_code": 1,
                "error": "boom",
            },
            False,
            False,
        ),
        (
            {"command": "pytest", "status": "RED_expected", "exit_code": "bad"},
            False,
            False,
        ),
        ({"command": "pytest", "exit_code": 1}, False, False),
        (
            {"name": "red by name", "status": "RED_expected", "exit_code": 1},
            True,
            True,
        ),
        (
            {"command": "", "name": " ", "status": "RED_expected", "exit_code": 1},
            False,
            False,
        ),
        ({"status": "RED_expected", "exit_code": 1}, False, False),
    ],
)
def test_check_passed_and_expected_red_matrix(
    check: dict,
    expected_passed: bool,
    expected_red: bool,
) -> None:
    assert _check_passed(check) is expected_passed
    assert _check_expected_red(check) is expected_red


def test_payload_scores_precedence_and_type_guards() -> None:
    assert _payload_scores(
        {
            "payload_evidence": {
                "scores": {"source": "nested-scores"},
                "scorecard": {"source": "nested-scorecard"},
            },
            "payload_scores": {"source": "top"},
        }
    ) == {"source": "nested-scores"}
    assert _payload_scores(
        {
            "payload_evidence": {
                "scores": [],
                "scorecard": {"source": "nested-scorecard"},
            },
            "payload_scores": {"source": "top"},
        }
    ) == {"source": "nested-scorecard"}
    assert _payload_scores({"payload_scores": {"source": "top"}}) == {
        "source": "top"
    }
    assert _payload_scores({"payload_scores": []}) == {}


def test_payload_refs_collects_top_nested_and_check_values_in_order() -> None:
    done = {
        "artifact_refs": [" top-a ", ""],
        "artifacts": "top-b",
        "payload_checks": [
            {
                "artifact_refs": ["check-a", ""],
                "artifacts": " check-b ",
            },
        ],
        "payload_evidence": {
            "artifact_refs": " nested-a ",
            "artifacts": ["nested-b", "  "],
        },
    }

    assert _payload_refs(done, "artifact_refs", "artifacts") == [
        "top-a",
        "top-b",
        "nested-a",
        "nested-b",
        "check-a",
        "check-b",
    ]
    assert _payload_refs({"payload_evidence": []}, "artifact_refs") == []


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-054: null artifact refs are stringified into replayable evidence",
)
def test_payload_refs_rejects_null_entries() -> None:
    assert _payload_refs({"artifact_refs": [None]}, "artifact_refs") == []


def test_required_and_covered_verification_tiers_are_replayable() -> None:
    done = {
        "contract": {
            "verification_tiers": [
                " static_quality ",
                "runtime:integration",
                "manual-evidence",
                " ",
            ]
        },
        "payload_evidence": {
            "tier_results": {
                "manual evidence": {"name": "receipt", "passed": True},
                "ignored": True,
            }
        },
    }
    checks = [
        {"command": "ruff check .", "passed": True, "tier": "static_quality"},
        {
            "name": "API behavior test",
            "exit_code": 0,
            "verification_tier": "runtime:integration",
        },
        {"command": "playwright test", "exit_code": 1, "tier": "e2e"},
    ]

    assert _required_verification_tiers(done) == [
        "static",
        "runtime",
        "manual_evidence",
    ]
    assert _covered_verification_tiers(done, checks) == {
        "static",
        "runtime",
        "e2e",
        "manual_evidence",
    }
    assert _required_verification_tiers({"contract": []}) == []
    assert _required_verification_tiers(
        {"contract": {"verification_tiers": "runtime"}}
    ) == []


def test_covered_tier_results_list_requires_command_evidence() -> None:
    done = {
        "payload_evidence": {
            "tier_results": [
                {"tier": "ignored-first", "passed": True},
                {"tier": "static", "command": "ruff", "passed": True},
                {"name": "runtime", "command": "pytest", "exit_code": 0},
                {"tier": 7, "command": "custom", "passed": True},
                {"tier": "e2e", "passed": True},
                "ignore",
            ]
        }
    }

    assert _covered_verification_tiers(done, []) == {"static", "runtime"}


def test_covered_tiers_skips_failed_check_before_passing_check() -> None:
    checks = [
        {"command": "first", "exit_code": 1, "tier": "static"},
        {"command": "second", "exit_code": 0, "tier": "runtime"},
    ]

    assert _covered_verification_tiers({}, checks) == {"runtime"}


def test_check_search_text_uses_every_supported_field() -> None:
    check = {
        "id": "Case 1",
        "name": "Browser Flow",
        "command": "pytest E2E",
        "summary": "API Flow",
        "description": "End To End",
        "evidence": 7,
        "artifact_refs": ["A Path", "B"],
        "evidence_refs": ["Evidence Ref"],
        "ignored": "never included",
    }

    assert _check_search_text(check) == (
        "case_1_browser_flow_pytest_e2e_api_flow_end_to_end_7_a_path_b_evidence_ref"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" STATIC ", "static"),
        ("runtime-regression", "runtime"),
        ("manual evidence:receipt", "manual_evidence"),
        ("e2e_browser", "e2e"),
        ("custom-tier", "custom-tier"),
        (None, "None"),
    ],
)
def test_canonical_verification_tier_contract(value: object, expected: str) -> None:
    assert _canonical_verification_tier(value) == expected


def test_validate_judge_payload_accepts_complete_replayable_evidence() -> None:
    assert _validate_judge_payload(_valid_done_evidence()) == []


def test_validate_judge_payload_accepts_legacy_ref_aliases() -> None:
    done = _valid_done_evidence()
    done.pop("artifact_refs")
    done.pop("evidence_refs")
    done["artifacts"] = ["artifact.json"]
    done["evidence_paths"] = ["events.jsonl:3"]

    assert _validate_judge_payload(done) == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda item: item.update(payload_summary="   "),
            ["judge.passed payload must include summary"],
        ),
        (
            lambda item: item.pop("payload_summary"),
            ["judge.passed payload must include summary"],
        ),
        (
            lambda item: item.update(payload_checks=[]),
            [
                "judge.passed payload must include passing command/check evidence",
                "judge.passed payload missing verification tier evidence: runtime",
            ],
        ),
        (
            lambda item: item["payload_scores"].pop("correctness"),
            ["judge.passed payload missing score dimensions: correctness"],
        ),
        (
            lambda item: item.update(payload_scores={}),
            [
                "judge.passed payload missing score dimensions: "
                "completeness, correctness, evidence_quality, regression_risk"
            ],
        ),
        (
            lambda item: item.update(artifact_refs=[]),
            ["judge.passed payload must include replayable artifact refs"],
        ),
        (
            lambda item: item.update(evidence_refs=[]),
            ["judge.passed payload must include evidence refs"],
        ),
        (
            lambda item: item["contract"].update(verification_tiers=["e2e"]),
            ["judge.passed payload missing verification tier evidence: e2e"],
        ),
        (
            lambda item: item["contract"].update(
                verification_tiers=["e2e", "static"]
            ),
            [
                "judge.passed payload missing verification tier evidence: "
                "e2e, static"
            ],
        ),
    ],
)
def test_validate_judge_payload_reports_each_missing_contract(
    mutation,
    expected: list[str],
) -> None:
    done = deepcopy(_valid_done_evidence())
    mutation(done)
    assert _validate_judge_payload(done) == expected
