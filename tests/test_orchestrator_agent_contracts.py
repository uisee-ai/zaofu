from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import ConfigError, load_config
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.orchestrator_agent_contracts import (
    OrchestratorAgentContractError,
    normalize_orchestration_decision,
    normalize_owner_delivery_narrative,
    normalize_run_orchestration_plan,
)
from zf.runtime.orchestrator_types import OrchestratorDecision
from zf.runtime.workflow_runtime import (
    WorkflowRuntimeCoordinator,
    WorkflowRuntimeDecision,
)
from zf.runtime.workflow_runtime_types import (
    WorkflowRuntimeDecision as CanonicalWorkflowRuntimeDecision,
)


DIGEST = "a" * 64


def _write_config(tmp_path: Path, workflow: str) -> Path:
    path = tmp_path / "zf.yaml"
    path.write_text(
        "\n".join((
            "version: '1.0'",
            "project:",
            "  name: oa-contract-test",
            "  workspace: .",
            "workflow:",
            *[f"  {line}" if line else "" for line in workflow.splitlines()],
            "roles:",
            "  - name: reader",
            "    backend: mock",
            "    role_kind: reader",
            "",
        )),
        encoding="utf-8",
    )
    return path


def _decision(*, action: str = "adopt", checkpoint: str = "plan_candidate") -> dict:
    return {
        "schema_version": "orchestration-decision.v1",
        "identity": {
            "operation_id": "wop-plan-review",
            "workflow_run_id": "run-1",
            "checkpoint": checkpoint,
            "input_digest": DIGEST,
            "effective_config_digest": DIGEST,
            "plan_artifact_package_ref": "artifacts/plan/package.json",
            "plan_artifact_package_digest": DIGEST,
            "task_map_generation": "generation-1",
        },
        "decision": action,
        "reason_codes": ["coverage_complete"],
        "summary": "The Plan package covers the admitted claims.",
        "affected_work_units": [],
        "required_followup": "continue",
        "expected_outcome": "materialize",
        "confidence": 0.9,
    }


def test_orchestration_config_defaults_to_exception_advisor(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, "{}"))

    assert config.workflow.orchestration.mode == "exception_advisor"
    assert config.workflow.orchestration.checkpoints == []


def test_orchestration_config_accepts_explicit_semantic_control(tmp_path: Path) -> None:
    config = load_config(_write_config(
        tmp_path,
        """orchestration:
  mode: semantic_control
  checkpoints: [plan_candidate, semantic_failure]
  checkpoint_policies:
    plan_candidate: shadow
    semantic_failure: shadow
  shadow_sample_percent: 25
  oa_profile: oa-primary
  normal_frontier_owner: wrc
  max_plan_revisions: 3
  no_progress_limit: 2""",
    ))

    policy = config.workflow.orchestration
    assert policy.mode == "semantic_control"
    assert policy.checkpoints == ["plan_candidate", "semantic_failure"]
    assert policy.checkpoint_policies["plan_candidate"] == "shadow"
    assert policy.shadow_sample_percent == 25
    assert policy.oa_profile == "oa-primary"
    assert policy.normal_frontier_owner == "wrc"


def test_orchestration_config_rejects_unknown_frontier_owner(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="normal_frontier_owner"):
        load_config(_write_config(
            tmp_path,
            """orchestration:
  normal_frontier_owner: supervisor""",
        ))


@pytest.mark.parametrize("value", [-1, 101, "invalid"])
def test_orchestration_config_rejects_invalid_shadow_sample_percent(
    tmp_path: Path,
    value: object,
) -> None:
    with pytest.raises(ConfigError, match="shadow_sample_percent"):
        load_config(_write_config(
            tmp_path,
            (
                "orchestration:\n"
                "  mode: semantic_control\n"
                "  checkpoints: [plan_candidate]\n"
                "  checkpoint_policies: {plan_candidate: shadow}\n"
                f"  shadow_sample_percent: {value}"
            ),
        ))


@pytest.mark.parametrize("flow_kind", ["prd", "issue", "refactor"])
def test_orchestration_config_accepts_flow_scoped_authority(
    tmp_path: Path,
    flow_kind: str,
) -> None:
    config = load_config(_write_config(
        tmp_path,
        f"""orchestration:
  mode: exception_advisor
  flow_policies:
    {flow_kind}:
      mode: semantic_control
      pilot_id: {flow_kind}-plan-candidate-test
      checkpoints: [plan_candidate]
      checkpoint_policies:
        plan_candidate: blocking
    research:
      mode: exception_advisor
kind_routes:
  {flow_kind}:
    pattern_id: test-route
    default_tier: standard
stages:
  - id: test-route
    trigger: test.requested
    topology: fanout_reader
    roles: [reader]""",
    ))

    policy = config.workflow.orchestration
    assert policy.mode == "exception_advisor"
    assert policy.flow_policies[flow_kind].mode == "semantic_control"
    assert policy.flow_policies["research"].checkpoints == []


@pytest.mark.parametrize(
    "workflow",
    [
        (
            "orchestration:\n  mode: semantic_control\n"
            "  pilot_id: root-pilot\n"
            "  checkpoints: [plan_candidate]\n"
            "  checkpoint_policies: {plan_candidate: blocking}"
        ),
        (
            "orchestration:\n  flow_policies:\n    workflow:\n"
            "      mode: semantic_control\n"
            "      pilot_id: workflow-pilot\n"
            "      checkpoints: [plan_candidate]\n"
            "      checkpoint_policies: {plan_candidate: blocking}"
        ),
        (
            "orchestration:\n  flow_policies:\n    prd:\n"
            "      mode: semantic_control\n"
            "      checkpoints: [plan_candidate]\n"
            "      checkpoint_policies: {plan_candidate: blocking}"
        ),
        (
            "orchestration:\n  flow_policies:\n    prd:\n"
            "      mode: semantic_control\n"
            "      pilot_id: too-wide\n"
            "      checkpoints: [plan_candidate, pre_impl]\n"
            "      checkpoint_policies: "
            "{plan_candidate: blocking, pre_impl: blocking}"
        ),
    ],
)
def test_orchestration_config_rejects_blocking_outside_product_flow_pilot(
    tmp_path: Path,
    workflow: str,
) -> None:
    with pytest.raises(ConfigError):
        load_config(_write_config(tmp_path, workflow))


@pytest.mark.parametrize("tier", ["", "micro", "light"])
def test_orchestration_config_rejects_blocking_for_small_or_unrouted_flow(
    tmp_path: Path,
    tier: str,
) -> None:
    route = (
            "\nkind_routes:\n"
            "  issue:\n"
            "    pattern_id: test-route\n"
            f"    default_tier: {tier}\n"
            "stages:\n"
            "  - id: test-route\n"
            "    trigger: test.requested\n"
            "    topology: fanout_reader\n"
            "    roles: [reader]"
        if tier
        else ""
    )
    workflow = (
        "orchestration:\n"
        "  flow_policies:\n"
        "    issue:\n"
        "      mode: semantic_control\n"
        "      pilot_id: issue-light-pilot\n"
        "      checkpoints: [plan_candidate]\n"
        "      checkpoint_policies: {plan_candidate: blocking}"
        f"{route}"
    )

    with pytest.raises(ConfigError, match="standard or full"):
        load_config(_write_config(tmp_path, workflow))


@pytest.mark.parametrize(
    "workflow",
    [
        "orchestration:\n  mode: magic",
        "orchestration:\n  mode: semantic_control",
        "orchestration:\n  checkpoints: [plan_candidate]",
        (
            "orchestration:\n  mode: semantic_control\n"
            "  checkpoints: [plan_candidate]\n"
            "  checkpoint_policies: {semantic_failure: blocking}"
        ),
        "orchestration:\n  flow_policies:\n    feat: {mode: exception_advisor}",
        (
            "orchestration:\n  flow_policies:\n    prd:\n"
            "      mode: semantic_control"
        ),
    ],
)
def test_orchestration_config_rejects_ambiguous_authority(
    tmp_path: Path,
    workflow: str,
) -> None:
    with pytest.raises(ConfigError):
        load_config(_write_config(tmp_path, workflow))


def test_plan_candidate_decision_is_normalized() -> None:
    normalized = normalize_orchestration_decision(_decision())
    assert normalized["decision"] == "adopt"
    assert normalized["explanation_status"] == "complete"


def test_legacy_decision_without_summary_is_degraded() -> None:
    value = _decision()
    value.pop("summary")

    normalized = normalize_orchestration_decision(value)

    assert normalized["summary"] == ""
    assert normalized["explanation_status"] == "degraded"


def test_explicit_empty_legacy_decision_summary_is_degraded() -> None:
    value = _decision()
    value["summary"] = ""

    normalized = normalize_orchestration_decision(value)

    assert normalized["summary"] == ""
    assert normalized["explanation_status"] == "degraded"


def test_checkpoint_rejects_disallowed_action() -> None:
    with pytest.raises(OrchestratorAgentContractError, match="not allowed"):
        normalize_orchestration_decision(_decision(action="rework"))


def test_mutating_decision_requires_typed_delta() -> None:
    value = _decision(action="rework", checkpoint="semantic_failure")

    with pytest.raises(OrchestratorAgentContractError, match="requires"):
        normalize_orchestration_decision(value)


def test_semantic_rework_requires_exact_target_identity() -> None:
    value = _decision(action="rework", checkpoint="semantic_failure")
    value["delta"] = {
        "schema_version": "orchestration-delta.v1",
        "identity": {
            "operation_id": "wop-plan-review",
            "workflow_run_id": "run-1",
            "checkpoint": "semantic_failure",
            "input_digest": DIGEST,
        },
        "directives": [{
            "directive_id": "directive-1",
            "action": "rework",
            "target": {"task_id": "TASK-1"},
            "basis_refs": [],
            "required_actions": ["repair the gap"],
        }],
    }

    with pytest.raises(OrchestratorAgentContractError, match="target missing"):
        normalize_orchestration_decision(value)

    value["delta"]["directives"][0]["target"] = {
        "task_id": "TASK-1",
        "stage_id": "impl",
        "attempt_id": "attempt-1",
        "role_instance": "dev-1",
    }
    assert normalize_orchestration_decision(value)["decision"] == "rework"


def test_run_orchestration_plan_requires_acyclic_graph() -> None:
    plan = {
        "schema_version": "run-orchestration-plan.v1",
        "identity": {
            "operation_id": "wop-run-start",
            "workflow_run_id": "run-1",
            "goal_id": "GOAL-1",
            "plan_revision": 1,
            "effective_config_digest": DIGEST,
            "run_contract_ref": "run-contracts/current.json",
            "run_contract_digest": DIGEST,
        },
        "goal_model": {
            "objective": "Deliver the goal",
            "mandatory_claims": ["CLAIM-1"],
        },
        "graph": {
            "work_units": [
                {"work_unit_id": "unit-a"},
                {"work_unit_id": "unit-b"},
            ],
            "edges": [
                {"from": "unit-a", "to": "unit-b"},
                {"from": "unit-b", "to": "unit-a"},
            ],
        },
        "delegation": [],
        "context_routes": [],
    }

    with pytest.raises(OrchestratorAgentContractError, match="acyclic"):
        normalize_run_orchestration_plan(plan)


def test_orchestration_delta_rejects_unbounded_directive_set() -> None:
    value = _decision(action="rebind", checkpoint="semantic_failure")
    value["affected_work_units"] = ["TASK-1"]
    value["delta"] = {
        "schema_version": "orchestration-delta.v1",
        "identity": {
            "operation_id": "wop-plan-review",
            "workflow_run_id": "run-1",
            "checkpoint": "semantic_failure",
            "input_digest": DIGEST,
        },
        "directives": [
            {
                "directive_id": f"directive-{index}",
                "action": "rebind",
                "target": {
                    "task_id": f"TASK-{index}",
                    "stage_id": "impl",
                    "attempt_id": f"attempt-{index}",
                    "role_instance": "dev-1",
                },
                "basis_refs": [],
                "required_actions": ["rebind exact target"],
            }
            for index in range(9)
        ],
    }

    with pytest.raises(OrchestratorAgentContractError, match="bounded limit"):
        normalize_orchestration_decision(value)


def test_owner_narrative_requires_cited_outcome() -> None:
    narrative = {
        "schema_version": "owner-delivery-narrative.v1",
            "identity": {
                "operation_id": "wop-owner-delivery",
                "workflow_run_id": "run-1",
                "terminal_event_id": "evt-terminal",
                "terminal_event_type": "run.goal.completed",
                "dossier_ref": "projections/goals/run-1/goal-dossier.v1.json",
                "dossier_source_fingerprint": DIGEST,
                "completion_receipt_ref": (
                    "projections/goals/run-1/goal-completion-receipt.v1.json"
                ),
                "completion_receipt_fingerprint": DIGEST,
        },
        "status": "completed",
        "executive_summary": "Delivered the requested behavior.",
        "delivered_outcomes": [{
            "claim_ids": ["CLAIM-1"],
            "task_ids": ["TASK-1"],
            "result_refs": [],
            "evidence_refs": [],
            "narrative": "The claim is closed.",
        }],
        "decisions_and_tradeoffs": [],
        "remaining_risks": [],
        "recommended_next_actions": [],
    }

    with pytest.raises(OrchestratorAgentContractError, match="requires a result"):
        normalize_owner_delivery_narrative(narrative)


def test_workflow_runtime_facade_preserves_legacy_runtime_types() -> None:
    assert WorkflowRuntimeCoordinator is Orchestrator
    assert WorkflowRuntimeDecision is OrchestratorDecision
    assert WorkflowRuntimeDecision is CanonicalWorkflowRuntimeDecision
    assert WorkflowRuntimeCoordinator.__name__ == "WorkflowRuntimeCoordinator"
    assert WorkflowRuntimeDecision.__name__ == "WorkflowRuntimeDecision"


@pytest.mark.parametrize("checkpoint", ["pre_impl", "semantic_failure", "pre_closeout"])
def test_product_pilot_loads_implemented_blocking_checkpoints(tmp_path, checkpoint):
    config = load_config(_write_config(tmp_path, f"""orchestration:
  flow_policies:
    prd:
      mode: semantic_control
      pilot_id: hello-pipeline
      checkpoints: [plan_candidate, {checkpoint}]
kind_routes:
  prd:
    pattern_id: test-route
    default_tier: full
stages:
  - id: test-route
    trigger: test.requested
    topology: fanout_reader
    roles: [reader]"""))
    assert checkpoint in config.workflow.orchestration.flow_policies["prd"].checkpoints


@pytest.mark.parametrize("backends", ["backend: codex", "backends: [claude-code, codex]\n    replicas: 2"])
def test_loader_rejects_codex_stream_json(tmp_path, backends):
    path = tmp_path / "zf.yaml"
    path.write_text("project: {name: transport-test}\nroles:\n  - name: worker\n    " + backends + "\n    transport: stream-json\n")
    with pytest.raises(ConfigError, match="stream-json.*claude-code"):
        load_config(path)
