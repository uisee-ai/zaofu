"""Wire-up tests: orchestrator._apply_housekeeping calls the memory
auto-promoter and appends the resulting memory.note via event_writer.

Per CLAUDE.md Wire-Up Discipline: this guarantees
housekeeping.promote_to_memory_note_event is not a library without
callers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.config.schema import ProjectConfig, RoleConfig, SessionConfig, ZfConfig
from zf.core.events.model import ZfEvent
from zf.core.events.log import EventLog
from zf.core.memory.store import MemoryStore
from zf.core.state.session import SessionStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.tmux import TmuxSession
from zf.runtime.transport import TmuxTransport


def test_grep_promoter_callsite_in_orchestrator():
    """The promoter must be imported and called from orchestrator.py.
    A 'library-without-callers' anti-pattern check (CLAUDE.md)."""
    src = Path(__file__).resolve().parents[1] / "src/zf/runtime/orchestrator.py"
    text = src.read_text(encoding="utf-8")
    assert "promote_to_memory_note_event" in text, (
        "orchestrator.py must import promote_to_memory_note_event"
    )
    assert text.count("promote_to_memory_note_event") >= 2, (
        "expected at least an import + a call site"
    )


def _make_orchestrator(tmp_path: Path) -> Orchestrator:
    """Minimal orchestrator wired to a real EventLog + MemoryStore so we
    can assert side effects on _apply_housekeeping."""
    state_dir = tmp_path / ".zf"
    state_dir.mkdir()
    (state_dir / "memory").mkdir()
    EventLog(state_dir / "events.jsonl").append(
        ZfEvent(type="loop.started", actor="zf-cli")
    )
    SessionStore(state_dir / "session.yaml").create(project_root=str(tmp_path))
    (state_dir / "kanban.json").write_text("[]\n")

    config = ZfConfig(
        project=ProjectConfig(name="t"),
        session=SessionConfig(tmux_session="t"),
        roles=[RoleConfig(name="dev", backend="mock")],
    )
    transport = TmuxTransport(TmuxSession(session_name="t", dry_run=True))
    return Orchestrator(state_dir, config, transport)


def test_candidate_conflict_promotes_via_apply_housekeeping(tmp_path: Path):
    orch = _make_orchestrator(tmp_path)
    state_dir = tmp_path / ".zf"

    trigger = ZfEvent(
        type="candidate.conflict",
        actor="zf-cli",
        task_id="TASK-X",
        payload={
            "branch": "candidate/F-test",
            "conflict_files": ["packages/core/package.json"],
            "failed_task_id": "TASK-X",
            "base_commit": "deadbeefcafe1234",
        },
    )
    orch.event_writer.append(trigger)
    orch._apply_housekeeping(trigger)

    # MemoryStore got the promoted note (shared memory, actor=None).
    store = MemoryStore(state_dir / "memory")
    shared = store.get(None)
    assert len(shared) == 1
    assert shared[0].type == "context"
    assert "packages/core/package.json" in shared[0].content
    assert "TASK-X" in shared[0].content

    # events.jsonl now has the trigger AND the memory.note.
    log = EventLog(state_dir / "events.jsonl")
    events = log.read_all()
    note_events = [e for e in events if e.type == "memory.note"]
    assert len(note_events) == 1
    note = note_events[0]
    assert note.causation_id == trigger.id
    assert note.payload["source"] == "auto_promote"
    assert note.payload["trigger_event_id"] == trigger.id


def test_dev_blocked_promotes_via_apply_housekeeping(tmp_path: Path):
    orch = _make_orchestrator(tmp_path)
    state_dir = tmp_path / ".zf"

    trigger = ZfEvent(
        type="dev.blocked",
        actor="dev-1",
        task_id="TASK-Y",
        payload={"reason": "pnpm install offline mirror missing"},
    )
    orch.event_writer.append(trigger)
    orch._apply_housekeeping(trigger)

    store = MemoryStore(state_dir / "memory")
    shared = store.get(None)
    assert len(shared) == 1
    assert shared[0].type == "fix"
    assert "TASK-Y" in shared[0].content
    assert "pnpm install offline mirror missing" in shared[0].content


def test_same_trigger_promoted_only_once(tmp_path: Path):
    """Dedupe: replaying the same event id in the same process does not
    create a second memory.note."""
    orch = _make_orchestrator(tmp_path)
    state_dir = tmp_path / ".zf"

    trigger = ZfEvent(
        type="candidate.conflict",
        task_id="TASK-Z",
        payload={"conflict_files": ["a"]},
    )
    orch.event_writer.append(trigger)
    orch._apply_housekeeping(trigger)
    orch._apply_housekeeping(trigger)
    orch._apply_housekeeping(trigger)

    log = EventLog(state_dir / "events.jsonl")
    note_events = [e for e in log.read_all() if e.type == "memory.note"]
    assert len(note_events) == 1, (
        f"expected 1 memory.note after 3 replays, got {len(note_events)}"
    )


def test_non_promotable_event_does_not_emit_memory_note(tmp_path: Path):
    orch = _make_orchestrator(tmp_path)
    state_dir = tmp_path / ".zf"

    trigger = ZfEvent(
        type="dev.build.done",
        actor="dev-1",
        task_id="TASK-Q",
        payload={
            "state": "DONE",
            "summary": "ok",
            "artifact_refs": [],
            "evidence_refs": [],
            "dispatch_id": "disp-xyz",
        },
    )
    orch.event_writer.append(trigger)
    orch._apply_housekeeping(trigger)

    log = EventLog(state_dir / "events.jsonl")
    note_events = [e for e in log.read_all() if e.type == "memory.note"]
    assert note_events == []


def test_memory_note_event_itself_does_not_loop(tmp_path: Path):
    """The promoter must not promote memory.note → memory.note (infinite
    loop guard). _apply_housekeeping receiving memory.note should write to
    store but not re-promote."""
    orch = _make_orchestrator(tmp_path)
    state_dir = tmp_path / ".zf"

    note = ZfEvent(
        type="memory.note",
        actor=None,
        payload={"mem_type": "decision", "content": "worker-emitted"},
    )
    orch.event_writer.append(note)
    orch._apply_housekeeping(note)

    log = EventLog(state_dir / "events.jsonl")
    note_events = [e for e in log.read_all() if e.type == "memory.note"]
    assert len(note_events) == 1  # only the original, no promoted dup
