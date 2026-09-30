"""Deterministic contracts for the optional ZAP report gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.testkit.zap import (
    ZapReportError,
    evaluate_zap_file,
    evaluate_zap_json,
    parse_zap_json,
)


def _report(*alerts: dict) -> dict:
    return {"site": [{"@name": "http://isolated.test", "alerts": list(alerts)}]}


def _alert(*, riskcode: object = "0", name: str = "Informational") -> dict:
    return {
        "pluginid": "10001",
        "alert": name,
        "riskcode": riskcode,
        "confidence": "High",
        "instances": [{"uri": "http://isolated.test/"}],
    }


def test_zap_empty_alert_report_passes_with_zero_risk_budget() -> None:
    verdict = evaluate_zap_json(_report())
    assert verdict.status == "pass"
    assert verdict.alerts == ()


def test_zap_alert_above_budget_is_security_failure() -> None:
    verdict = evaluate_zap_json(_report(_alert(riskcode="2", name="Medium")))
    assert verdict.status == "fail"
    assert "risk=2" in verdict.violations[0]


def test_zap_declared_risk_budget_can_allow_low_alert() -> None:
    assert evaluate_zap_json(_report(_alert(riskcode="1")), max_risk=1).status == "pass"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"site": []},
        {"site": [{"alerts": "bad"}]},
        {"site": [{"alerts": [{}]}]},
        {"site": [{"alerts": [_alert() | {"instances": ["not-an-object"]}]}]},
    ],
)
def test_zap_malformed_report_is_harness_failure(payload: object) -> None:
    assert evaluate_zap_json(payload).status == "harness_failure"


@pytest.mark.parametrize("riskcode", [True, "4", "unknown"])
def test_zap_invalid_riskcode_is_rejected(riskcode: object) -> None:
    with pytest.raises(ZapReportError):
        parse_zap_json(_report(_alert(riskcode=riskcode)))


def test_zap_nonzero_scanner_exit_without_alerts_is_harness_failure() -> None:
    verdict = evaluate_zap_json(_report(), scanner_exit_code=2)
    assert verdict.status == "harness_failure"
    assert "code 2" in verdict.reason


def test_zap_alert_instance_count_is_preserved() -> None:
    alert = parse_zap_json(_report(_alert()))[0]
    assert alert.instances == 1


@pytest.mark.parametrize("max_risk", [1.0, "1"])
def test_zap_non_integer_risk_budget_is_harness_failure(max_risk: object) -> None:
    assert evaluate_zap_json(_report(), max_risk=max_risk).status == "harness_failure"


@pytest.mark.parametrize("contents", ["{not-json"])
def test_zap_report_file_read_errors_are_harness_failures(
    tmp_path: Path, contents: str
) -> None:
    report = tmp_path / "zap.json"
    report.write_text(contents, encoding="utf-8")
    verdict = evaluate_zap_file(report)
    assert verdict.status == "harness_failure"
    assert "cannot read ZAP report" in verdict.reason


def test_zap_missing_report_file_is_harness_failure(tmp_path: Path) -> None:
    verdict = evaluate_zap_file(tmp_path / "missing.json")
    assert verdict.status == "harness_failure"
    assert "cannot read ZAP report" in verdict.reason


def test_zap_report_file_rejects_non_object_json(tmp_path: Path) -> None:
    report = tmp_path / "zap.json"
    report.write_text("[]", encoding="utf-8")
    verdict = evaluate_zap_file(report)
    assert verdict.status == "harness_failure"
    assert verdict.reason == "ZAP report root must be an object"


def test_zap_report_file_uses_json_gate(tmp_path: Path) -> None:
    report = tmp_path / "zap.json"
    report.write_text(json.dumps(_report(_alert(riskcode="2"))), encoding="utf-8")
    assert evaluate_zap_file(report).status == "fail"
