"""U20:审角色报告证据观测门(r6.1 凭证复核 finding 13)。

实弹:第 12 轮 review 报告声称跑了 Playwright 运行时探针,但
evidence_refs=0——判决靠信任(经地面真值核验恰好全对,机制上裸奔)。
F7 诚实门只覆盖 dev 完成事件;此门补审角色一侧:review/verify 家族的
子报告若无任何证据引用,发观测事件(与 P3-3 同哲学,不阻塞;一轮
实弹后再议 fail-closed)。
"""

from __future__ import annotations

from typing import Any

REPORT_EVIDENCE_MISSING_EVENT = "stage.report.evidence_missing"
_VERIFICATION_STAGE_MARKERS = ("review", "verify", "judge", "test")
_EVIDENCE_KEYS = (
    "evidence_refs",
    "runtime_evidence_refs",
    "evidence",
    "artifact_refs",
    "probes",
)


def is_verification_stage(*, stage_id: str, event_type: str) -> bool:
    stage = str(stage_id or "").lower()
    etype = str(event_type or "").lower()
    return any(
        marker in stage or etype.startswith(f"{marker}.")
        for marker in _VERIFICATION_STAGE_MARKERS
    )


def report_evidence_gap(report: Any) -> str:
    """返回缺口描述("" = 报告带证据或无报告可核)。"""
    if not isinstance(report, dict):
        return ""
    has_verdict = bool(report.get("status") or report.get("recommendation"))
    if not has_verdict:
        return ""
    for key in _EVIDENCE_KEYS:
        value = report.get(key)
        if isinstance(value, list) and any(str(item or "").strip() for item in value):
            return ""
        if isinstance(value, str) and value.strip():
            return ""
    findings = report.get("findings")
    if isinstance(findings, list):
        for item in findings:
            if isinstance(item, dict):
                for key in _EVIDENCE_KEYS:
                    value = item.get(key)
                    if value and str(value).strip():
                        return ""
    return (
        "verification report carries a verdict but no evidence refs "
        f"(checked keys: {', '.join(_EVIDENCE_KEYS)})"
    )


__all__ = [
    "REPORT_EVIDENCE_MISSING_EVENT",
    "is_verification_stage",
    "report_evidence_gap",
]
