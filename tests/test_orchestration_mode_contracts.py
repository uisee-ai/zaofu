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


def test_product_controller_profiles_keep_happy_path_dispatch_in_kernel() -> None:
    controller_dir = ROOT / "examples" / "prod" / "controller"

    for filename in (
        "issue-fanout-v3.yaml",
        "prd-fanout-v3.yaml",
        "refactor-lane-v3.yaml",
    ):
        config = load_config(controller_dir / filename)
        role_names = {role.name for role in config.roles}

        assert "orchestrator" not in role_names, filename
        assert config.workflow.stages, filename
        assert config.workflow.pipelines, filename
