"""FIX-13(bizsim r4 F13):预算 sweep 主动越限可见 + explain 预算区块。

r4 实锚:$1150 上限被在途 turns 静默穿透至 $1166,cost.budget.exceeded
迟至下一次派发才发;run_status_explain 无预算区块,operator 盲飞。
"""
from __future__ import annotations

from pathlib import Path

from zf.core.config.schema import ProjectConfig, RoleConfig, ZfConfig
from zf.core.events.log import EventLog
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.run_manager import (
    _budget_status_block,
    build_run_status_explain_projection,
)


class _Transport:
    def send_task(self, role_name, briefing_path, prompt, *, context=None):  # noqa: ANN001
        pass

    def is_alive(self, role_name):  # noqa: ANN001
        return True

    def capture_log(self, role_name, lines=200):  # noqa: ANN001
        return ""

    def poll_events(self):
        return []


def _orch(tmp_path: Path) -> tuple[Orchestrator, EventLog]:
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "kanban.json").write_text("[]\n", encoding="utf-8")
    config = ZfConfig(
        project=ProjectConfig(name="test"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )
    config.global_budget_usd = 10.0
    config.budget_enforcement_enabled = True
    log = EventLog(state_dir / "events.jsonl")
    orch = Orchestrator(state_dir, config, _Transport())  # type: ignore[arg-type]
    return orch, log


def test_budget_sweep_emits_exceeded_without_dispatch(tmp_path, monkeypatch):
    orch, log = _orch(tmp_path)
    monkeypatch.setattr(orch.cost_tracker, "total_usd", lambda **kw: 12.5)

    orch.run_once(events=[])  # 无任何派发诉求,纯 sweep

    exceeded = [
        e for e in log.read_all() if e.type == "cost.budget.exceeded"
    ]
    assert exceeded, "spent>=cap 必须由 sweep 主动发,不等派发"
    assert exceeded[-1].payload.get("scope") == "global_sweep"


def test_budget_sweep_quiet_under_cap(tmp_path, monkeypatch):
    orch, log = _orch(tmp_path)
    monkeypatch.setattr(orch.cost_tracker, "total_usd", lambda **kw: 3.0)

    orch.run_once(events=[])

    assert not [
        e for e in log.read_all() if e.type == "cost.budget.exceeded"
    ]


def test_budget_status_block_and_projection(tmp_path):
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    config = ZfConfig(project=ProjectConfig(name="test"))
    config.global_budget_usd = 100.0
    config.budget_enforcement_enabled = False

    block = _budget_status_block(state_dir, config=config)
    assert block["global_budget_usd"] == 100.0
    assert block["enforcement_enabled"] is False
    assert "exceeded" in block

    projection = build_run_status_explain_projection(
        state_dir,
        events=[],
        budget=block,
        goal={},
        completion_profile={},
        monitor={},
        pending_actions=[],
        no_progress={},
        repair_merge_queue={},
        wait_hints={},
    )
    assert projection["budget"]["global_budget_usd"] == 100.0
