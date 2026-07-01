from __future__ import annotations

from zf.autoresearch.validity import assess_validity


def test_validity_blocks_holdout_touch() -> None:
    result = assess_validity(
        changed_files=["tests/fixtures/holdout/case.json"],
        claims=["dev 场景已通过"],
        evidence_paths=["run.log"],
    )

    assert result.status == "blocked"
    assert "HOLDOUT_TOUCHED" in result.risk_labels
    assert result.protected_paths_touched == ["tests/fixtures/holdout/case.json"]


def test_validity_blocks_real_claim_without_evidence() -> None:
    result = assess_validity(
        claims=["真实 Codex 场景已修复"],
        evidence_paths=[],
    )

    assert result.status == "blocked"
    assert result.blocked_claims == ["真实 Codex 场景已修复"]
    assert result.evidence_debt


def test_validity_needs_probe_without_screenshot() -> None:
    result = assess_validity(
        claims=["dev 场景已通过"],
        evidence_paths=["run.log"],
    )

    assert result.status == "needs_probe"
    assert result.recommended_probe["type"] == "playwright_probe"


def test_validity_passes_supported_claim() -> None:
    result = assess_validity(
        claims=["真实 Codex 场景已修复"],
        evidence_paths=["inner-runner.log", "screenshot.png"],
    )

    assert result.status == "pass"
    assert result.blocked_claims == []
