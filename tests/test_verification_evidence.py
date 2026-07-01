"""Tests for evidence collection."""

from __future__ import annotations

from zf.core.verification.evidence import (
    Evidence,
    EvidenceCollector,
    validate_terminal_done_evidence,
)
from zf.core.verification.gates import GateResult


def test_evidence_from_gate_result():
    result = GateResult(name="typecheck", passed=True, exit_code=0, output="All good")
    evidence = Evidence.from_gate_result(result)
    assert evidence.gate_name == "typecheck"
    assert evidence.passed
    assert evidence.output_summary == "All good"


def test_evidence_collector_all_pass():
    collector = EvidenceCollector()
    collector.record(GateResult(name="lint", passed=True, exit_code=0, output=""))
    collector.record(GateResult(name="test", passed=True, exit_code=0, output=""))
    assert collector.all_passed()
    assert len(collector.results) == 2


def test_evidence_collector_one_fails():
    collector = EvidenceCollector()
    collector.record(GateResult(name="lint", passed=True, exit_code=0, output=""))
    collector.record(GateResult(name="test", passed=False, exit_code=1, output="FAIL"))
    assert not collector.all_passed()


def test_evidence_collector_summary():
    collector = EvidenceCollector()
    collector.record(GateResult(name="a", passed=True, exit_code=0, output=""))
    collector.record(GateResult(name="b", passed=False, exit_code=1, output="err"))
    summary = collector.summary()
    assert "a" in summary
    assert "b" in summary
    assert "FAIL" in summary or "fail" in summary.lower()


def test_evidence_collector_empty():
    collector = EvidenceCollector()
    assert collector.all_passed()  # vacuously true
    assert collector.results == []


def test_terminal_done_evidence_accepts_expected_red_runtime_tier():
    missing = validate_terminal_done_evidence(
        done_evidence={
            "payload_summary": "RED test seed is intentionally failing.",
            "payload_checks": [
                {
                    "command": "pnpm vitest run",
                    "exit_code": 1,
                    "tier": "runtime",
                    "status": "RED_expected",
                },
            ],
            "payload_scores": {
                "correctness": "pass",
                "completeness": "pass",
                "regression_risk": "risk",
                "evidence_quality": "pass",
            },
            "artifact_refs": ["test/red.test.ts"],
            "evidence_refs": ["events.jsonl:1"],
            "contract": {"verification_tiers": ["runtime"]},
        },
        require_payload_evidence=True,
    )

    assert missing == []


def test_terminal_done_evidence_rejects_unmarked_runtime_failure():
    missing = validate_terminal_done_evidence(
        done_evidence={
            "payload_summary": "plain failure",
            "payload_checks": [
                {"command": "pnpm vitest run", "exit_code": 1, "tier": "runtime"},
            ],
            "payload_scores": {
                "correctness": "pass",
                "completeness": "pass",
                "regression_risk": "risk",
                "evidence_quality": "pass",
            },
            "artifact_refs": ["test/red.test.ts"],
            "evidence_refs": ["events.jsonl:1"],
            "contract": {"verification_tiers": ["runtime"]},
        },
        require_payload_evidence=True,
    )

    assert any("passing command/check evidence" in item for item in missing)
    assert any("verification tier evidence: runtime" in item for item in missing)


def test_terminal_done_evidence_infers_e2e_from_passing_behavior_check():
    missing = validate_terminal_done_evidence(
        done_evidence={
            "payload_summary": "CLI behavior flow passed.",
            "payload_checks": [
                {
                    "id": "focused_behavior_vitest",
                    "command": (
                        "pnpm vitest run "
                        "test/behavior/vs1-cli-faux-provider.test.ts"
                    ),
                    "exit_code": 0,
                    "tier": "runtime",
                },
            ],
            "payload_scores": {
                "correctness": "pass",
                "completeness": "pass",
                "regression_risk": "low",
                "evidence_quality": "pass",
            },
            "artifact_refs": ["test/behavior/vs1-cli-faux-provider.test.ts"],
            "evidence_refs": ["command:focused_behavior_vitest:exit_code_0"],
            "contract": {"verification_tiers": ["runtime", "e2e"]},
        },
        require_payload_evidence=True,
    )

    assert missing == []


def test_terminal_done_evidence_does_not_infer_e2e_from_failed_behavior_check():
    missing = validate_terminal_done_evidence(
        done_evidence={
            "payload_summary": "CLI behavior flow failed.",
            "payload_checks": [
                {
                    "id": "focused_behavior_vitest",
                    "command": (
                        "pnpm vitest run "
                        "test/behavior/vs1-cli-faux-provider.test.ts"
                    ),
                    "exit_code": 1,
                    "tier": "runtime",
                },
            ],
            "payload_scores": {
                "correctness": "fail",
                "completeness": "fail",
                "regression_risk": "high",
                "evidence_quality": "pass",
            },
            "artifact_refs": ["test/behavior/vs1-cli-faux-provider.test.ts"],
            "evidence_refs": ["command:focused_behavior_vitest:exit_code_1"],
            "contract": {"verification_tiers": ["runtime", "e2e"]},
        },
        require_payload_evidence=True,
    )

    assert any("passing command/check evidence" in item for item in missing)
    assert any("verification tier evidence: e2e" in item for item in missing)


class TestTierResultsRequireCommandEvidence:
    """2026-06-10 review (I4/I7): bare-boolean/string tier_results were a
    self-asserted bypass of contract tier coverage."""

    def _payload(self, tier_results):
        return {
            "payload_summary": "done",
            "payload_checks": [
                {"command": "pytest -q", "exit_code": 0},
            ],
            "payload_scores": {
                "correctness": "pass",
                "completeness": "pass",
                "regression_risk": "low",
                "evidence_quality": "pass",
            },
            "artifact_refs": ["src/x.py"],
            "evidence_refs": ["events.jsonl:1"],
            "payload_evidence": {"tier_results": tier_results},
            "contract": {"verification_tiers": ["e2e"]},
        }

    def test_bare_boolean_tier_result_does_not_cover(self):
        missing = validate_terminal_done_evidence(
            done_evidence=self._payload({"e2e": True}),
            require_payload_evidence=True,
        )
        assert any("verification tier" in m and "e2e" in m for m in missing)

    def test_pass_string_tier_result_does_not_cover(self):
        missing = validate_terminal_done_evidence(
            done_evidence=self._payload({"e2e": "pass"}),
            require_payload_evidence=True,
        )
        assert any("verification tier" in m and "e2e" in m for m in missing)

    def test_command_backed_tier_result_covers(self):
        missing = validate_terminal_done_evidence(
            done_evidence=self._payload(
                {"e2e": {"command": "pytest tests/e2e -q", "exit_code": 0}},
            ),
            require_payload_evidence=True,
        )
        assert missing == []

    def test_command_backed_list_tier_result_covers(self):
        missing = validate_terminal_done_evidence(
            done_evidence=self._payload(
                [{"tier": "e2e", "command": "npx playwright test", "exit_code": 0}],
            ),
            require_payload_evidence=True,
        )
        assert missing == []

    def test_dict_without_command_does_not_cover(self):
        missing = validate_terminal_done_evidence(
            done_evidence=self._payload({"e2e": {"passed": True}}),
            require_payload_evidence=True,
        )
        assert any("verification tier" in m and "e2e" in m for m in missing)
