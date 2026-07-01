"""ZF-LH-SP-001 — State Packet schema + projector tests (doc 39 §4.1).

Covers acceptance §1 (schema round-trip), §2 (projector behaviour),
§5 (atomic write) and §7 (I51 invariant / schema-version lock).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zf.core.state.state_packet import (
    SCHEMA_VERSION,
    StatePacket,
    StatePacketContract,
    StatePacketEvidence,
    StatePacketOwner,
    StatePacketRefs,
    packet_from_dict,
    packet_from_json,
    packet_to_dict,
    packet_to_json,
)
from zf.core.task.schema import Task, TaskContract
from zf.runtime.state_packet_projector import (
    StatePacketProjector,
    read_state_packet,
)


# ---------------------------------------------------------------------------
# Schema invariants
# ---------------------------------------------------------------------------


def test_schema_version_is_locked_at_1_0() -> None:
    """Doc 39 §4.1 + sprint §10: schema_version frozen at 1.0 for the
    1-week freeze window. Any field change after this date requires
    bumping the major version."""
    assert SCHEMA_VERSION == "1.0"


def test_default_packet_has_safe_defaults() -> None:
    packet = StatePacket()
    assert packet.schema_version == "1.0"
    assert packet.task_id == ""
    assert packet.current_stage == ""
    assert packet.owner == StatePacketOwner()
    assert packet.contract == StatePacketContract()
    assert packet.refs.base_ref == "main"
    assert packet.completed == ()
    assert packet.evidence == ()


def test_state_packet_is_frozen() -> None:
    packet = StatePacket()
    with pytest.raises((AttributeError, TypeError)):
        packet.task_id = "TASK-X"  # type: ignore[misc]


def test_nested_dataclasses_are_frozen() -> None:
    owner = StatePacketOwner(role="dev", instance_id="dev-1")
    with pytest.raises((AttributeError, TypeError)):
        owner.role = "review"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


def test_packet_to_dict_includes_all_top_level_fields() -> None:
    packet = StatePacket(task_id="TASK-X", current_stage="implement")
    data = packet_to_dict(packet)
    required = {
        "schema_version", "run_id", "feature_id", "task_id",
        "objective", "current_stage", "owner", "contract", "refs",
        "completed", "decisions", "evidence", "risks", "blocked_by",
        "next_owner", "next_event", "generated_at", "generated_by",
    }
    assert required <= set(data)


def test_round_trip_via_dict_preserves_fields() -> None:
    packet = StatePacket(
        task_id="TASK-RT",
        current_stage="review",
        owner=StatePacketOwner(role="dev", instance_id="dev-1"),
        contract=StatePacketContract(
            behavior="do thing",
            acceptance=("step1", "step2"),
        ),
        evidence=(
            StatePacketEvidence(kind="test", path="/log", status="passed"),
        ),
        next_event="review.approved",
    )
    rt = packet_from_dict(packet_to_dict(packet))
    assert rt == packet


def test_round_trip_via_json_preserves_fields() -> None:
    packet = StatePacket(
        task_id="TASK-JSON",
        objective="ship feature",
        completed=("dev.build.done",),
    )
    rt = packet_from_json(packet_to_json(packet))
    assert rt == packet


def test_packet_from_dict_rejects_incompatible_major_version() -> None:
    bad = packet_to_dict(StatePacket())
    bad["schema_version"] = "2.0"
    with pytest.raises(ValueError, match="incompatible"):
        packet_from_dict(bad)


def test_packet_from_dict_accepts_minor_version_drift() -> None:
    """1.1 / 1.2 / etc. are forward-compat readable by 1.0 readers."""
    data = packet_to_dict(StatePacket())
    data["schema_version"] = "1.5"
    rt = packet_from_dict(data)
    assert rt.schema_version == "1.5"


def test_packet_from_dict_ignores_unknown_fields() -> None:
    """Forward compat — readers must not crash on unknown keys."""
    data = packet_to_dict(StatePacket())
    data["some_future_field"] = "value"
    rt = packet_from_dict(data)
    assert rt.task_id == ""


def test_json_serialization_is_stable_key_order() -> None:
    a = StatePacket(task_id="TASK-A", run_id="run-1")
    b = StatePacket(run_id="run-1", task_id="TASK-A")
    assert packet_to_json(a) == packet_to_json(b)


# ---------------------------------------------------------------------------
# Projector — fakes
# ---------------------------------------------------------------------------


class _FakeTaskStore:
    def __init__(self, tasks: list[Task]):
        self._tasks = tasks

    def list_all(self) -> list[Task]:
        return self._tasks

    def get(self, task_id: str) -> Task | None:
        for t in self._tasks:
            if t.id == task_id:
                return t
        return None


class _FakeEvent:
    def __init__(self, etype: str, eid: str = "", payload: dict | None = None):
        self.type = etype
        self.id = eid or f"evt-{etype}"
        self.payload = payload or {}


class _FakeEventLog:
    def __init__(self, events_by_task: dict[str, list[_FakeEvent]] | None = None):
        self._events = events_by_task or {}

    def query(self, *, task_id: str | None = None, **kw):
        if task_id is None:
            return []
        return list(self._events.get(task_id, []))


def _task(
    *,
    id: str = "TASK-T1",
    status: str = "in_progress",
    assigned: str = "dev-1",
    tiers: list[str] | None = None,
) -> Task:
    return Task(
        id=id,
        title="demo task",
        status=status,
        assigned_to=assigned,
        active_dispatch_id="disp-1",
        contract=TaskContract(
            behavior="implement thing",
            verification_tiers=tiers or ["review", "test"],
            feature_id="F-abc",
        ),
    )


# ---------------------------------------------------------------------------
# Projector — no task case
# ---------------------------------------------------------------------------


def test_project_with_no_stores_returns_no_task_packet(tmp_path: Path) -> None:
    projector = StatePacketProjector(state_dir=tmp_path)
    packet = projector.project()
    assert packet.current_stage == "no_task"
    assert packet.task_id == ""


def test_project_empty_task_store_returns_no_task_packet(tmp_path: Path) -> None:
    projector = StatePacketProjector(
        state_dir=tmp_path,
        task_store=_FakeTaskStore([]),
    )
    packet = projector.project()
    assert packet.current_stage == "no_task"


# ---------------------------------------------------------------------------
# Projector — single active task
# ---------------------------------------------------------------------------


def test_project_active_task_fills_owner_and_refs(tmp_path: Path) -> None:
    task = _task()
    projector = StatePacketProjector(
        state_dir=tmp_path,
        task_store=_FakeTaskStore([task]),
        event_log=_FakeEventLog(),
    )
    packet = projector.project()
    assert packet.task_id == "TASK-T1"
    assert packet.owner.role == "dev"
    assert packet.owner.instance_id == "dev-1"
    assert packet.refs.task_ref == "refs/zaofu/tasks/TASK-T1"
    assert packet.feature_id == "F-abc"
    assert packet.refs.candidate_ref == "candidate/F-abc"


def test_project_acceptance_from_verification_tiers(tmp_path: Path) -> None:
    task = _task(tiers=["review", "test", "judge"])
    projector = StatePacketProjector(
        state_dir=tmp_path,
        task_store=_FakeTaskStore([task]),
        event_log=_FakeEventLog(),
    )
    packet = projector.project()
    assert packet.contract.acceptance == ("review", "test", "judge")


def test_project_explicit_task_id_overrides_auto_pick(tmp_path: Path) -> None:
    t1 = _task(id="TASK-A", status="in_progress")
    t2 = _task(id="TASK-B", status="in_progress")
    projector = StatePacketProjector(
        state_dir=tmp_path,
        task_store=_FakeTaskStore([t1, t2]),
        event_log=_FakeEventLog(),
    )
    packet = projector.project(task_id="TASK-B")
    assert packet.task_id == "TASK-B"


# ---------------------------------------------------------------------------
# Projector — stage / evidence inference from events
# ---------------------------------------------------------------------------


def test_project_infers_stage_from_latest_event(tmp_path: Path) -> None:
    task = _task()
    events = _FakeEventLog({"TASK-T1": [
        _FakeEvent("task.dispatched"),
        _FakeEvent("dev.build.done"),
    ]})
    projector = StatePacketProjector(
        state_dir=tmp_path,
        task_store=_FakeTaskStore([task]),
        event_log=events,
    )
    packet = projector.project()
    assert packet.current_stage == "static_gate"
    assert packet.next_event == "static_gate.passed"


def test_project_infers_design_stage_from_arch_dispatch(tmp_path: Path) -> None:
    task = _task(assigned="arch")
    events = _FakeEventLog({"TASK-T1": [
        _FakeEvent("task.dispatched", payload={"role": "arch"}),
    ]})
    projector = StatePacketProjector(
        state_dir=tmp_path,
        task_store=_FakeTaskStore([task]),
        event_log=events,
    )
    packet = projector.project()
    assert packet.current_stage == "design"
    assert packet.next_event == "artifact.manifest.published -> arch.proposal.done"
    assert packet.next_owner == "arch"


def test_project_infers_next_owner(tmp_path: Path) -> None:
    task = _task()
    events = _FakeEventLog({"TASK-T1": [
        _FakeEvent("review.approved"),
    ]})
    projector = StatePacketProjector(
        state_dir=tmp_path,
        task_store=_FakeTaskStore([task]),
        event_log=events,
    )
    packet = projector.project()
    assert packet.current_stage == "test"
    assert packet.next_event == "test.passed"
    assert packet.next_owner == "test"


def test_project_collects_completed_milestones(tmp_path: Path) -> None:
    task = _task()
    events = _FakeEventLog({"TASK-T1": [
        _FakeEvent("task.dispatched"),
        _FakeEvent("dev.build.done"),
        _FakeEvent("static_gate.passed"),
    ]})
    projector = StatePacketProjector(
        state_dir=tmp_path,
        task_store=_FakeTaskStore([task]),
        event_log=events,
    )
    packet = projector.project()
    assert "task.dispatched" in packet.completed
    assert "dev.build.done" in packet.completed
    assert "static_gate.passed" in packet.completed


def test_project_collects_evidence_records(tmp_path: Path) -> None:
    task = _task()
    events = _FakeEventLog({"TASK-T1": [
        _FakeEvent("review.approved", eid="evt-rev",
                   payload={"path": "/r"}),
        _FakeEvent("test.passed", eid="evt-test",
                   payload={"path": "/t"}),
    ]})
    projector = StatePacketProjector(
        state_dir=tmp_path,
        task_store=_FakeTaskStore([task]),
        event_log=events,
    )
    packet = projector.project()
    kinds = {e.kind for e in packet.evidence}
    assert {"review", "test"} <= kinds
    statuses = {e.status for e in packet.evidence}
    assert statuses == {"passed"}


def test_project_collects_evidence_from_real_event_log_query(
    tmp_path: Path,
) -> None:
    from zf.core.events.log import EventLog
    from zf.core.events.model import ZfEvent

    task = _task()
    log = EventLog(tmp_path / "events.jsonl")
    log.append(ZfEvent(
        type="review.approved",
        task_id="TASK-T1",
        payload={"path": "/r"},
    ))
    log.append(ZfEvent(
        type="test.passed",
        task_id="TASK-T1",
        payload={"path": "/t"},
    ))
    log.append(ZfEvent(
        type="review.approved",
        task_id="TASK-OTHER",
        payload={"path": "/other"},
    ))
    projector = StatePacketProjector(
        state_dir=tmp_path,
        task_store=_FakeTaskStore([task]),
        event_log=log,
    )

    packet = projector.project()

    assert [(e.kind, e.path, e.status) for e in packet.evidence] == [
        ("review", "/r", "passed"),
        ("test", "/t", "passed"),
    ]


def test_project_judge_passed_clears_next_event(tmp_path: Path) -> None:
    """After judge.passed the task is ready to ship — no further
    event required from the worker side."""
    task = _task()
    events = _FakeEventLog({"TASK-T1": [
        _FakeEvent("judge.passed"),
    ]})
    projector = StatePacketProjector(
        state_dir=tmp_path,
        task_store=_FakeTaskStore([task]),
        event_log=events,
    )
    packet = projector.project()
    assert packet.current_stage == "ship"
    assert packet.next_event == ""


# ---------------------------------------------------------------------------
# Persistence — atomic write to canonical + per-dispatch path
# ---------------------------------------------------------------------------


def test_write_canonical_state_packet(tmp_path: Path) -> None:
    projector = StatePacketProjector(state_dir=tmp_path)
    packet = StatePacket(task_id="TASK-W")
    out = projector.write(packet)
    assert out == tmp_path / "state" / "state-packet.json"
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["task_id"] == "TASK-W"
    assert data["schema_version"] == "1.0"


def test_write_per_dispatch_snapshot(tmp_path: Path) -> None:
    projector = StatePacketProjector(state_dir=tmp_path)
    packet = StatePacket(task_id="TASK-P", current_stage="implement")
    projector.write(packet, dispatch_id="disp-1")
    per_dispatch_json = (
        tmp_path / "briefings" / "TASK-P" / "disp-1" / "state-packet.json"
    )
    per_dispatch_md = (
        tmp_path / "briefings" / "TASK-P" / "disp-1" / "state-packet.md"
    )
    assert per_dispatch_json.exists()
    assert per_dispatch_md.exists()


def test_write_skips_per_dispatch_when_dispatch_id_missing(tmp_path: Path) -> None:
    """No dispatch_id → only canonical write; no per-task dir created."""
    projector = StatePacketProjector(state_dir=tmp_path)
    projector.write(StatePacket(task_id="TASK-N"))
    assert not (tmp_path / "briefings" / "TASK-N").exists()


def test_render_md_includes_task_and_stage(tmp_path: Path) -> None:
    projector = StatePacketProjector(state_dir=tmp_path)
    packet = StatePacket(
        task_id="TASK-MD",
        current_stage="review",
        objective="ship feature",
        contract=StatePacketContract(behavior="do thing"),
        evidence=(StatePacketEvidence(
            kind="test", path="/log", status="passed", event_id="evt-1",
        ),),
        next_event="test.passed",
    )
    md = projector.render_md(packet)
    assert "TASK-MD" in md
    assert "review" in md
    assert "ship feature" in md
    assert "do thing" in md
    assert "test.passed" in md
    assert "projection only" in md


# ---------------------------------------------------------------------------
# read_state_packet helper
# ---------------------------------------------------------------------------


def test_read_state_packet_missing_returns_none(tmp_path: Path) -> None:
    assert read_state_packet(tmp_path) is None


def test_read_state_packet_round_trip(tmp_path: Path) -> None:
    projector = StatePacketProjector(state_dir=tmp_path)
    original = StatePacket(task_id="TASK-R", current_stage="implement")
    projector.write(original)
    loaded = read_state_packet(tmp_path)
    assert loaded is not None
    assert loaded.task_id == "TASK-R"
    assert loaded.current_stage == "implement"


# ---------------------------------------------------------------------------
# I51 invariant — projector uses atomic_write_text (single physical
# writer for state-packet.json)
# ---------------------------------------------------------------------------


def test_projector_uses_atomic_write_text() -> None:
    """I51 candidate: StatePacketProjector.write must use
    atomic_write_text. Direct file writes would defeat the
    crash-safety + single-writer invariant."""
    import inspect

    from zf.runtime import state_packet_projector

    source = inspect.getsource(state_packet_projector)
    assert "atomic_write_text" in source


def test_projector_does_not_emit_events() -> None:
    """Projector reads truth; it must NEVER call event_writer.append.
    State Packet is a projection, not a new truth store."""
    import inspect

    from zf.runtime import state_packet_projector

    source = inspect.getsource(state_packet_projector)
    assert "event_writer.append" not in source
    assert "EventWriter(" not in source
