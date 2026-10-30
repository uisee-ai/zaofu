"""Blind semantics, bounded planning and Generic Workflow caller wiring."""
from copy import deepcopy

import pytest

from tests.e2e import evidence_review_194 as pilot


def _report(generation):
    values = [("C1", "supported", ["S1"]),
              ("C2", "conflict", ["S2", "S3"]), ("C3", "unknown", ["S5"])] if generation == 1 else [
              ("C1", "supported", ["S1"]), ("C2", "refuted", ["S4"]), ("C3", "supported", ["S6"])]
    return {"claims": [{"id": key, "status": status, "sources": sources, "reason": "source-grounded"}
                       for key, status, sources in values]}


def test_grader_rejects_fabricated_citation_and_omitted_claim():
    report = _report(2)
    assert pilot.hidden_grade(report, 2) == []
    report["claims"][0]["sources"].append("invented")
    assert pilot.hidden_grade(report, 2) == ["C1"]
    report["claims"].pop()
    assert pilot.hidden_grade(report, 2) == ["claim_coverage"]


def test_invalid_dynamic_plan_stops_before_children(tmp_path):
    class Planner:
        def call(self, *args):
            return {"groups": [["C1"], ["C1", "C2", "C3"]]}
    with pytest.raises(RuntimeError, match="bounds"):
        pilot.author(Planner(), "dynamic", 1)


def test_real_pilot_adapter_preserves_generic_admission_and_terminal(tmp_path, monkeypatch):
    prompts = []
    def fake_call(self, name, prompt, schema):
        prompts.append(prompt)
        generation = int(name[1])
        if name.endswith("verifier"):
            assert "expected" not in prompt and "hidden_grade" not in prompt
            return {"valid": True, "ready": generation == 2, "issues": []}
        return _report(generation)
    monkeypatch.setattr(pilot.Provider, "call", fake_call)
    result = pilot.run_pilot({"identity": {"model": "mock"}, "arm": "direct"}, tmp_path)
    assert result["metrics"]["accepted"] and result["metrics"]["integrated"]
    assert result["runtime_receipts"]["semantic_replan_count"] == 1
    assert result["runtime_receipts"]["terminal_event_id"]
    assert len(prompts) == 4


def test_manifest_freezes_actual_input_source_and_budget():
    from tests.e2e.staged_workflow_comparison import validate_manifest
    manifest = pilot.comparison_manifest()
    validate_manifest(manifest)
    manifest["budget"]["max_tokens"] += 1
    with pytest.raises(ValueError, match="budget_digest"):
        validate_manifest(manifest)


def test_failed_provider_leaves_simulation_closeout(tmp_path, monkeypatch):
    from zf.core.events import EventLog
    from zf.core.config.loader import load_config
    def fail(*args, **kwargs):
        raise RuntimeError("provider failed")
    monkeypatch.setattr(pilot.Provider, "call", fail)
    with pytest.raises(RuntimeError, match="provider failed"):
        pilot.run_pilot({"identity": {"model": "mock"}, "arm": "direct"}, tmp_path)
    project = tmp_path / "runtime/project"
    state = project / load_config(project / "zf.yaml").project.state_dir
    assert [e.payload["status"] for e in EventLog(state / "events.jsonl").read_all() if e.type == "simulation.done"] == ["aborted"]
