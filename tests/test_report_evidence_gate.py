"""U20:审角色报告证据观测门(finding 13)。"""
from __future__ import annotations

from zf.runtime.report_evidence_gate import is_verification_stage, report_evidence_gap


def test_r61_round12_report_shape_flags_gap() -> None:
    # 第 12 轮真实报告形状:有判决、有 findings、零证据引用
    report = {
        "child_id": "review-metrics-metrics",
        "status": "failed",
        "recommendation": "reject",
        "summary": "cancelled orders re-enter running state",
        "findings": [{"severity": "high", "path": "src/stores/simulationStore.ts",
                      "message": "..."}],
    }
    assert report_evidence_gap(report) != ""


def test_report_with_evidence_refs_passes() -> None:
    assert report_evidence_gap({
        "status": "failed", "evidence_refs": ["docs/validation/probe.json"],
    }) == ""
    assert report_evidence_gap({
        "status": "passed",
        "findings": [{"message": "ok", "evidence": "logs/run.txt"}],
    }) == ""


def test_no_verdict_or_non_dict_is_ignored() -> None:
    assert report_evidence_gap({"summary": "just notes"}) == ""
    assert report_evidence_gap(None) == ""


def test_stage_family_detection() -> None:
    assert is_verification_stage(stage_id="avbs-review", event_type="dev.build.done")
    assert is_verification_stage(stage_id="", event_type="verify.child.failed")
    assert is_verification_stage(stage_id="judge-final", event_type="")
    assert not is_verification_stage(stage_id="avbs-impl", event_type="dev.build.done")
    assert not is_verification_stage(stage_id="avbs-scan", event_type="scan.child.completed")
