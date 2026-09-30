"""S2 conformance tests for EventLog, Store and evidence truth."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.testkit.evidence import sha256_file, write_json_atomic
from tests.testkit.case_evidence import attach_case_evidence
from tests.testkit.oracles import (
    assert_event_causation,
    assert_no_duplicate_event_ids,
    assert_required_events,
)
from tests.testkit.scenarios import isolated_project
from tests.testkit.snapshots import assert_snapshot_equal, truth_snapshot
from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from tests.testkit.runtime_case_evidence import attach_runtime_case_evidence
from zf.core.events.writer import EventWriter
from zf.core.security.signing import EventSigner
from zf.core.verification.event_schema import EventSchemaRegistry
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


def test_event_writer_preserves_order_correlation_and_origin(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    project = isolated_project(tmp_path, run_id="RUN-S2")
    attach_case_evidence(request, project.state_dir, metadata={
        "given": "isolated EventWriter with kernel origin and RUN-S2 correlation",
        "when": "append task.created then causally linked task.dispatched",
        "then": "ordered events with matching origin/correlation/causation",
        "forbidden": ["missing or reordered events", "incorrect causal link"],
        "identity": {"run_id": "RUN-S2", "task_id": "TASK-S2", "attempt": None,
                     "generation": None, "operation_id": None, "reason": "event contract does not create attempts or operations"},
    })
    writer = project.event_writer(origin="kernel")
    first = writer.append(ZfEvent(type="task.created", task_id="TASK-S2"))
    second = writer.append(
        ZfEvent(
            type="task.dispatched",
            task_id="TASK-S2",
            causation_id=first.id,
        )
    )

    events = project.event_log().read_all()
    assert [event.type for event in events] == ["task.created", "task.dispatched"]
    assert all(event.correlation_id == "RUN-S2" for event in events)
    assert all(event.origin == "kernel" for event in events)
    assert_required_events(project.event_log(), ["task.created", "task.dispatched"])
    assert_event_causation(
        project.event_log(),
        event_type="task.dispatched",
        causation_id=first.id,
    )
    assert second.causation_id == first.id


def test_replay_snapshot_is_semantically_equal(tmp_path: Path) -> None:
    project = isolated_project(tmp_path)
    project.emit("task.created", payload={"status": "ready"})
    original = truth_snapshot(
        project.event_log(),
        state={"task_id": project.task_id, "status": "ready"},
    )
    reloaded = EventLog(project.events_path)
    replayed = truth_snapshot(
        reloaded,
        state={"task_id": project.task_id, "status": "ready"},
    )
    assert_snapshot_equal(original, replayed)


def test_duplicate_event_id_is_detected_as_conformance_failure(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    event = ZfEvent(type="task.created", id="evt-duplicate")
    log.append(event)
    log.append(ZfEvent(type="task.replayed", id="evt-duplicate"))
    with pytest.raises(AssertionError, match="duplicate event ids"):
        assert_no_duplicate_event_ids(log)


def test_malformed_event_line_does_not_become_a_valid_event(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    valid = ZfEvent(type="task.created")
    path.write_text(valid.to_json() + "\nnot-json\n", encoding="utf-8")
    events = EventLog(path).read_all()
    assert len(events) == 1
    assert events[0].type == "task.created"


def test_corrupt_archive_is_replayable_with_bounded_malformed_marker(
    tmp_path: Path,
) -> None:
    """Archive corruption is surfaced without inventing a valid event."""
    path = tmp_path / "events.jsonl"
    archive_dir = tmp_path / "events"
    archive_dir.mkdir()
    (archive_dir / "2026-01-01.jsonl").write_text(
        ZfEvent(type="archived", id="evt-archived").to_json()
        + "\n"
        + '{"type":"corrupt","payload":[]}'
        + "\nnot-json\n",
        encoding="utf-8",
    )
    path.write_text(
        ZfEvent(type="active", id="evt-active").to_json() + "\n",
        encoding="utf-8",
    )

    events = EventLog(path).read_all()

    assert [event.type for event in events] == [
        "archived",
        "event.malformed",
        "active",
    ]
    malformed = events[1]
    assert "payload" in malformed.payload["error"]
    assert not any(event.id == "corrupt" for event in events)


@pytest.mark.parametrize(
    ("corrupt_line", "malformed_count"),
    [
        (b"[]\n", 1),
        (b'"not-an-event"\n', 1),
        (b'{"type":"wrong-payload","payload":[]}\n', 1),
        (b'{"type":"partial"', 0),
    ],
    ids=["array", "scalar", "payload-type", "partial-tail"],
)
def test_archive_corruption_shapes_never_create_valid_events(
    tmp_path: Path,
    corrupt_line: bytes,
    malformed_count: int,
) -> None:
    """Replay handles malformed archive shapes fail-closed and deterministically."""
    path = tmp_path / "events.jsonl"
    archive_dir = tmp_path / "events"
    archive_dir.mkdir()
    valid_archive = (
        ZfEvent(type="archived-valid", id="evt-archived-valid").to_json().encode()
        + b"\n"
    )
    archive_bytes = (
        valid_archive + corrupt_line
        if malformed_count == 0
        else corrupt_line + valid_archive
    )
    (archive_dir / "2026-01-02.jsonl").write_bytes(archive_bytes)
    path.write_text(
        ZfEvent(type="active-valid", id="evt-active-valid").to_json() + "\n",
        encoding="utf-8",
    )

    events = EventLog(path).read_all()

    assert [event.id for event in events if event.type != "event.malformed"] == [
        "evt-archived-valid",
        "evt-active-valid",
    ]
    assert sum(event.type == "event.malformed" for event in events) == malformed_count
    assert not any(event.id in {"wrong-payload", "partial"} for event in events)


def test_task_store_state_and_atomic_file_are_readable(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    root = tmp_path / "store"
    root.mkdir()
    attach_case_evidence(request, root, metadata={
        "given": "empty private TaskStore", "when": "add TASK-S2 then update to in_progress",
        "then": "public reads and canonical file match the update", "forbidden": ["lost or partial update"],
        "identity": {"run_id": None, "task_id": "TASK-S2", "attempt": None, "generation": None,
                     "operation_id": None, "reason": "direct Store contract without runtime attempt"},
    })
    store = TaskStore(root / "kanban.json")
    task = store.add(Task(id="TASK-S2", title="contract"))
    assert store.get(task.id).status == "backlog"
    store.update(task.id, status="in_progress")
    assert store.get(task.id).status == "in_progress"
    assert (root / "kanban.json").read_text(encoding="utf-8").endswith("\n")


def test_terminal_archive_survives_index_failure_and_retries_without_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interruption after archive write must not make a task disappear."""
    store = TaskStore(tmp_path / "kanban.json")
    task = store.add(Task(id="TASK-S2-TERMINAL", title="archive-first"))
    original_mark = store._mark_terminal

    def fail_index(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated terminal-index interruption")

    monkeypatch.setattr(store, "_mark_terminal", fail_index)
    with pytest.raises(OSError, match="terminal-index interruption"):
        store.update(task.id, status="done")

    archive_files = sorted((tmp_path / "kanban").glob("*.json"))
    assert archive_files
    archived = json.loads(archive_files[-1].read_text(encoding="utf-8"))
    assert [row["id"] for row in archived] == [task.id]
    assert store.get(task.id) is not None
    assert store.get(task.id).status == "backlog"

    monkeypatch.setattr(store, "_mark_terminal", original_mark)
    recovered = store.update(task.id, status="done")
    assert recovered is not None and recovered.status == "done"
    assert store.list_all() == []
    assert store.get(task.id).status == "done"


def test_evidence_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    from tests.testkit.oracles import assert_evidence

    path = tmp_path / "evidence.json"
    write_json_atomic(path, {"case_id": "S2", "status": "pass"})
    with pytest.raises(AssertionError, match="digest mismatch"):
        assert_evidence(path, expected_digest="0" * 64)
    assert len(sha256_file(path)) == 64


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-001: signed EventLog append-fold cache can mask same-prefix tamper",
)
def test_signed_event_roundtrip_and_tamper_rejection(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    root = tmp_path / "signed-state"
    root.mkdir()
    attach_case_evidence(request, root, metadata={
        "given": "EventLog signed with a public synthetic test key",
        "when": "append safe=true, read, rewrite bytes to safe=false, read again",
        "then": "tampered event rejected", "forbidden": ["cached acceptance of tampered bytes"],
        "identity": {"run_id": None, "task_id": None, "attempt": None, "generation": None,
                     "operation_id": None, "reason": "direct signed-log contract without runtime identities"},
    })
    path = root / "events.jsonl"
    signer = EventSigner(b"s2-secret")
    EventLog(path, signer=signer).append(
        ZfEvent(type="task.created", payload={"safe": True})
    )
    raw = path.read_text(encoding="utf-8")
    assert '"sig"' in raw and '"event"' in raw
    assert [event.type for event in EventLog(path, signer=signer).read_all()] == [
        "task.created"
    ]
    path.write_text(raw.replace("true", "false"), encoding="utf-8")
    assert EventLog(path, signer=signer).read_all() == []


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-001: signed EventLog append-fold cache can mask same-size tamper",
)
def test_signed_same_size_tamper_rejection_after_fold_cache(
    tmp_path: Path,
) -> None:
    """A same-length rewrite must not be treated as an append-only tail."""
    path = tmp_path / "events-same-size.jsonl"
    signer = EventSigner(b"s2-same-size-secret")
    log = EventLog(path, signer=signer)
    log.append(ZfEvent(type="task.created", payload={"safe": True}))
    assert [event.type for event in log.read_all()] == ["task.created"]
    raw = path.read_text(encoding="utf-8")
    tampered = raw.replace("true", "null")
    assert len(tampered) == len(raw)
    path.write_text(tampered, encoding="utf-8")
    assert EventLog(path, signer=signer).read_all() == []


@pytest.mark.xfail(
    strict=True,
    reason="ZF-TEST-001: signed EventLog fold cache can mask tamper with preserved mtime",
)
def test_signed_same_size_tamper_rejection_with_preserved_mtime(
    tmp_path: Path,
) -> None:
    """A rewrite preserving both size and mtime must still be re-verified."""
    path = tmp_path / "events-preserved-mtime.jsonl"
    signer = EventSigner(b"s2-preserved-mtime-secret")
    log = EventLog(path, signer=signer)
    log.append(ZfEvent(type="task.created", payload={"safe": True}))
    assert [event.type for event in log.read_all()] == ["task.created"]
    raw = path.read_text(encoding="utf-8")
    tampered = raw.replace("true", "null")
    assert len(tampered) == len(raw)
    stat = path.stat()
    path.write_text(tampered, encoding="utf-8")
    os.utime(path, (stat.st_atime, stat.st_mtime))
    assert EventLog(path, signer=signer).read_all() == []


def test_signed_archive_tamper_does_not_hide_valid_active_event(
    tmp_path: Path, request,
) -> None:
    """A bad archived signature is rejected while active evidence survives."""
    path = tmp_path / "signed-events.jsonl"
    signer = EventSigner(b"s2-archive-secret")
    log = EventLog(path, signer=signer)
    log.append(ZfEvent(type="archived-signed", id="evt-archived-signed"))
    old_time = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(path, (old_time, old_time))
    log.append(ZfEvent(type="active-signed", id="evt-active-signed"))

    archive = next((tmp_path / "signed-events").glob("*.jsonl"))
    envelope = json.loads(archive.read_text(encoding="utf-8"))
    envelope["sig"] = "0" * len(envelope["sig"])
    archive.write_text(json.dumps(envelope) + "\n", encoding="utf-8")

    events = EventLog(path, signer=signer).read_all()

    assert [event.id for event in events] == ["evt-active-signed"]
    assert not any(event.id == "evt-archived-signed" for event in events)


def test_schema_warning_and_blocking_are_distinguishable(tmp_path: Path) -> None:
    schema = EventSchemaRegistry.from_dict(
        {"review.approved": {"required": ["verdict"]}}
    )
    warning_log = EventLog(tmp_path / "warning.jsonl")
    warning_writer = EventWriter(
        warning_log, schema_registry=schema, schema_mode="warning"
    )
    warning_writer.append(ZfEvent(type="review.approved", payload={}))
    warning_types = [event.type for event in warning_log.read_all()]
    assert warning_types == ["review.approved", "event.schema.violated"]

    blocking_log = EventLog(tmp_path / "blocking.jsonl")
    blocking_writer = EventWriter(
        blocking_log, schema_registry=schema, schema_mode="blocking"
    )
    result = blocking_writer.append(ZfEvent(type="review.approved", payload={}))
    assert result.type == "discriminator.failed"
    assert [event.type for event in blocking_log.read_all()] == [
        "discriminator.failed"
    ]


def test_archive_rotation_is_replayed_with_active_events(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps(ZfEvent(type="old", id="evt-old").__dict__) + "\n",
        encoding="utf-8",
    )
    old_time = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(path, (old_time, old_time))
    log = EventLog(path)
    log.append(ZfEvent(type="new", id="evt-new"))
    assert (tmp_path / "events").exists()
    assert [event.type for event in log.read_all()] == ["old", "new"]


def test_partial_tail_does_not_advance_event_offset(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(ZfEvent(type="complete", id="evt-complete"))
    offset = log.current_offset()
    with path.open("ab") as stream:
        stream.write(b'{"type":"partial"')
    events, new_offset = log.read_from_offset(offset)
    assert events == []
    assert new_offset == offset


def test_concurrent_event_writers_append_all_unique_events(tmp_path: Path) -> None:
    path = tmp_path / "events-concurrent.jsonl"

    def append(index: int) -> None:
        EventWriter(EventLog(path)).append(
            ZfEvent(type="worker.heartbeat", id=f"evt-concurrent-{index}")
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(32)))

    events = EventLog(path).read_all()
    assert len(events) == 32
    assert {event.id for event in events} == {
        f"evt-concurrent-{index}" for index in range(32)
    }
    assert_no_duplicate_event_ids(EventLog(path))
