"""Tests for config schema dataclasses."""

from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config, validate_config
from zf.core.config.schema import (
    ZfConfig,
    ProjectConfig,
    SessionConfig,
    OrchestratorConfig,
    RoleConfig,
    ConstraintsConfig,
    QualityGateConfig,
    AutopilotConfig,
    AutopilotScheduleConfig,
    IntegrationsConfig,
    OpenClawFeishuBridgeConfig,
)


def test_project_config_defaults():
    p = ProjectConfig(name="test")
    assert p.name == "test"
    assert p.workspace == "."
    assert p.state_dir == ".zf"


def test_session_config_defaults():
    s = SessionConfig()
    assert s.tmux_session == "zf"


def test_orchestrator_config_defaults():
    o = OrchestratorConfig()
    assert o.backend == "python"
    # P-Y1: empty model means "use backend CLI default"
    assert o.model == ""


def test_role_config_minimal():
    r = RoleConfig(name="dev", backend="python")
    assert r.name == "dev"
    assert r.model == ""
    assert r.stages == []


def test_role_config_with_constraints():
    r = RoleConfig(
        name="dev",
        backend="python",
        constraints=ConstraintsConfig(
            allowed_paths=["src"],
            blocked_paths=[".zf"],
        ),
    )
    assert r.constraints.allowed_paths == ["src"]
    assert r.constraints.blocked_paths == [".zf"]


def test_zf_config_defaults():
    cfg = ZfConfig(project=ProjectConfig(name="test"))
    assert cfg.version == "1.0"
    assert cfg.project.name == "test"
    assert cfg.roles == []
    assert cfg.session.tmux_session == "zf"


def test_zf_config_has_preset():
    cfg = ZfConfig(project=ProjectConfig(name="test"), preset="safe-local")
    assert cfg.preset == "safe-local"


def test_zf_config_preset_default_empty():
    cfg = ZfConfig()
    assert cfg.preset == ""


def test_zf_config_has_stage_labels():
    cfg = ZfConfig(stage_labels={"intake": "① Intake"})
    assert cfg.stage_labels == {"intake": "① Intake"}


def test_zf_config_stage_labels_default_empty():
    cfg = ZfConfig()
    assert cfg.stage_labels == {}


def test_quality_gate_config_defaults():
    g = QualityGateConfig()
    assert g.enabled is True
    assert g.required_checks == []


def test_quality_gate_config_with_checks():
    g = QualityGateConfig(enabled=True, required_checks=["artifacts_present"])
    assert g.required_checks == ["artifacts_present"]


def test_zf_config_has_quality_gates():
    cfg = ZfConfig(quality_gates={
        "static": QualityGateConfig(enabled=True, required_checks=["artifacts_present"]),
    })
    assert "static" in cfg.quality_gates
    assert cfg.quality_gates["static"].enabled is True


def test_zf_config_quality_gates_default_empty():
    cfg = ZfConfig()
    assert cfg.quality_gates == {}


def test_role_config_has_triggers_and_publishes():
    r = RoleConfig(
        name="dev",
        triggers=["task.assigned"],
        publishes=["dev.build.done"],
    )
    assert r.triggers == ["task.assigned"]
    assert r.publishes == ["dev.build.done"]


def test_role_config_triggers_default_empty():
    r = RoleConfig(name="dev")
    assert r.triggers == []
    assert r.publishes == []


def test_autopilot_config_defaults():
    cfg = AutopilotConfig()
    assert cfg.enabled is False
    assert cfg.mode == "proposal_only"
    assert cfg.schedules == []


def test_zf_config_has_autopilot():
    cfg = ZfConfig(autopilot=AutopilotConfig(
        enabled=True,
        schedules=[AutopilotScheduleConfig(id="daily-triage")],
    ))
    assert cfg.autopilot.enabled is True
    assert cfg.autopilot.schedules[0].action == "triage"


def test_zf_config_has_integrations_default():
    cfg = ZfConfig()
    assert isinstance(cfg.integrations, IntegrationsConfig)
    assert isinstance(
        cfg.integrations.openclaw_feishu_bridge,
        OpenClawFeishuBridgeConfig,
    )
    assert cfg.integrations.openclaw_feishu_bridge.enabled is False


def test_full_codex_autoresearch_template_uses_single_state_tree():
    template = (
        Path(__file__).parent.parent
        / "examples"
        / "zf-full-codex-autoresearch.yaml"
    )

    errors = validate_config(template)
    assert errors == [], f"unexpected validate errors: {errors}"

    cfg = load_config(template)

    assert cfg.project.state_dir == ".zf"
    assert cfg.runtime.workdirs.root == ".zf/workdirs"
    assert cfg.runtime.git.remote_policy == "local"
    assert cfg.runtime.skills.pool == ".zf/skills"
    assert cfg.runtime.skills.lock_file == ".zf/skills.lock.json"
    assert ".zf-full-codex/" not in template.read_text(encoding="utf-8")
