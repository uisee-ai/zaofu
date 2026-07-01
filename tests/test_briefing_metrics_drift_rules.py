"""LH-1.T3/T4/T5: briefing injects three new sections.

  - Current Health (metrics summary with ⚠ on threshold breaches)
  - Drift Warnings (from worker.drift.detected in last hour)
  - Learned Constraints (from .zf/promoted_rules.jsonl)

Zero-noise principle: if no drift / no rules / all healthy, the
respective section is omitted (or says "none").
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from zf.core.config.schema import (
    ProjectConfig, RoleConfig, SessionConfig, ZfConfig,
)
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task, TaskEvidence
from zf.core.task.store import TaskStore
from zf.core.state.session import SessionStore
from zf.runtime.orchestrator_briefing import build_orchestrator_briefing


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    sd = tmp_path / ".zf"
    sd.mkdir()
    (sd / "memory").mkdir()
    EventLog(sd / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(sd / "session.yaml").create(project_root=str(tmp_path))
    (sd / "kanban.json").write_text("[]\n")
    return sd


@pytest.fixture
def config():
    return ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[
            RoleConfig(name="orchestrator", backend="mock"),
            RoleConfig(name="dev", backend="mock"),
        ],
    )


def _trigger():
    return ZfEvent(type="user.message", actor=None, payload={"text": "hi"})


class TestCurrentHealthSection:
    def test_briefing_has_current_health_section(self, state_dir, config):
        text = build_orchestrator_briefing(
            state_dir=state_dir, config=config, trigger_event=_trigger(),
        )
        assert "Current Health" in text

    def test_alerts_surface_with_warning_marker(self, state_dir, config):
        # Emit a cost.budget.exceeded → BudgetBreachRate > 0.05
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(type="task.dispatched", actor="zf-cli", task_id="T1"))
        log.append(ZfEvent(type="cost.budget.exceeded", actor="zf-cli",
                            payload={"scope": "global"}))
        text = build_orchestrator_briefing(
            state_dir=state_dir, config=config, trigger_event=_trigger(),
        )
        assert "⚠" in text or "Alert" in text

    def test_no_alerts_when_healthy(self, state_dir, config):
        text = build_orchestrator_briefing(
            state_dir=state_dir, config=config, trigger_event=_trigger(),
        )
        # A fresh project may still emit "no alerts" placeholder — either
        # absence of ⚠ or explicit "no alerts" is acceptable.
        assert "⚠" not in text or "no alerts" in text.lower()


class TestDriftSection:
    def test_drift_detected_appears_in_briefing(self, state_dir, config):
        log = EventLog(state_dir / "events.jsonl")
        log.append(ZfEvent(
            type="worker.drift.detected", actor="zf-cli",
            payload={"signal": "thrashing", "severity": "high",
                     "detail": "T1 rejected 3 times"},
        ))
        text = build_orchestrator_briefing(
            state_dir=state_dir, config=config, trigger_event=_trigger(),
        )
        assert "Drift" in text
        assert "thrashing" in text

    def test_no_drift_keeps_section_quiet(self, state_dir, config):
        text = build_orchestrator_briefing(
            state_dir=state_dir, config=config, trigger_event=_trigger(),
        )
        # Either section absent, or present with "none" placeholder.
        assert "thrashing" not in text


class TestPromotedRulesSection:
    def test_active_rules_render_in_briefing(self, state_dir, config):
        rules_path = state_dir / "promoted_rules.jsonl"
        rules_path.write_text(json.dumps({
            "category": "lint",
            "rule": "ruff check src",
            "fix_hint": "ruff check --fix src",
            "promoted_at": str(time.time()),
            "occurrences": 3,
        }) + "\n")
        text = build_orchestrator_briefing(
            state_dir=state_dir, config=config, trigger_event=_trigger(),
        )
        assert "Learned Constraints" in text
        assert "ruff check src" in text

    def test_stale_rule_omitted(self, state_dir, config):
        rules_path = state_dir / "promoted_rules.jsonl"
        # 10 days ago
        stale_ts = str(time.time() - 10 * 24 * 3600)
        rules_path.write_text(json.dumps({
            "category": "lint",
            "rule": "ruff check OLD",
            "fix_hint": "",
            "promoted_at": stale_ts,
            "occurrences": 1,
        }) + "\n")
        text = build_orchestrator_briefing(
            state_dir=state_dir, config=config, trigger_event=_trigger(),
        )
        assert "ruff check OLD" not in text

    def test_no_rules_file_is_fine(self, state_dir, config):
        # No promoted_rules.jsonl at all — briefing must still build.
        text = build_orchestrator_briefing(
            state_dir=state_dir, config=config, trigger_event=_trigger(),
        )
        assert text.startswith("# Orchestrator wake")


class TestWireUp:
    def test_collector_imported_from_briefing(self):
        src = (Path(__file__).resolve().parents[1]
               / "src/zf/runtime/orchestrator_briefing.py").read_text()
        assert "MetricsCollector" in src

    def test_collector_imported_from_cli(self):
        src = (Path(__file__).resolve().parents[1]
               / "src/zf/cli/metrics.py").read_text()
        assert "MetricsCollector" in src
