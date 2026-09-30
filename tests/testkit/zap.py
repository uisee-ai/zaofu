"""Fail-closed adapter for OWASP ZAP JSON report evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping


class ZapReportError(ValueError):
    """The ZAP report cannot support a trustworthy security verdict."""


@dataclass(frozen=True)
class ZapAlert:
    plugin_id: str
    name: str
    risk: int
    confidence: str
    instances: int


@dataclass(frozen=True)
class ZapVerdict:
    status: str
    alerts: tuple[ZapAlert, ...] = ()
    violations: tuple[str, ...] = ()
    reason: str = ""


def _risk(value: Any) -> int:
    if isinstance(value, bool):
        raise ZapReportError("alert riskcode must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ZapReportError("alert riskcode must be an integer") from exc
    if str(value) != str(result) or result not in range(4):
        raise ZapReportError("alert riskcode must be 0, 1, 2 or 3")
    return result


def parse_zap_json(payload: Mapping[str, Any]) -> tuple[ZapAlert, ...]:
    """Parse the stable fields emitted by ZAP's JSON report format."""

    if not isinstance(payload, Mapping):
        raise ZapReportError("ZAP report root must be an object")
    sites = payload.get("site")
    if not isinstance(sites, list) or not sites:
        raise ZapReportError("ZAP report needs a non-empty site array")
    alerts: list[ZapAlert] = []
    for site in sites:
        if not isinstance(site, Mapping):
            raise ZapReportError("each ZAP site must be an object")
        raw_alerts = site.get("alerts")
        if not isinstance(raw_alerts, list):
            raise ZapReportError("each ZAP site needs an alerts array")
        for raw in raw_alerts:
            if not isinstance(raw, Mapping):
                raise ZapReportError("each ZAP alert must be an object")
            plugin_id = raw.get("pluginid")
            name = raw.get("alert")
            confidence = raw.get("confidence")
            instances = raw.get("instances")
            if (
                not isinstance(plugin_id, (str, int))
                or plugin_id == ""
                or not isinstance(name, str)
                or not name
            ):
                raise ZapReportError("ZAP alert needs pluginid and alert")
            if not isinstance(confidence, str) or not confidence or not isinstance(instances, list):
                raise ZapReportError("ZAP alert confidence/instances are malformed")
            if any(not isinstance(instance, Mapping) for instance in instances):
                raise ZapReportError("ZAP alert instances are malformed")
            alerts.append(
                ZapAlert(str(plugin_id), name, _risk(raw.get("riskcode")), confidence, len(instances))
            )
    return tuple(alerts)


def evaluate_zap_json(
    payload: Mapping[str, Any],
    *,
    max_risk: int = 0,
    scanner_exit_code: int | None = 0,
) -> ZapVerdict:
    """Apply a declared maximum risk; malformed/incomplete scans fail closed."""

    if isinstance(max_risk, bool) or not isinstance(max_risk, int) or max_risk not in range(4):
        return ZapVerdict("harness_failure", reason="max_risk must be 0, 1, 2 or 3")
    try:
        alerts = parse_zap_json(payload)
    except ZapReportError as exc:
        return ZapVerdict("harness_failure", reason=str(exc))
    violations = tuple(
        f"{alert.plugin_id}:{alert.name} risk={alert.risk}"
        for alert in alerts
        if alert.risk > max_risk
    )
    if violations:
        return ZapVerdict("fail", alerts, violations)
    if scanner_exit_code != 0:
        return ZapVerdict(
            "harness_failure", alerts, reason=f"ZAP exited with code {scanner_exit_code}"
        )
    return ZapVerdict("pass", alerts)


def evaluate_zap_file(
    report_path: str | Path,
    *,
    max_risk: int = 0,
    scanner_exit_code: int | None = 0,
) -> ZapVerdict:
    """Load a scanner report and preserve file/JSON errors as harness failures."""

    try:
        payload = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return ZapVerdict("harness_failure", reason=f"cannot read ZAP report: {exc}")
    return evaluate_zap_json(
        payload,
        max_risk=max_risk,
        scanner_exit_code=scanner_exit_code,
    )


__all__ = [
    "ZapAlert",
    "ZapReportError",
    "ZapVerdict",
    "evaluate_zap_file",
    "evaluate_zap_json",
    "parse_zap_json",
]
