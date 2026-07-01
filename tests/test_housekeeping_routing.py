"""Tests for housekeeping event routing (G-MEM-2 / G-EVT-3).

Verifies that events required by housekeeping handlers (memory.note,
agent.usage, task.contract.update, agent.tool.use, agent.tool.result)
are in the wake_patterns list so EventWatcher triggers run_once for
them, which in turn runs _apply_housekeeping.

Layer 2 (orchestrator agent) is still gated by its own triggers
filter in _notify_orchestrator_agent — these housekeeping-only events
don't wake Layer 2.
"""

from __future__ import annotations

import importlib
import inspect

import pytest


class TestHousekeepingWakePatterns:
    def _wake_patterns(self) -> list[str]:
        """Read wake patterns from the module-level constant.

        Historically this parsed start.py source; the constant was
        promoted to zf.runtime.wake_patterns during the 2026-04-20
        topology wiring refactor.
        """
        from zf.runtime.wake_patterns import WAKE_PATTERNS
        return list(WAKE_PATTERNS)

    def test_memory_note_batch_processed_not_wake(self):
        # K2:memory.note 在 run_once 内批处理,不自唤醒(level-triggered)。
        from zf.runtime.wake_patterns import (
            BATCH_PROCESSED_EVENTS,
            WAKE_PATTERNS,
        )
        assert "memory.note" in BATCH_PROCESSED_EVENTS
        assert "memory.note" not in WAKE_PATTERNS

    def test_agent_usage_batch_processed_not_wake(self):
        # K2:agent.usage 喂 CostTracker,延到下次唤醒聚合无正确性影响
        # (cangjie 实测此类高频事件曾是空唤醒大头)。
        from zf.runtime.wake_patterns import (
            BATCH_PROCESSED_EVENTS,
            WAKE_PATTERNS,
        )
        assert "agent.usage" in BATCH_PROCESSED_EVENTS
        assert "agent.usage" not in WAKE_PATTERNS

    def test_task_contract_update_in_wake_patterns(self):
        assert "task.contract.update" in self._wake_patterns()

    def test_agent_tool_use_NOT_in_wake_patterns(self):
        # Phase 2.5 Bug 2: tailer emits many tool.use events per turn.
        # No housekeeping reacts to them, so waking run_once per event
        # is pure overhead and can trip drift/stuck detectors.
        assert "agent.tool.use" not in self._wake_patterns()

    def test_agent_tool_result_NOT_in_wake_patterns(self):
        # Same reasoning as agent.tool.use — tailer telemetry, not a
        # control signal.
        assert "agent.tool.result" not in self._wake_patterns()

    def test_user_message_still_there(self):
        """Don't accidentally remove existing Layer 2 triggers."""
        assert "user.message" in self._wake_patterns()
        assert "dev.build.done" in self._wake_patterns()
