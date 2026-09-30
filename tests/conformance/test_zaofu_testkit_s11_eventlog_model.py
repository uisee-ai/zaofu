"""Occurrence, blocking-schema and archive model; not an ingress dedupe claim.

Method/source audit: eventlog_model_research.md. Raw corruption and duplicates
are deliberate test inputs. Generated time only advances; no Provider or host.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
import pytest

from tests.testkit.command_runner import run_command
from tests.testkit.model_evidence import ModelEvidence, record_action, record_invariant, replay_steps
from tests.testkit.oracles import assert_no_duplicate_event_ids
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.events.projectors import ProjectorRunner
from zf.core.verification.event_schema import EventSchemaRegistry


RULES = {"testkit.checked": {
    "required": ["token", "verdict", "detail"], "non_empty": ["token"],
    "enum": {"verdict": ["ok", "reject"]}, "nested": {"detail": {"required": ["marker"]}},
}}
BAD_ROWS = {
    "json": ("not-json", None),
    "array": ("[]", "top-level must be object"),
    "type": ('{"type":0}', "missing or non-string `type`"),
    "payload": ('{"type":"bad","payload":[]}', "`payload` must be a dict"),
}


def semantic(event: ZfEvent) -> dict:
    row = asdict(event)
    if event.type == "event.malformed":
        # Diagnostic IDs/time are freshly minted on decode, not ledger facts.
        row.pop("id")
        row.pop("ts")
    return row


class EventLedgerMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.temporary = tempfile.TemporaryDirectory(prefix="zf-event-model-")
        self.root = Path(self.temporary.name)
        self.path = self.root / "custom-state/events.jsonl"
        self.patch = pytest.MonkeyPatch()
        self.day = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.active_day = self.day.date().isoformat()
        self.shards: dict[str, int] = {}
        self.tick = 0
        self.patch.setattr("zf.core.state.rotation._today_utc", lambda: self.day.date().isoformat())
        self.patch.delenv("ZF_EVENT_LOG_MAX_ACTIVE_BYTES", raising=False)
        self.expected: list[dict] = []
        self.inputs: list[dict] = []
        self.physical = b""
        self.pending: tuple[bytes, dict] | None = None
        self.pending_offset = 0
        self.next_id = 0
        self._open()
        self.evidence = ModelEvidence(
            root=self.root, model_name=type(self).__qualname__, source=Path(__file__),
            settings=str(type(self).TestCase.settings),
            reference=lambda: {"expected": self.expected, "physical_hex": self.physical.hex(),
                               "pending": None if self.pending is None else {"suffix_hex": self.pending[0].hex(), "event": self.pending[1]},
                               "day": self.day.isoformat(), "tick": self.tick, "shards": self.shards,
                               "active_day": self.active_day, "pending_offset": self.pending_offset},
        )

    def _open(self) -> None:
        self.log = EventLog(self.path)
        self.writer = EventWriter(self.log, schema_registry=EventSchemaRegistry.from_dict(RULES),
                                  schema_mode="blocking", projector_runner=ProjectorRunner(()))

    def _files(self) -> dict[str, bytes]:
        paths = sorted((self.path.parent / "events").glob("*.jsonl"))
        if self.path.exists():
            paths.append(self.path)
        return {str(path.relative_to(self.root)): path.read_bytes() for path in paths}

    def _stamp(self) -> None:
        self.tick += 1
        stamp = (self.day + timedelta(seconds=self.tick)).timestamp()
        os.utime(self.path, (stamp, stamp))
        self.active_day = self.day.date().isoformat()

    def _event(self, marker: int = 0, token: str = "ok") -> ZfEvent:
        self.next_id += 1
        return ZfEvent(type="testkit.checked", id=f"model-{self.next_id:04d}",
                       ts=(self.day + timedelta(seconds=self.next_id)).isoformat(), actor="testkit",
                       payload={"token": token, "verdict": "ok", "detail": {"marker": marker}},
                       correlation_id="RUN-EVENT-MODEL", origin="worker")

    def _accept(self, event: ZfEvent, *, via_writer: bool = True) -> None:
        if via_writer:
            returned = self.writer.append(event)
            assert asdict(returned) == asdict(event)
        else:
            self.log.append(event)
        self._stamp()
        self._record_written(asdict(event))
        self.inputs.append(asdict(event))

    def _record_written(self, expected: dict) -> None:
        combined = b"".join(self._files().values())
        assert combined.startswith(self.physical), "append/rotation changed the previous byte prefix"
        delta = combined[len(self.physical):]
        assert delta.endswith(b"\n") and len(delta.splitlines()) == 1
        assert json.loads(delta) == expected
        self.physical = combined  # Only bytes whose complete semantics were checked above.
        self.expected.append(expected)

    @precondition(lambda self: self.pending is None)
    @rule(marker=st.integers(-100, 100), token=st.sampled_from(["ok", "界", "🙂"]))
    @record_action
    def append_valid(self, marker: int, token: str) -> None:
        self._accept(self._event(marker, token))

    @precondition(lambda self: self.pending is None)
    @rule(kind=st.sampled_from(["missing", "empty", "enum", "nested", "nested_missing"]))
    @record_action
    def reject_payload(self, kind: str) -> None:
        event = self._event()
        if kind == "missing":
            del event.payload["token"]
            field, code = "token", "missing_required"
        elif kind == "empty":
            event.payload["token"] = ""
            field, code = "token", "empty_required"
        elif kind == "enum":
            event.payload["verdict"] = "unknown"
            field, code = "verdict", "enum_mismatch"
        elif kind == "nested":
            event.payload["detail"] = []
            field, code = "detail", "type_mismatch"
        else:
            event.payload["detail"] = {}
            field, code = "detail.marker", "missing_required"
        failure = self.writer.append(event)
        assert failure.type == "discriminator.failed" and failure.id != event.id
        assert failure.id and all(row.get("id") != failure.id for row in self.expected)
        assert failure.actor == "zf-cli" and failure.task_id is None and failure.origin == ""
        assert failure.correlation_id == event.correlation_id and failure.causation_id is None
        body = failure.payload
        assert set(body) == {"failed_d", "blocked_event_id", "blocked_event_type", "blocked_event_payload", "violations", "mode"}
        assert body["failed_d"] == ["EventSchemaD"] and body["mode"] == "blocking"
        assert body["blocked_event_id"] == event.id and body["blocked_event_type"] == event.type
        assert body["blocked_event_payload"] == event.payload
        assert [(v["field_path"], v["code"]) for v in body["violations"]] == [(f"payload.{field}", code)]
        assert all(v["event_type"] == event.type and v["expected"] and v["actual"] for v in body["violations"])
        self._stamp()
        self._record_written(asdict(failure))  # Opaque generated identity + independently verified failure semantics.

    @precondition(lambda self: self.pending is None and bool(self.inputs))
    @rule(index=st.integers(0, 20))
    @record_action
    def append_duplicate(self, index: int) -> None:
        event = ZfEvent(**self.inputs[index % len(self.inputs)])
        self._accept(event, via_writer=False)
        with pytest.raises(AssertionError, match="duplicate event ids"):
            assert_no_duplicate_event_ids(self.log)

    @precondition(lambda self: self.pending is None)
    @rule(kind=st.sampled_from(tuple(BAD_ROWS)))
    @record_action
    def inject_bad_row(self, kind: str) -> None:
        line, error = BAD_ROWS[kind]
        raw = (line + "\n").encode()
        self._raw_append(raw)
        if error:
            self.expected.append({"type": "event.malformed", "actor": "zf-cli", "task_id": None,
                "payload": {"line": line, "error": error}, "causation_id": None, "correlation_id": None, "origin": ""})

    def _raw_append(self, body: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as stream:
            stream.write(body)
        self.physical += body
        self._stamp()

    @precondition(lambda self: self.pending is None)
    @rule(token=st.sampled_from(["界", "🙂", "é"]))
    @record_action
    def begin_tail(self, token: str) -> None:
        event = self._event(token=token)
        raw = (event.to_json() + "\n").encode()
        split = raw.index(token.encode()) + 1  # Deliberately inside the multibyte code point.
        self.pending_offset = self.path.stat().st_size if self.path.exists() else 0
        self.pending = (raw[split:], asdict(event))
        self._raw_append(raw[:split])

    @precondition(lambda self: self.pending is not None)
    @rule()
    @record_action
    def complete_tail(self) -> None:
        assert self.pending is not None
        tail, expected = self.pending
        self._raw_append(tail)
        events, offset = self.log.read_from_offset(self.pending_offset)
        assert [semantic(event) for event in events] == [expected]
        assert offset == self.path.stat().st_size
        self.expected.append(expected)
        self.inputs.append(expected)
        self.pending = None

    @precondition(lambda self: self.pending is None)
    @rule(mode=st.sampled_from(["size", "day"]))
    @record_action
    def rotate_and_append(self, mode: str) -> None:
        before = self._files()
        active_key = str(self.path.relative_to(self.root))
        previous = before.pop(active_key, None)
        archived_key = None
        if previous is not None:
            if mode == "day":
                name = f"{self.active_day}.jsonl"
            else:
                self.shards[self.active_day] = self.shards.get(self.active_day, 0) + 1
                name = f"{self.active_day}-{self.shards[self.active_day]:04d}.jsonl"
            archived_key = f"custom-state/events/{name}"
        if mode == "day":
            self.day += timedelta(days=1)
        else:
            self.patch.setenv("ZF_EVENT_LOG_MAX_ACTIVE_BYTES", "1")
        try:
            self._accept(self._event())
        finally:
            self.patch.delenv("ZF_EVENT_LOG_MAX_ACTIVE_BYTES", raising=False)
        after = self._files()
        assert all(after.get(key) == body for key, body in before.items())
        expected_paths = set(before) | {active_key}
        if archived_key:
            expected_paths.add(archived_key)
            assert after.get(archived_key) == previous
        assert set(after) == expected_paths, "rotation did not create exactly the expected archive"

    @rule()
    @record_action
    def reopen(self) -> None:
        self.log.close()
        self._open()

    @invariant()
    @record_invariant
    def ledger_matches_reference(self) -> None:
        before = self._files()
        assert b"".join(before.values()) == self.physical
        assert [semantic(event) for event in self.log.read_all()] == self.expected
        assert [semantic(event) for event in EventLog(self.path).read_all()] == self.expected
        if self.pending is not None:
            assert self.log.read_from_offset(self.pending_offset) == ([], self.pending_offset)
        assert self._files() == before, "reading mutated the canonical ledger"

    def teardown(self) -> None:
        self.patch.undo()
        self.temporary.cleanup()
        super().teardown()


TestEventLedger = EventLedgerMachine.TestCase
TestEventLedger.settings = settings(max_examples=30, stateful_step_count=30, derandomize=True,
                                    database=None, deadline=None, print_blob=True)


def test_fixed_archive_schema_partial_and_duplicate_trace(tmp_path: Path) -> None:
    model = EventLedgerMachine()
    steps = [
        {"action": "append_valid", "arguments": {"marker": 1, "token": "界"}},
        {"action": "rotate_and_append", "arguments": {"mode": "size"}},
        {"action": "rotate_and_append", "arguments": {"mode": "size"}},
        {"action": "rotate_and_append", "arguments": {"mode": "day"}},
        *[{"action": "reject_payload", "arguments": {"kind": kind}} for kind in ("missing", "empty", "enum", "nested", "nested_missing")],
        *[{"action": "inject_bad_row", "arguments": {"kind": kind}} for kind in BAD_ROWS],
        {"action": "begin_tail", "arguments": {"token": "🙂"}},
        {"action": "reopen", "arguments": {}},
        {"action": "complete_tail", "arguments": {}},
        {"action": "append_duplicate", "arguments": {"index": 0}},
        {"action": "reopen", "arguments": {}},
    ]
    try:
        replay_steps(model, steps, model.ledger_matches_reference)
        assert {path.name for path in (model.path.parent / "events").iterdir()} == {
            "2026-01-01-0001.jsonl", "2026-01-01-0002.jsonl", "2026-01-01.jsonl",
        }
        repo = Path(__file__).resolve().parents[2]
        before_cold = model._files()
        receipt = run_command([sys.executable, "-c",
            "import json, sys; from pathlib import Path; from zf.core.events import EventLog; "
            "from tests.conformance.test_zaofu_testkit_s11_eventlog_model import semantic; "
            "print(json.dumps([semantic(e) for e in EventLog(Path(sys.argv[1])).read_all()]))", str(model.path)],
            command_id="cold-replay", cwd=repo, output_dir=tmp_path / "receipts", timeout_seconds=15,
            env={"PYTHONPATH": str(repo)})
        assert receipt.passed and not receipt.environment_error
        assert json.loads(Path(receipt.stdout_ref).read_text()) == model.expected
        assert model._files() == before_cold
    finally:
        model.teardown()


@pytest.mark.parametrize("fault", ["drop", "reorder", "hide_malformed"])
def test_model_oracle_detects_wrong_reads_before_fixture_cleanup(tmp_path: Path, monkeypatch, fault: str) -> None:
    output = tmp_path / "captured"
    monkeypatch.setenv("ZF_TESTKIT_MODEL_EVIDENCE", str(output))
    model = EventLedgerMachine()
    root = model.root
    try:
        model.append_valid(1, "ok")
        model.append_valid(2, "界")
        model.inject_bad_row("type")
        events = model.log.read_all()
        broken = events[1:] if fault == "drop" else list(reversed(events)) if fault == "reorder" else events[:-1]
        monkeypatch.setattr(model.log, "read_all", lambda: broken)
        with pytest.raises(AssertionError) as caught:
            model.ledger_matches_reference()
        assert any("TestKit model evidence:" in note for note in caught.value.__notes__)
    finally:
        model.teardown()
    assert not root.exists()
    snapshots = list(output.glob("*/latest.json"))
    assert len(snapshots) == 1
    snapshot = json.loads(snapshots[0].read_text())
    assert snapshot["complete"] and snapshot["failed_check"] == "ledger_matches_reference"
    assert snapshot["reference"]["expected"][-1]["type"] == "event.malformed"
    assert any(row["path"] == "custom-state/events.jsonl" for row in snapshot["files"])


class EventIndexMismatch(AssertionError):
    """Only the observed query/ledger mismatch, not environment failures."""


@pytest.mark.parametrize("scenario", [
    pytest.param("external_append", marks=pytest.mark.xfail(
        strict=True, raises=EventIndexMismatch, reason="ZF-TEST-034: warm task index misses another writer's event")),
    pytest.param("timestamp_order", marks=pytest.mark.xfail(
        strict=True, raises=EventIndexMismatch, reason="ZF-TEST-035: index flush changes occurrence order")),
])
def test_task_index_preserves_complete_occurrence_order(tmp_path: Path, scenario: str) -> None:
    path = tmp_path / "state/events.jsonl"
    log = EventLog(path)
    first = ZfEvent(id="index-a", type="task.created", task_id="TASK-INDEX", ts="2026-01-01T12:00:00Z")
    second = ZfEvent(id="index-b", type="task.updated", task_id="TASK-INDEX",
                     ts="2026-01-01T11:00:00Z" if scenario == "timestamp_order" else "2026-01-01T13:00:00Z")
    evidence = ModelEvidence(root=tmp_path, model_name="EventIndexOccurrence", source=Path(__file__),
                             reference=lambda: {"scenario": scenario, "expected_ids": [first.id, second.id]})
    with evidence.guard("append_query", {"scenario": scenario}):
        log.append(first)
        assert [e.id for e in log.events_for_task(first.task_id)] == [first.id]
        if scenario == "external_append":
            EventLog(path).append(second)
        else:
            log.append(second)
            log.close()
        expected = [first.id, second.id]
        assert [e.id for e in log.read_all()] == expected
        assert [e.id for e in EventLog(path).events_for_task(first.task_id)] == expected
        actual = [e.id for e in log.query(task_id=first.task_id)]
        if actual != expected:
            raise EventIndexMismatch(f"{scenario}: query={actual}, ledger={expected}")
