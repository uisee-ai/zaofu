"""EVT-05 production ingress, distinct from raw-corruption oracle canaries.

Method: event_ingress_dedup_research.md. Fixed occurrence reference, real
default stage projector and Store; no self-deduplicating fake reducer/sink.
Service reopen is not process crash. No Provider, network or live state.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

import pytest

from tests.conformance.test_zaofu_testkit_s4 import _orchestrator_context
from tests.test_scheduler_task_attempt import _RecordingTransport
from tests.testkit.evidence import write_json_atomic
from tests.testkit.model_evidence import ModelEvidence
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.core.verification.event_schema import EventSchemaRegistry
from zf.runtime import stage_reports


class DuplicateOccurrenceAccepted(AssertionError):
    """A successful Writer call reaccepted an already accepted occurrence."""


class FixedOccurrenceModel:
    """Reference is supplied by the case, never inferred from actual output."""

    def __init__(self, root: Path, patch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.state = root / "custom-state"
        self.state.mkdir()
        self.path = self.state / "events.jsonl"
        self.patch = patch
        patch.delenv("ZF_EVENT_LOG_MAX_ACTIVE_BYTES", raising=False)
        self.store = TaskStore(self.state / "kanban.json")
        self.store.add(Task(id="TASK-EVT05", title="fixed ingress", status="in_progress"))
        self.initial_task = asdict(self.store.get("TASK-EVT05"))
        self.expected: list[dict] = []
        self.inputs: list[dict] = []
        self.calls: list[dict] = []
        original = stage_reports.project_stage_report_for_event

        def observe(state_dir: Path, event: ZfEvent, **kwargs):
            if Path(state_dir) == self.state and event.type == "dev.build.done":
                self.calls.append(asdict(event))  # Count every call, then run production.
            return original(state_dir, event, **kwargs)

        patch.setattr(stage_reports, "project_stage_report_for_event", observe)
        self.reopen()
        self.writer.append(ZfEvent(
            id="EVT05-PARENT", type="task.created", task_id="TASK-EVT05",
            ts="2030-01-01T00:00:00+00:00", origin="kernel",
            correlation_id="RUN-EVT05",
        ))
        self.evidence = ModelEvidence(
            root=root, model_name="FixedOccurrenceModel", source=Path(__file__),
            settings="fixed cases; no generated/shrunk sequence",
            reference=lambda: {"accepted": self.expected, "initial_task": self.initial_task},
        )

    def reopen(self) -> None:
        if hasattr(self, "log"):
            self.log.close()
        self.log = EventLog(self.path)
        self.writer = EventWriter(
            self.log, schema_mode="blocking",
            schema_registry=EventSchemaRegistry.from_dict({
                "dev.build.done": {"required": ["result"], "non_empty": ["result"]},
            }),
        )

    def event(self, *, event_id: str = "EVT05-BUILD", result: str = "first") -> dict:
        return asdict(ZfEvent(
            id=event_id, type="dev.build.done", task_id="TASK-EVT05", actor="dev",
            ts="2030-01-01T00:00:01+00:00", origin="worker",
            causation_id="EVT05-PARENT", correlation_id="RUN-EVT05",
            payload={"result": result},
        ))

    def submit(self, row: dict) -> ZfEvent:
        # Freeze inputs: Writer enrichment must not mutate our reference.
        self.inputs.append(json.loads(json.dumps(row)))
        write_json_atomic(self.root / "inputs.json", {"events": self.inputs})
        with self.evidence.guard("writer.append", {"event": row}):
            result = self.writer.append(ZfEvent.from_dict(json.loads(json.dumps(row))))
            diagnostics = [asdict(item) for item in self.writer.projector_diagnostics]
            write_json_atomic(self.root / "observer.json", {
                "stage_calls": self.calls, "last_projector_diagnostics": diagnostics,
                "returned_event": asdict(result),
            })
            assert not any(item["status"] == "failed" for item in diagnostics), diagnostics
            return result

    def boundary(self, mode: str) -> None:
        with self.evidence.guard("boundary", {"mode": mode}):
            self._apply_boundary(mode)

    def _apply_boundary(self, mode: str) -> None:
        if mode == "archive":
            # Real normal size rotation, not hand-written/moved archive data.
            self.patch.setenv("ZF_EVENT_LOG_MAX_ACTIVE_BYTES", "1")
            self.writer.append(ZfEvent(type="testkit.rotation.tick", id="EVT05-TICK"))
            self.patch.delenv("ZF_EVENT_LOG_MAX_ACTIVE_BYTES")
            assert any(
                '"EVT05-BUILD"' in path.read_text()
                for path in (self.state / "events").glob("*.jsonl")
            ), "the original accepted occurrence must really be archived"
        if mode != "same-writer":
            self.reopen()

    def check(self, *, duplicate_probe: bool = False) -> None:
        with self.evidence.guard("accepted-occurrence-invariant", {}, action=False):
            physical = [
                json.loads(line)
                for path in [*sorted((self.state / "events").glob("*.jsonl")), self.path]
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            actual = [row for row in physical if row["type"] == "dev.build.done"]
            reopened = [asdict(row) for row in EventLog(self.path).read_all()
                        if row.type == "dev.build.done"]
            assert reopened == actual, "reader must expose the actual raw ledger"
            task = asdict(TaskStore(self.state / "kanban.json").get("TASK-EVT05"))
            assert task == self.initial_task, "append-side projection must not mutate TaskStore"
            assert not any(row["type"] in {"run.goal.completed", "task.done"} for row in physical)
            report = json.loads((self.state / "artifacts/stage-reports/impl-report.json").read_text())
            expected_ids = [row["id"] for row in self.expected]
            expected_by_id = {row["id"]: row for row in self.expected}
            assert self.log.index is not None, "index is required by this test fixture"
            indexed = {
                key: asdict(event) if (event := self.log.index.lookup_event(key)) else None
                for key in expected_by_id
            }
            checks = {
                "raw_occurrences": actual == self.expected,
                "projector_calls": self.calls == self.expected,
                "report_count": report["summary"]["event_count"] == len(self.expected),
                "report_ids": [row["id"] for row in report["events"]] == expected_ids,
                "accepted_identity_not_replaced": indexed == expected_by_id,
            }
            write_json_atomic(self.root / "observed.json", {
                "checks": checks, "raw_occurrences": actual, "reopened_occurrences": reopened,
                "projector_calls": self.calls, "indexed": indexed, "task": task, "report": report,
            })
            failures = [name for name, passed in checks.items() if not passed]
            reproduced_duplicate = (
                len(self.expected) == 1 and len(actual) == 2
                and actual[0] == self.expected[0]
                and actual[1]["id"] == actual[0]["id"]
                and self.calls == actual
                and report["summary"]["event_count"] == 2
                and [row["id"] for row in report["events"]] == [actual[0]["id"]] * 2
            )
            if duplicate_probe and reproduced_duplicate:
                raise DuplicateOccurrenceAccepted(
                    f"{failures}: accepted={len(actual)}, projector_calls={len(self.calls)}, "
                    f"report_count={report['summary']['event_count']}, expected={len(self.expected)}"
                )
            assert not failures, failures


@pytest.fixture
def occurrence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    model = FixedOccurrenceModel(tmp_path, monkeypatch)
    try:
        yield model
    finally:
        model.log.close()


@pytest.mark.parametrize("mode", ["same-writer", "reopen", "archive"])
@pytest.mark.parametrize("conflict", [False, True], ids=["identical", "payload-conflict"])
@pytest.mark.xfail(
    strict=True, raises=DuplicateOccurrenceAccepted,
    reason="ZF-TEST-039: Writer accepts duplicate IDs and repeats the real stage projection",
)
def test_writer_rejects_reaccepted_occurrence(occurrence, mode: str, conflict: bool) -> None:
    original = occurrence.event()
    occurrence.expected = [original]
    assert asdict(occurrence.submit(original)) == original
    occurrence.check()  # Nonzero baseline must work before the defective step.
    occurrence.boundary(mode)
    write_json_atomic(occurrence.root / "before-redelivery.json", {
        "report": json.loads((occurrence.state / "artifacts/stage-reports/impl-report.json").read_text()),
        "task": asdict(occurrence.store.get("TASK-EVT05")),
    })
    occurrence.submit(occurrence.event(result="conflict" if conflict else "first"))
    occurrence.check(duplicate_probe=True)


def test_identical_body_with_new_id_is_a_distinct_occurrence(occurrence) -> None:
    first = occurrence.event()
    second = occurrence.event(event_id="EVT05-SECOND")
    assert {key: value for key, value in first.items() if key != "id"} == {
        key: value for key, value in second.items() if key != "id"
    }
    occurrence.expected = [first, second]
    occurrence.submit(first)
    occurrence.submit(second)
    occurrence.check()


def test_nonduplicate_index_error_is_not_the_known_duplicate_failure(occurrence) -> None:
    original = occurrence.event()
    occurrence.expected = [original]
    occurrence.submit(original)
    occurrence.check()
    # A separate index-only regression must not be hidden by ZF-TEST-039.
    occurrence.log.index.observe(ZfEvent.from_dict(occurrence.event(result="wrong-index")))
    with pytest.raises(AssertionError) as failure:
        occurrence.check(duplicate_probe=True)
    assert type(failure.value) is AssertionError


def test_blocked_schema_retry_does_not_claim_the_unaccepted_id(occurrence) -> None:
    invalid = occurrence.event(result="")
    results = [occurrence.submit(invalid), occurrence.submit(invalid)]
    assert len({event.id for event in results}) == 2
    assert all(event.type == "discriminator.failed" for event in results)
    assert all(event.payload["blocked_event_id"] == invalid["id"] for event in results)
    assert not occurrence.calls
    assert not any(event.id == invalid["id"] for event in occurrence.log.read_all())
    corrected = occurrence.event()
    occurrence.expected = [corrected]
    assert asdict(occurrence.submit(corrected)) == corrected
    occurrence.check()


@pytest.mark.parametrize("reopen", [False, True], ids=["same-runtime", "new-runtime"])
def test_duplicate_created_input_does_not_add_dispatch_or_attempt(
    tmp_path: Path, reopen: bool,
) -> None:
    project, state, store, log, make_runtime = _orchestrator_context(tmp_path)
    transport = _RecordingTransport(state)  # Records every send; does not deduplicate.
    runtime = make_runtime()
    runtime.transport = transport
    store.add(Task(id=project.task_id, title="ingress dispatch", status="backlog"))
    event = ZfEvent(
        id="EVT05-CREATED", type="task.created", task_id=project.task_id,
        ts="2030-01-01T00:00:00+00:00", origin="kernel",
        correlation_id=project.run_id, causation_id=log.read_all()[-1].id,
    )
    first = runtime.event_writer.append(ZfEvent.from_dict(asdict(event)))
    runtime.run_once(events=[first])
    attempts_before = TaskAttemptStore(state / "task_attempts.json").load()["attempts"]
    assert len(attempts_before) == len(transport.contexts) == 1
    active_dispatch = store.get(project.task_id).active_dispatch_id
    assert active_dispatch
    if reopen:
        runtime = make_runtime()
        runtime.transport = transport
    # This intentionally reaches the consumer even though ingress is defective.
    # Its narrower protection cannot make the six Writer probes pass.
    second = runtime.event_writer.append(ZfEvent.from_dict(asdict(event)))
    runtime.run_once(events=[second])
    task = TaskStore(state / "kanban.json").get(project.task_id)
    attempts_after = TaskAttemptStore(state / "task_attempts.json").load()["attempts"]
    assert attempts_after == attempts_before
    assert task.status == "in_progress" and task.active_dispatch_id == active_dispatch
    assert task.retry_count == 0
    assert len(transport.contexts) == 1
    events = log.read_all()
    assert sum(row.type == "task.dispatched" for row in events) == 1
    assert sum(row.type == "task.attempt.started" for row in events) == 1
    assert not any(row.type in {"task.done", "run.goal.completed"} for row in events)
