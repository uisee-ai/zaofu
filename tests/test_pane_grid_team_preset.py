"""1206 Phase C-T3 — lock `examples/pane-grid-team.yaml` shape."""

from __future__ import annotations

from pathlib import Path

from zf.core.config.loader import load_config


PANE_GRID_YAML = (
    Path(__file__).resolve().parents[1] / "examples" / "pane-grid-team.yaml"
)


def test_pane_grid_yaml_loads():
    config = load_config(PANE_GRID_YAML)
    assert config.preset == "pane-grid-team"
    assert config.session.tmux_session == "zf-pane-grid"


def test_pane_grid_yaml_uses_pane_grid_layout():
    config = load_config(PANE_GRID_YAML)
    assert config.session.tmux_layout == "pane_grid"


def test_pane_grid_yaml_topology_is_clean():
    from zf.core.workflow.topology import WorkflowTopology
    config = load_config(PANE_GRID_YAML)
    topology = WorkflowTopology.from_config(config)
    assert topology.orphan_events() == []
    assert topology.dead_end_roles() == []


def test_pane_grid_yaml_has_expected_6_roles():
    config = load_config(PANE_GRID_YAML)
    names = {r.name for r in config.roles}
    assert names == {"orchestrator", "arch", "dev", "review", "test", "judge"}
