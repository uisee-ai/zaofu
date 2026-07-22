"""Current orchestration-mode ownership contracts from design 142."""

from pathlib import Path

from zf.core.config.loader import load_config
from zf.core.config.presets import get_preset


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_safe_team_explicitly_enables_layer2_decision_maker() -> None:
    preset = get_preset("safe-team")
    orchestrator = next(
        role for role in preset["roles"] if role["name"] == "orchestrator"
    )

    assert orchestrator["transport"] == "stream-json"
    assert any("zf kanban" in tool for tool in orchestrator["allowed_tools"])


def test_product_controller_profiles_declare_exception_only_layer2() -> None:
    controller_dir = ROOT / "examples" / "prod" / "controller"

    for filename in (
        "issue-fanout-v3.yaml",
        "prd-fanout-v3.yaml",
        "refactor-lane-v3.yaml",
    ):
        config = load_config(controller_dir / filename)
        orchestrator = next(role for role in config.roles if role.name == "orchestrator")
        stage_roles = {
            role
            for stage in config.workflow.stages
            for role in stage.roles
        }

        assert orchestrator.triggers == [
            "dispatch.silent_stall",
            "orchestrator.rework.triage.requested",
        ], filename
        assert orchestrator.publishes == [
            "orchestrator.rework.triage.recorded",
        ], filename
        assert "orchestrator" not in stage_roles, filename
        assert config.workflow.stages, filename
        assert config.workflow.pipelines, filename


def test_all_product_controller_variants_declare_orchestrator_agent() -> None:
    controller_dir = ROOT / "examples" / "prod" / "controller"

    for path in sorted(controller_dir.glob("*-v3*.yaml")):
        config = load_config(path)
        orchestrators = [role for role in config.roles if role.name == "orchestrator"]

        assert len(orchestrators) == 1, path.name
        assert orchestrators[0].transport == "tmux", path.name
        assert "zf-yoke-orchestrator-role-context" in orchestrators[0].skills, path.name
