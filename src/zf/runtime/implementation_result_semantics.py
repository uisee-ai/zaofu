"""Compatibility normalization for implementation result verdicts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def recover_implementation_verdict(
    result: dict[str, Any], payload: Mapping[str, Any]
) -> None:
    """Promote nested worker self-check evidence to the control result."""

    if str(result.get("verdict") or "").strip():
        return
    self_check = result.get("self_check")
    if not isinstance(self_check, Mapping):
        self_check = payload.get("impl_self_check")
    verdict = str(self_check.get("verdict") or "").strip() if isinstance(self_check, Mapping) else ""
    if not verdict:
        report = payload.get("report")
        verdict = str(report.get("status") or "").strip() if isinstance(report, Mapping) else ""
    if verdict:
        result["verdict"] = verdict
