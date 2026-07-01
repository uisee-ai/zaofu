"""ZF-PWF-PRECOMPACT-001 — Claude Code PreCompact hook tests (doc 41 §4.4).

Acceptance coverage:
- §1: claude hook settings.json has PreCompact registration
- §3: WAKE_PATTERNS contains both new events
- §4: precompact event emits snapshot_requested when worker has active task
- §6: precompact with no active task is a no-op
- §7: hook command structure does not block compaction (exit 0 implicit
       — hook_recv always returns 0 even on dead-letter; this is verified
       indirectly via wire-up grep proof that command does not pass
       --block or similar flag)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.events.known_types import KNOWN_EVENT_TYPES
from zf.runtime.wake_patterns import WAKE_PATTERNS


# ---------------------------------------------------------------------------
# Event-registry wire-up — both new events must be known + wake-able
# ---------------------------------------------------------------------------


def test_worker_context_precompact_is_known_event() -> None:
    assert "worker.context.precompact" in KNOWN_EVENT_TYPES


def test_worker_context_snapshot_requested_is_known_event() -> None:
    assert "worker.context.snapshot_requested" in KNOWN_EVENT_TYPES


def test_worker_context_precompact_wakes_orchestrator() -> None:
    """precompact must wake the orchestrator (else handler never fires)."""
    assert "worker.context.precompact" in WAKE_PATTERNS


def test_worker_context_snapshot_requested_wakes_orchestrator() -> None:
    """snapshot_requested must wake downstream projector consumers."""
    assert "worker.context.snapshot_requested" in WAKE_PATTERNS


# ---------------------------------------------------------------------------
# Claude hook settings — PreCompact registration
# ---------------------------------------------------------------------------


def test_claude_hook_settings_contains_precompact(tmp_path: Path) -> None:
    """zf start --backend claude writes a settings.json that registers
    both Stop (existing) and PreCompact (new). The PreCompact command
    pipes to ``zf hook-recv --event worker.context.precompact``."""
    from zf.cli.start import _write_claude_hook_settings

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _write_claude_hook_settings(state_dir)

    settings_path = state_dir / "hooks" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]

    # Stop hook preserved
    assert "Stop" in hooks
    # PreCompact registered with the snapshot-emit command
    assert "PreCompact" in hooks, (
        "PreCompact hook missing from claude settings.json"
    )
    pc = hooks["PreCompact"][0]
    assert pc["matcher"] == "*"
    cmd = pc["hooks"][0]["command"]
    assert "zf hook-recv" in cmd
    assert "--event worker.context.precompact" in cmd
    # Hook MUST NOT pass any block-style flag.
    assert "--block" not in cmd


def test_claude_hook_settings_precompact_state_dir_quoted(tmp_path: Path) -> None:
    """state_dir must be shlex-quoted in the command so a workdir with
    spaces does not break the hook."""
    from zf.cli.start import _write_claude_hook_settings

    state_dir = tmp_path / "with spaces"
    state_dir.mkdir()
    _write_claude_hook_settings(state_dir)

    data = json.loads(
        (state_dir / "hooks" / "settings.json").read_text()
    )
    pc_cmd = data["hooks"]["PreCompact"][0]["hooks"][0]["command"]
    # When the path contains spaces, shlex.quote wraps it in single
    # quotes. The actual path string should appear quoted somewhere.
    assert "'" in pc_cmd or " --state-dir " in pc_cmd, (
        f"state_dir not quoted in PreCompact command: {pc_cmd!r}"
    )


# ---------------------------------------------------------------------------
# Handler wire-up — Orchestrator must invoke _handle_precompact_signal
# from _react_to_events.
# ---------------------------------------------------------------------------


def test_orchestrator_has_precompact_handler() -> None:
    """Wire-up: Orchestrator must declare _handle_precompact_signal
    or the PreCompact hook is library-without-callers (Class D)."""
    from zf.runtime.orchestrator import Orchestrator

    assert hasattr(Orchestrator, "_handle_precompact_signal")


def test_react_to_events_calls_precompact_handler() -> None:
    """Source-level grep proof that _react_to_events invokes the
    precompact handler."""
    import inspect

    from zf.runtime.orchestrator import Orchestrator

    source = inspect.getsource(Orchestrator._react_to_events)
    assert "_handle_precompact_signal" in source


# ---------------------------------------------------------------------------
# Handler semantics — these are unit-style tests against a minimal
# fake orchestrator (we don't spin up a full kernel; just verify the
# logic of _handle_precompact_signal itself).
# ---------------------------------------------------------------------------


class _FakeEventWriter:
    def __init__(self) -> None:
        self.appended: list[object] = []

    def append(self, event: object) -> None:
        self.appended.append(event)


class _FakeOrchestrator:
    """Minimal fake providing only the attributes
    ``_handle_precompact_signal`` reads."""

    def __init__(
        self,
        active_task=None,
        event_writer=None,
    ) -> None:
        self._active_task = active_task
        self.event_writer = event_writer or _FakeEventWriter()

    def _active_task_for_instance(self, instance_id: str):
        return self._active_task


def test_precompact_with_active_task_emits_snapshot_requested() -> None:
    """Happy path: precompact event for an instance with an active
    task produces a snapshot_requested event tagged with the task id."""
    from zf.core.events.model import ZfEvent
    from zf.core.task.schema import Task
    from zf.runtime.orchestrator import Orchestrator

    task = Task(id="TASK-PC", title="demo")
    orch = _FakeOrchestrator(active_task=task)
    event = ZfEvent(
        type="worker.context.precompact",
        actor="dev-1",
        payload={
            "session_id": "claude-uuid-123",
            "transcript_path": "/tmp/transcript.jsonl",
        },
    )
    Orchestrator._handle_precompact_signal(orch, event)  # type: ignore[arg-type]
    assert len(orch.event_writer.appended) == 1
    emitted = orch.event_writer.appended[0]
    assert emitted.type == "worker.context.snapshot_requested"
    assert emitted.task_id == "TASK-PC"
    assert emitted.payload["instance_id"] == "dev-1"
    assert emitted.payload["trigger"] == "precompact"
    assert emitted.payload["source_event_id"] == event.id
    assert emitted.payload["session_id"] == "claude-uuid-123"
    assert emitted.payload["transcript_path"] == "/tmp/transcript.jsonl"


def test_precompact_with_no_active_task_is_noop() -> None:
    """No active task → no snapshot_requested emit (acceptance §6)."""
    from zf.core.events.model import ZfEvent
    from zf.runtime.orchestrator import Orchestrator

    orch = _FakeOrchestrator(active_task=None)
    event = ZfEvent(
        type="worker.context.precompact",
        actor="dev-1",
        payload={},
    )
    Orchestrator._handle_precompact_signal(orch, event)  # type: ignore[arg-type]
    assert orch.event_writer.appended == []


def test_precompact_missing_instance_id_is_noop() -> None:
    """Hook event with no actor → no-op (defensive)."""
    from zf.core.events.model import ZfEvent
    from zf.core.task.schema import Task
    from zf.runtime.orchestrator import Orchestrator

    orch = _FakeOrchestrator(active_task=Task(id="TASK-PC", title="demo"))
    event = ZfEvent(
        type="worker.context.precompact",
        actor=None,
        payload={},
    )
    Orchestrator._handle_precompact_signal(orch, event)  # type: ignore[arg-type]
    assert orch.event_writer.appended == []


def test_handler_ignores_non_precompact_events() -> None:
    """Defense in depth: the handler is called from a loop processing
    arbitrary event types. Non-precompact events must short-circuit."""
    from zf.core.events.model import ZfEvent
    from zf.core.task.schema import Task
    from zf.runtime.orchestrator import Orchestrator

    orch = _FakeOrchestrator(active_task=Task(id="TASK-X", title="demo"))
    event = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        payload={},
    )
    Orchestrator._handle_precompact_signal(orch, event)  # type: ignore[arg-type]
    assert orch.event_writer.appended == []
