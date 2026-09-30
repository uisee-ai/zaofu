from __future__ import annotations

import pytest

from .stability import StabilityError, assess_stability


def _result(*, verdict: str, passed: bool, commit: str = "a" * 40, digest: str = "b" * 64) -> dict:
    return {
        "schema_version": "verification-result.v1",
        "run_id": "run-1",
        "candidate_ref": "candidate/main",
        "target_commit": commit,
        "verdict": verdict,
        "commands": [{
            "command_id": "unit",
            "command_digest": digest,
            "passed": passed,
            "timed_out": False,
            "environment_error": False,
        }],
        "services": [],
        "cleanup": [],
    }


def test_same_command_fail_then_pass_is_flaky_not_green():
    report = assess_stability((
        _result(verdict="fail", passed=False),
        _result(verdict="pass", passed=True),
    ))

    assert report["schema_version"] == "product-stability-report.v1"
    assert report["verdict"] == "flaky"
    assert report["stable"] is False
    assert report["flaky_commands"] == ["unit"]
    assert report["attempt_verdicts"] == ["fail", "pass"]


@pytest.mark.parametrize(
    ("second", "message"),
    [
        (_result(verdict="pass", passed=True, commit="c" * 40), "target_commit"),
        (_result(verdict="pass", passed=True, digest="d" * 64), "command plan"),
    ],
)
def test_stability_rejects_incomparable_attempts(second, message):
    with pytest.raises(StabilityError, match=message):
        assess_stability((_result(verdict="fail", passed=False), second))


def test_stop_on_first_failure_uses_full_plan_and_still_detects_flaky_command():
    plan = [
        {"command_id": "unit", "command_digest": "b" * 64},
        {"command_id": "later", "command_digest": "c" * 64},
    ]
    first = _result(verdict="fail", passed=False)
    first["command_plan"] = plan
    first["missing_commands"] = ["later"]
    second = _result(verdict="pass", passed=True)
    second["command_plan"] = plan
    second["commands"].append({
        "command_id": "later",
        "command_digest": "c" * 64,
        "passed": True,
        "timed_out": False,
        "environment_error": False,
    })

    report = assess_stability((first, second))

    assert report["verdict"] == "flaky"
    assert report["flaky_commands"] == ["unit"]
    assert report["command_outcomes"]["later"] == ["not_run", "pass"]


def test_openapi_fail_then_pass_is_flaky_not_green():
    first = _result(verdict="fail", passed=True)
    second = _result(verdict="pass", passed=True)
    for result, verdict in ((first, "fail"), (second, "pass")):
        result["commands"] = []
        result["command_plan"] = [{"command_id": "openapi", "command_digest": "c" * 64}]
        result["api_contracts"] = [{"contract_id": "openapi", "verdict": verdict}]

    report = assess_stability((first, second))

    assert report["verdict"] == "flaky"
    assert report["flaky_commands"] == ["openapi"]
