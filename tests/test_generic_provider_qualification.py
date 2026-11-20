"""Independent provider judgments and reproducible failed qualification."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pytest

from tests.e2e import generic_workflow_real_provider_drill as drill
from tests.e2e.test_generic_workflow_complex_mock_e2e import run_generic_workflow_complex_scenario


def _expected():
    return {"workflow_run_id": "run", "generic_workflow_contract_digest": "a" * 64,
            "artifacts": [{"ref": "report.json"}],
            "verdict": "passed", "summary": "All claims verified.",
            "recommended_action": "complete", "open_gap_refs": [],
            "goal_coverage": [{"goal_claim_id": "AC1", "status": "closed",
                               "supporting_artifact_refs": ["report.json"]}]}


def test_provider_gets_fixed_identity_without_a_preset_judgment(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    report = state / "report.json"
    report.write_text('{"conclusion":"unsupported"}')
    expected = _expected()

    def invoke(**kwargs):
        contract = json.loads((state / "provider-verification-contract.json").read_text())
        assert "verdict" not in contract["artifact_delivery_result"]
        assert "summary" not in contract["artifact_delivery_result"]
        assert "recommended_action" not in contract["artifact_delivery_result"]
        assert contract["artifact_delivery_result"]["goal_coverage"] == [{"goal_claim_id": "AC1"}]
        fields = kwargs["schema"]["properties"]["artifact_delivery_result"]["properties"]
        assert fields["generic_workflow_contract_digest"]["enum"] == ["a" * 64]
        assert fields["workflow_run_id"]["enum"] == ["run"]
        assert set(fields["verdict"]["enum"]) == {"passed", "rejected", "blocked"}
        supporting = fields["goal_coverage"]["items"]["properties"]["supporting_artifact_refs"]
        assert supporting["items"]["enum"] == ["report.json"]
        assert "minItems" not in supporting
        return {"artifact_delivery_result": expected}, {"raw_stdout": ""}

    monkeypatch.setattr(drill, "_invoke_codex", invoke)
    verify = drill._provider_verifier(backend="codex", timeout_seconds=5,
                                     model="test", reasoning_effort="high", audit_holder={})
    assert verify(tmp_path, state, {"report": {"ref": str(report)}}, expected) == expected


def test_rejected_judgment_is_admitted_but_never_completes_goal(tmp_path):
    def reject(_project, _state, sources, expected):
        result = copy.deepcopy(expected)
        result.update(verdict="rejected", recommended_action="gap_plan",
                      summary="Dispatch count is not independent proof of delivery.",
                      open_gap_refs=[sources["report"]["ref"]])
        for claim in result["goal_coverage"]:
            claim["status"] = "open"
        return result

    result = run_generic_workflow_complex_scenario(tmp_path, provider_verifier=reject,
                                                  expected_verdict="rejected")
    assert result["provider_verdict"] == "rejected"
    assert result["goal_completed_count"] == 0
    assert result["result_admitted"] is True


def test_positive_fixture_answers_question_without_equating_dispatch_and_delivery(tmp_path):
    def verify(_project, state, sources, expected):
        report = json.loads((state / sources["report"]["ref"]).read_text())
        assert report["conclusion"].startswith("No:")
        assert "scope" in sources
        return expected

    run_generic_workflow_complex_scenario(tmp_path, provider_verifier=verify)


def test_provider_can_cite_the_actual_upstream_evidence_it_read(tmp_path):
    def verify(_project, _state, sources, expected):
        result = copy.deepcopy(expected)
        result["verification_evidence_refs"] = [sources[key]["ref"]
                                                for key in ("scope", "evidence_1", "evidence_2")]
        return result

    result = run_generic_workflow_complex_scenario(tmp_path, provider_verifier=verify)
    assert result["dossier_status"] == "ready"


def test_failed_real_drill_preserves_evidence_before_cleanup(tmp_path, monkeypatch):
    def fail(root, **_kwargs):
        state = root / "project" / ".zf"
        state.mkdir(parents=True)
        (state / "provider-audit.json").write_text('{"provider_result":{"verdict":"rejected"}}')
        (state / "events.jsonl").write_text("")
        raise ValueError("qualification failed")

    monkeypatch.setattr(drill, "run_generic_workflow_complex_scenario", fail)
    evidence = tmp_path / "evidence"
    with pytest.raises(ValueError, match="qualification failed"):
        drill.run(argparse.Namespace(confirm_real=True, backend="codex", timeout_seconds=5,
                  model="test", reasoning_effort="high", evidence_dir=str(evidence), case="supported"))
    receipt = json.loads((evidence / "qualification.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["error_type"] == "ValueError"
    assert receipt["cleaned"] is True
    assert not Path(receipt["temporary_root"]).exists()
    assert json.loads((evidence / "provider-audit.json").read_text())["provider_result"]["verdict"] == "rejected"
