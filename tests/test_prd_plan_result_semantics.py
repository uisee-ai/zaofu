from pathlib import Path
from types import SimpleNamespace

import pytest

from zf.core.config.loader import load_config
from zf.core.events.model import ZfEvent
from zf.runtime.fanout_dispatch_lineage import apply_stage_result_semantics
from zf.runtime.workflow_read_result import normalize_workflow_read_result


@pytest.mark.parametrize("controller", [
    "prd-fanout-v3.yaml",
    "prd-task-pipeline-v4-canary.yaml",
])
@pytest.mark.parametrize("provider_failed", [False, True])
def test_prd_planner_handoff_keeps_critic_decision_independent(
    controller: str, provider_failed: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ZF_TASK_PIPELINE_MODE", "blocking")
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "examples/prod/controller" / controller)
    stage = next(s for s in config.workflow.stages if s.id == "prd-plan")
    child = SimpleNamespace(payload={})
    apply_stage_result_semantics(stage, [child])
    report = {
        "status": "passed", "recommendation": "abstain", "findings": [],
        "summary": "Plan artifacts produced; independent Critic must review.",
        "plan_artifact_ref": "docs/plans/plan.md",
        "task_map_ref": "artifacts/plan/task_map.json",
    }
    if provider_failed:
        report["provider_error"] = "provider exited before finishing the result"
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="prd.plan.child.completed", payload={**child.payload, "report": report},
    ))
    assert issues == []
    assert result["execution_status"] == ("failed" if provider_failed else "completed")
    assert result["verdict"] == ("abstained" if provider_failed else "passed")
    assert result["recommendation"] == "abstain"
    assert stage.aggregate.synth_role == "plan-critic"


def test_review_rejection_remains_a_failure_without_production_semantics() -> None:
    result, issues = normalize_workflow_read_result(ZfEvent(
        type="review.completed", payload={"report": {
            "status": "passed", "recommendation": "reject",
            "summary": "Missing mandatory acceptance coverage.", "findings": [],
        }},
    ))
    assert issues == []
    assert result["verdict"] == "rejected"
