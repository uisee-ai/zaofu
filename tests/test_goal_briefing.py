"""G4/U21:briefing goal 块与地面真值/自检条款(灰度)。"""
from __future__ import annotations

from types import SimpleNamespace

from zf.core.events.model import ZfEvent
from zf.runtime.goal_briefing import goal_briefing_section


def _cfg(enabled=True):
    return SimpleNamespace(goal=SimpleNamespace(enabled=enabled))


def _events():
    return [ZfEvent(type="run.goal.started", actor="zf-cli",
                    payload={"objective": "deliver avbs runtime", "run_id": "r1"})]


def test_section_rendered_with_objective_and_clauses() -> None:
    lines = goal_briefing_section(_events(), config=_cfg())
    text = "\n".join(lines)
    assert "## Run Goal (persistent)" in text
    assert "deliver avbs runtime" in text
    assert "以 workdir 当前状态与外部实物为权威" in text
    assert "全量自检" in text  # U21 完成自检条款


def test_disabled_or_no_goal_renders_nothing() -> None:
    assert goal_briefing_section(_events(), config=_cfg(enabled=False)) == []
    assert goal_briefing_section([], config=_cfg()) == []
    assert goal_briefing_section(_events(), config=SimpleNamespace()) == []
