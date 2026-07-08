"""GoalConfig(133/G 批)YAML 往返加载(B2 教训:必须过真实 loader)。"""
from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.loader import ConfigError, load_config

_BASE = """
version: "1.0"
project:
  name: goal-test
  state_dir: .zf
roles:
  - {name: dev, backend: mock, role_kind: writer}
"""


def _load(tmp_path: Path, extra: str):
    cfg_path = tmp_path / "zf.yaml"
    cfg_path.write_text(_BASE + extra, encoding="utf-8")
    return load_config(cfg_path)


def test_goal_section_round_trip(tmp_path: Path) -> None:
    cfg = _load(tmp_path, """
goal:
  enabled: true
  max_rescans: 7
  idle_progress_ticks: 5
  rework_fingerprint: true
  quiescent_after_escalate: false
  micro_loop: true
""")
    assert cfg.goal.enabled is True
    assert cfg.goal.max_rescans == 7
    assert cfg.goal.idle_progress_ticks == 5
    assert cfg.goal.rework_fingerprint is True
    assert cfg.goal.quiescent_after_escalate is False
    assert cfg.goal.micro_loop is True


def test_goal_defaults_all_off(tmp_path: Path) -> None:
    cfg = _load(tmp_path, "")
    assert cfg.goal.enabled is False
    assert cfg.goal.rework_fingerprint is False
    assert cfg.goal.max_rescans == 5
    assert cfg.goal.micro_loop is False


def test_goal_unknown_key_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        _load(tmp_path, "goal:\n  bogus_key: 1\n")


def test_budget_fail_closed_top_key_accepted(tmp_path: Path) -> None:
    # P0-8 存量白名单遗漏回归钉(与 attempt_lease_grace_s 同族)
    cfg = _load(tmp_path, "budget_fail_closed: true\n")
    assert cfg.budget_fail_closed is True
