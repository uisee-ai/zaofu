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
    assert "按 briefing 的 stage scope" in text
    assert "全量自检" not in text


def test_role_specific_goal_clauses_do_not_cross_stage_boundaries() -> None:
    judge = "\n".join(goal_briefing_section(
        _events(), config=_cfg(), role="judge-1", stage="thin-judge",
        output_profile="thin-judge-goal-closure",
    ))
    assert "不运行测试" in judge
    assert "不 commit" in judge
    assert "必要的邻接回归" not in judge

    verify = "\n".join(goal_briefing_section(
        _events(), config=_cfg(), role="verify-1", stage="candidate-verify",
        output_profile="candidate-verify",
    ))
    assert "先复用" in verify
    assert "独立风险 probe" in verify

    impl = "\n".join(goal_briefing_section(
        _events(), config=_cfg(), role="dev-1", stage="impl",
        output_profile="implementation",
    ))
    assert "必要的邻接回归" in impl
    assert "不要自行扩大为全量回归" in impl


def test_disabled_or_no_goal_renders_nothing() -> None:
    assert goal_briefing_section(_events(), config=_cfg(enabled=False)) == []
    assert goal_briefing_section([], config=_cfg()) == []
    assert goal_briefing_section(_events(), config=SimpleNamespace()) == []
