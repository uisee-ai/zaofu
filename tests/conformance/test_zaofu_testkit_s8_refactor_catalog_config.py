"""The catalog's full ZfConfig/profile/semantic-control deployment contract."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from tests.conformance.test_zaofu_testkit_s8_refactor_two_lane import assert_two_lane_delivery
from tests.e2e.test_orchestrator_agent_semantic_control_mock_e2e import _candidate
from tests.testkit.issue_route import isolate_issue_processes
from tests.testkit.refactor_catalog_route import CatalogRefactorRoute
from zf.core.config.loader import ConfigError, load_config
from zf.core.profile.apply import materialize_flow_assets
from zf.core.profile.flows import flow_path
from zf.runtime.orchestrator_agent_policy import (
    checkpoint_policy,
    shadow_checkpoint_selection,
)
from zf.runtime.orchestrator_agent_plan_adoption import plan_candidate_checkpoint_state


CATALOG_ID = "refactor-lane-v3-codex"


def _materialized_catalog(tmp_path: Path) -> tuple[Path, object, dict]:
    source = flow_path(CATALOG_ID)
    assert source is not None and source.is_file()
    root = tmp_path / "catalog-project"
    root.mkdir()
    config_path = root / "zf.yaml"
    shutil.copy2(source, config_path)
    materialized = materialize_flow_assets(
        CATALOG_ID,
        root,
        config_path=config_path,
    )
    return config_path, load_config(config_path), materialized


def test_refactor_catalog_materializes_full_config_and_profile_closure(tmp_path: Path) -> None:
    config_path, config, materialized = _materialized_catalog(tmp_path)

    assert materialized["profile_sources"] == ["common/profiles.yaml"]
    assert materialized["rewrote_skill_sources"] is True
    assert (config_path.parent / "common/profiles.yaml").is_file()
    assert (config_path.parent / "skills").is_dir()

    sources = list(config.config_sources)
    assert [item["kind"] for item in sources] == ["ConfigProfile", "ProfileSource"]
    assert sources[0]["name"] == "refactor-controller-runtime/v3"
    assert sources[1]["name"] == "common/profiles.yaml"
    assert sources[1]["path"] == str(config_path.parent / "common/profiles.yaml")
    assert all(item.get("sha256") for item in sources)

    assert config.goal.enabled is True
    assert config.runtime.skills.strict is True
    assert config.runtime.workdirs.enabled is True
    assert config.runtime.workdirs.mode == "worktree"
    assert config.runtime.git.candidate_base_ref == "main"
    assert config.runtime.git.remote_policy == "local"
    assert len(config.roles) == 13
    assert config.workflow.pipelines[0].lane_count == 2
    assert config.workflow.flow_metadata["flow_kind"] == "refactor"
    assert config.workflow.flow_metadata["delivery_policy"] == "ship_candidate"
    assert config.workflow.flow_metadata["result_protocol"]["mode"] == "blocking"
    assert config.workflow.flow_metadata["artifact_package"]["mode"] == "blocking"
    assert config.workflow.flow_metadata["product_acceptance"]["mode"] == "blocking"

    orchestration = config.workflow.orchestration
    assert orchestration.mode == "semantic_control"
    assert orchestration.checkpoints == ["plan_candidate"]
    assert orchestration.checkpoint_policies == {"plan_candidate": "shadow"}
    assert orchestration.shadow_sample_percent == 25
    assert orchestration.flow_policies["workflow"].mode == "exception_advisor"
    assert orchestration.flow_policies["research"].mode == "exception_advisor"


def test_materialized_catalog_preserves_semantic_control_sampling_contract(
    tmp_path: Path,
) -> None:
    _, config, _ = _materialized_catalog(tmp_path)

    assert checkpoint_policy(config, "plan_candidate", flow_kind="refactor") == "shadow"
    assert checkpoint_policy(config, "pre_impl", flow_kind="refactor") == ""

    ordinary = shadow_checkpoint_selection(
        config,
        "plan_candidate",
        workflow_run_id="run-catalog-ordinary",
        revision="r1",
        flow_kind="refactor",
    )
    risky = shadow_checkpoint_selection(
        config,
        "plan_candidate",
        workflow_run_id="run-catalog-risky",
        revision="r2",
        flow_kind="refactor",
        payload={"risk_level": "critical"},
    )
    assert ordinary.sample_percent == 25
    assert 0 <= ordinary.bucket < 100
    assert risky.selected is True
    assert risky.reason == "risk_override"
    assert "risk_level:critical" in risky.risk_signals


def test_catalog_deployment_fails_closed_when_profile_source_is_missing(tmp_path: Path) -> None:
    source = flow_path(CATALOG_ID)
    assert source is not None
    config_path = tmp_path / "zf.yaml"
    shutil.copy2(source, config_path)
    with pytest.raises(ConfigError, match="profile_sources.*did not match any files"):
        load_config(config_path)


def test_catalog_sampled_checkpoint_submits_after_real_dispatch(tmp_path, monkeypatch):
    """Risk override exercises the sampled path without changing the 25% policy."""
    forbidden = isolate_issue_processes(tmp_path, monkeypatch)
    route = CatalogRefactorRoute(tmp_path / "target", monkeypatch)
    trigger, loaded = _candidate(route.runtime, flow_kind="refactor", package_mode="blocking")
    assert not route.events("discriminator.failed")
    trigger.payload["risk_level"] = "critical"
    state = plan_candidate_checkpoint_state(
        route.runtime, stage_id="flow-lanes-impl", trigger_event=trigger,
        loaded=loaded, trace_id=loaded.workflow_run_id,
    )
    assert state.enabled and not state.blocking
    assert route.config.workflow.orchestration.shadow_sample_percent == 25
    with route.evidence.guard("sampled_checkpoint", {"operation_id": state.operation_id}):
        for _ in range(3):
            route.tick()
        [submitted] = route.events("orchestrator.semantic.decision.submitted")
        [shadowed] = route.events("orchestrator.semantic.decision.shadowed")
        assert submitted.payload["operation_id"] == shadowed.payload["operation_id"] == state.operation_id
        assert route.catalog_results == {state.operation_id: submitted.id}
        assert not route.events("orchestrator.semantic.decision.applied")
        assert not route.events("orchestrator.semantic.decision.rejected")
        assert not route.events("orchestrator.semantic.checkpoint.skipped")
        assert len([row for row in route.transport.sent if row[0] == "orchestrator"]) == 1
        route.reopen()
        route.tick()
        assert len(route.events("orchestrator.semantic.decision.submitted")) == 1
        assert not forbidden


@pytest.mark.parametrize("reopen", [False, True])
def test_full_catalog_drives_two_lane_delivery_and_shadow_checkpoint(tmp_path, monkeypatch, reopen):
    cls = CatalogRefactorRoute
    forbidden = isolate_issue_processes(tmp_path, monkeypatch, extra_commands=(
        cls.probe_command(cls.second_probe), cls.probe_command(cls.product_probe),
    ))
    route = cls(tmp_path / "target", monkeypatch)
    with route.evidence.guard("full_catalog", {"reopen": reopen}):
        assert route.config.runtime.skills.strict is True
        assert {role.lifecycle.mode for role in route.config.roles} == {"on_demand", "resident"}
        assert {role.backend for role in route.config.roles} == {"codex"}
        assert route.config.workflow.candidate_quality_source == "task_contract_required"
        assert route.config.workflow.work_units.split_quality.mode == "blocking"
        assert route.config.workflow.orchestration.shadow_sample_percent == 25
        assert checkpoint_policy(route.config, "plan_candidate", flow_kind="refactor") == "shadow"
        assert route.config.config_sources[1]["path"] == str(route.root / "common/profiles.yaml")
        assert route.config.runtime.git.auto_ship_on_judge_passed is True
        assert_two_lane_delivery(route, reverse_and_reopen=reopen)
        requested = route.events("orchestrator.semantic.checkpoint.requested")
        skipped = route.events("orchestrator.semantic.checkpoint.skipped")
        assert requested or skipped
        assert {e.payload["operation_id"] for e in requested} == set(route.catalog_results)
        shadowed = route.events("orchestrator.semantic.decision.shadowed")
        assert {e.payload["operation_id"] for e in shadowed} == set(route.catalog_results)
        assert not route.events("orchestrator.semantic.decision.applied")
        assert not forbidden
