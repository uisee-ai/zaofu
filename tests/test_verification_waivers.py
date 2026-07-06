"""avbs-r4 F6: operator waive 事件化持久层(doc 124 STOP waive-trail)。

r4 中同一 waive 裁决被迫口头重发 5+ 次(每次 respawn 蒸发)。waive 走
events.jsonl,briefing 渲染自动带出。
"""

from __future__ import annotations

from pathlib import Path

from zf.core.events.model import ZfEvent
from zf.runtime.waivers import (
    REVOKE_EVENT,
    WAIVE_EVENT,
    active_waivers,
    render_waiver_lines,
)


def _waive(signature: str, task_ids: list[str], reason: str = "env defect") -> ZfEvent:
    return ZfEvent(type=WAIVE_EVENT, actor="operator", payload={
        "task_ids": task_ids, "signature": signature, "reason": reason,
    })


def test_waive_applies_to_scoped_task() -> None:
    events = [_waive("npx playwright test tests/e2e/scene", ["AVBS-SCENE-001"])]
    assert len(active_waivers(events, "AVBS-SCENE-001")) == 1
    assert active_waivers(events, "AVBS-FLOW-001") == []


def test_wildcard_waive_applies_everywhere() -> None:
    events = [_waive("npm test -- --run tests/scene", ["*"])]
    assert len(active_waivers(events, "ANY-TASK")) == 1


def test_revoke_clears_waiver() -> None:
    events = [
        _waive("cmd-a", ["T-1"]),
        ZfEvent(type=REVOKE_EVENT, actor="operator", payload={
            "task_ids": ["T-1"], "signature": "cmd-a",
        }),
    ]
    assert active_waivers(events, "T-1") == []


def test_latest_waive_wins_and_renders() -> None:
    events = [
        _waive("cmd-a", ["T-1"], reason="old"),
        _waive("cmd-a", ["T-1"], reason="chromium 缺 libnspr4.so"),
    ]
    waivers = active_waivers(events, "T-1")
    assert len(waivers) == 1
    assert waivers[0]["reason"] == "chromium 缺 libnspr4.so"
    lines = render_waiver_lines(waivers)
    assert any("cmd-a" in line for line in lines)
    assert any("Operator waivers" in line for line in lines)


def test_briefing_includes_waiver_lines(tmp_path: Path) -> None:
    from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
    from zf.core.events.log import EventLog
    from zf.core.task.schema import Task, TaskContract
    from zf.runtime.injection import generate_task_briefing

    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    log = EventLog(state_dir / "events.jsonl")
    log.append(_waive("npx playwright test tests/e2e/scene", ["AVBS-SCENE-001"]))

    config = ZfConfig(
        project=ProjectConfig(name="t", state_dir=str(state_dir)),
        roles=[RoleConfig(name="dev", instance_id="dev-1", backend="mock")],
    )
    task = Task(
        id="AVBS-SCENE-001", title="scene", status="in_progress",
        contract=TaskContract(
            feature_id="F-1",
            behavior="implement scene",
            verification="npm run build",
        ),
    )
    briefing = generate_task_briefing(
        config, config.roles[0], task, state_dir_ref=state_dir,
    )
    assert "Operator waivers" in briefing
    assert "npx playwright test tests/e2e/scene" in briefing
