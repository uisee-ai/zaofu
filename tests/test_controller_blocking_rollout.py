from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from zf.core.config.loader import load_config
from zf.core.config.render import renderable_config_to_primitive
from zf.runtime.call_result_envelope import write_immutable_json_sidecar
from zf.runtime.artifact_package_policy import effective_artifact_package_mode
from zf.runtime.plan_artifact_package import (
    build_plan_artifact_package,
    write_plan_artifact_package,
)
from zf.runtime.run_contract import (
    build_run_contract,
    stable_json_sha256,
    write_run_contract_snapshot,
)


CONTROLLER_CONFIGS = (
    "prd-fanout-v3.yaml",
    "prd-fanout-v3-claude.yaml",
    "prd-light-v3.yaml",
    "prd-light-v3-claude.yaml",
    "issue-fanout-v3.yaml",
    "issue-fanout-v3-claude.yaml",
    "refactor-lane-v3.yaml",
    "refactor-lane-v3-claude.yaml",
    "issue-fanout-v3-oa-pilot.yaml",
    "prd-fanout-v3-oa-pilot.yaml",
    "refactor-lane-v3-oa-pilot.yaml",
)

GENERAL_CONTROLLER_CONFIGS = (
    "general-workflow-v3.yaml",
    "general-workflow-v3-claude.yaml",
)

ALL_CONTROLLER_CONFIGS = CONTROLLER_CONFIGS + GENERAL_CONTROLLER_CONFIGS

CODEX_CONTROLLER_CONFIGS = (
    "prd-fanout-v3.yaml",
    "prd-light-v3.yaml",
    "issue-fanout-v3.yaml",
    "refactor-lane-v3.yaml",
    "general-workflow-v3.yaml",
    "issue-fanout-v3-oa-pilot.yaml",
    "prd-fanout-v3-oa-pilot.yaml",
    "refactor-lane-v3-oa-pilot.yaml",
)

PILOT_CONTROLLER_CONFIGS = {
    "issue-fanout-v3-oa-pilot.yaml": "issue",
    "prd-fanout-v3-oa-pilot.yaml": "prd",
    "refactor-lane-v3-oa-pilot.yaml": "refactor",
}


@pytest.mark.parametrize("name", CONTROLLER_CONFIGS)
def test_production_controllers_pin_blocking_artifact_handoff(name: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "examples" / "prod" / "controller" / name)

    assert config.workflow.flow_metadata["result_protocol"]["mode"] == "blocking"
    assert config.workflow.flow_metadata["artifact_package"]["mode"] == "blocking"


@pytest.mark.parametrize(
    "name",
    [
        "prd-fanout-v3.yaml",
        "issue-fanout-v3.yaml",
        "refactor-lane-v3.yaml",
        "issue-fanout-v3-oa-pilot.yaml",
        "prd-fanout-v3-oa-pilot.yaml",
        "refactor-lane-v3-oa-pilot.yaml",
    ],
)
def test_codex_controllers_pin_file_based_semantic_submit(name: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(
        root / "examples" / "prod" / "controller" / name
    )

    assert config.workflow.flow_metadata["result_protocol"][
        "semantic_submit_profiles"
    ] == {
        "workflow-read": "blocking",
        "plan-synth": "blocking",
        "implementation": "blocking",
        "task-verify": "blocking",
        "candidate-verify": "blocking",
        "thin-judge-goal-closure": "blocking",
    }


@pytest.mark.parametrize("name", GENERAL_CONTROLLER_CONFIGS)
def test_general_controllers_pin_verified_artifact_delivery(name: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "examples" / "prod" / "controller" / name)
    metadata = config.workflow.flow_metadata

    assert metadata["result_protocol"] == {
        "mode": "blocking",
        "semantic_submit_profiles": {
            "artifact-delivery": "blocking",
            "workflow-read": "blocking",
        },
    }
    assert metadata["completion_profile"] == "artifact_delivery"
    assert metadata["delivery_policy"] == "report_only"
    assert "artifact_package" not in metadata


@pytest.mark.parametrize("name", ALL_CONTROLLER_CONFIGS)
def test_production_controllers_allow_long_horizon_with_cost_and_usage_guards(name: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "examples" / "prod" / "controller" / name)

    assert config.workflow.run_limits.timeout_seconds == 0
    assert config.workflow.run_limits.token_budget == 10_000_000
    assert config.workflow.run_limits.cost_budget_usd == 40
    profile = config.workflow.execution_profiles["bounded-direct-v1"]
    assert profile.limits.timeout_seconds == 900
    assert profile.limits.max_usage_samples == 60
    assert profile.limits.token_budget == 1_500_000
    assert profile.limits.cost_budget_usd == 8
    assert all(
        role.execution.default_profile == "bounded-direct-v1"
        and "bounded-direct-v1" in role.execution.profile_allowlist
        for role in config.roles
    )


@pytest.mark.parametrize("name", CODEX_CONTROLLER_CONFIGS)
def test_codex_controllers_pin_bounded_reasoning_effort(name: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "examples" / "prod" / "controller" / name)

    assert all(
        role.model_reasoning_effort == "high"
        for role in config.roles
        if role.backend == "codex"
    )


@pytest.mark.parametrize(
    ("name", "flow_kind"),
    PILOT_CONTROLLER_CONFIGS.items(),
)
def test_blocking_pilot_round_trips_through_rendered_config(
    tmp_path: Path,
    name: str,
    flow_kind: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "examples" / "prod" / "controller" / name
    rendered_path = tmp_path / name
    rendered_path.write_text(
        yaml.safe_dump(
            renderable_config_to_primitive(load_config(source)),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    rendered = load_config(rendered_path)
    orchestration = rendered.workflow.orchestration
    blocking = [
        (kind, checkpoint)
        for kind, policy in orchestration.flow_policies.items()
        for checkpoint in policy.checkpoints
        if policy.checkpoint_policies.get(checkpoint, "blocking")
        == "blocking"
    ]

    assert orchestration.mode == "exception_advisor"
    assert blocking == [(flow_kind, "plan_candidate")]
    assert orchestration.flow_policies[flow_kind].pilot_id


def test_blocking_pilots_are_not_default_catalog_routes(monkeypatch) -> None:
    monkeypatch.delenv("ZF_EXAMPLES_DIR", raising=False)
    from zf.core.profile.flows import flow_id_for_intent, list_flows_detailed

    catalog = {
        item["id"]: item for item in list_flows_detailed()
    }
    for flow_id in (
        "issue-fanout-v3-oa-pilot-codex",
        "prd-fanout-v3-oa-pilot-codex",
        "refactor-lane-v3-oa-pilot-codex",
    ):
        assert catalog[flow_id]["preferred"] is False

    assert flow_id_for_intent("build", "codex") == "prd-fanout-v3-codex"
    assert flow_id_for_intent("maintain", "codex") == "issue-fanout-v3-codex"
    assert flow_id_for_intent("refactor", "codex") == "refactor-lane-v3-codex"


def test_claude_refactor_critic_does_not_require_unwritable_result_file() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(
        root / "examples" / "prod" / "controller"
        / "refactor-lane-v3-claude.yaml"
    )

    profiles = config.workflow.flow_metadata["result_protocol"][
        "semantic_submit_profiles"
    ]
    assert "plan-synth" not in profiles


def test_run_contract_records_controller_artifact_package_mode(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config_path = root / "examples/prod/controller/prd-light-v3.yaml"
    config = load_config(config_path)

    contract = build_run_contract(
        config,
        config_path=config_path,
        project_root=root,
        state_dir=tmp_path / ".zf",
    )

    assert contract["protocols"]["artifact_package"] == {
        "schema_version": "plan-artifact-package.v1",
        "mode": "blocking",
    }


def test_package_mode_is_run_pinned_across_upgrade_and_rollback(
    tmp_path: Path,
) -> None:
    run_contract_body = {
        "schema_version": "run-contract.v1",
        "workflow": {"kind": "prd"},
    }
    run_contract = {
        **run_contract_body,
        "contract_digest": stable_json_sha256(run_contract_body),
    }
    run_contract_ref = write_run_contract_snapshot(tmp_path, run_contract)
    port = write_immutable_json_sidecar(
        tmp_path,
        {"schema_version": "task-map.v1", "tasks": []},
        root="fixtures",
        kind="task_map",
        schema_version="task-map.v1",
        created_by="test",
    )
    produced = [{
        "logical_name": "task_map",
        "artifact_kind": "task_map",
        "schema_version": "task-map.v1",
        "producer_stage_id": "plan",
        "ref": port["ref"],
        "sha256": port["sha256"],
    }]

    legacy = build_plan_artifact_package(
        workflow_run_id="legacy-run",
        flow_kind="prd",
        producer_stage_id="plan",
        run_contract=run_contract_ref,
        plan_revision="R1",
        task_map_generation="G1",
        produced=produced,
    )
    legacy_ref = write_plan_artifact_package(tmp_path, legacy)
    assert effective_artifact_package_mode(
        state_dir=tmp_path,
        payload={
            "plan_artifact_package_ref": legacy_ref["ref"],
            "plan_artifact_package_digest": legacy_ref["sha256"],
        },
        metadata={"artifact_package": {"mode": "blocking"}},
    ) == "shadow"

    blocking = build_plan_artifact_package(
        workflow_run_id="blocking-run",
        flow_kind="prd",
        producer_stage_id="plan",
        run_contract=run_contract_ref,
        plan_revision="R2",
        task_map_generation="G2",
        produced=produced,
        package_mode="blocking",
    )
    blocking_ref = write_plan_artifact_package(tmp_path, blocking)
    assert effective_artifact_package_mode(
        state_dir=tmp_path,
        payload={
            "artifact_package_mode": "shadow",
            "plan_artifact_package_ref": blocking_ref["ref"],
            "plan_artifact_package_digest": blocking_ref["sha256"],
        },
        metadata={"artifact_package": {"mode": "shadow"}},
    ) == "blocking"
