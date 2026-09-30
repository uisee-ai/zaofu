"""Property/stateful checks for the TestKit's event-log truth model.

The state machine deliberately exercises only deterministic local files.  It
does not call a provider or a remote integration; generated counterexamples
remain safe to replay in a normal pytest case.
"""

from __future__ import annotations

from pathlib import Path
import tempfile

from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.state.task_attempts import TaskAttemptStore
from tests.testkit.model_evidence import ModelEvidence, record_action, record_invariant


class EventLogReplayMachine(RuleBasedStateMachine):
    """Keep append/reopen behavior equivalent to a small reference model."""

    def __init__(self) -> None:
        super().__init__()
        self._temporary = tempfile.TemporaryDirectory(prefix="zf-hypothesis-")
        self.path = Path(self._temporary.name) / "events.jsonl"
        self.log = EventLog(self.path)
        self.expected: list[tuple[str, str, int]] = []
        self.evidence = ModelEvidence(
            root=Path(self._temporary.name), model_name=type(self).__qualname__,
            source=Path(__file__), settings=str(type(self).TestCase.settings),
            reference=lambda: {"expected": self.expected},
        )

    @rule(
        event_type=st.sampled_from(("task.created", "task.updated", "task.reviewed")),
        marker=st.integers(min_value=-1000, max_value=1000),
    )
    @record_action
    def append_event(self, event_type: str, marker: int) -> None:
        event_id = f"hypothesis-{len(self.expected):04d}"
        self.log.append(
            ZfEvent(
                id=event_id,
                type=event_type,
                task_id="TASK-HYPOTHESIS",
                payload={"marker": marker},
            )
        )
        self.expected.append((event_id, event_type, marker))

    @rule()
    @record_action
    def reopen_and_replay(self) -> None:
        replayed = EventLog(self.path).read_all()
        assert [event.id for event in replayed] == [row[0] for row in self.expected]
        assert [event.type for event in replayed] == [row[1] for row in self.expected]
        assert [event.payload["marker"] for event in replayed] == [
            row[2] for row in self.expected
        ]

    @invariant()
    @record_invariant
    def live_read_matches_reference(self) -> None:
        events = self.log.read_all()
        assert len(events) == len(self.expected)
        assert [event.id for event in events] == [row[0] for row in self.expected]

    def teardown(self) -> None:
        self._temporary.cleanup()
        super().teardown()


TestEventLogReplay = EventLogReplayMachine.TestCase
TestEventLogReplay.settings = settings(
    max_examples=100, stateful_step_count=50, derandomize=True,
    database=None, deadline=None, print_blob=True,
)


ATTEMPT_SEQUENCE_SETTINGS = settings(
    max_examples=25, derandomize=True, database=None, deadline=None, print_blob=True,
)


@ATTEMPT_SEQUENCE_SETTINGS
@given(dispatches=st.lists(st.integers(min_value=0, max_value=5), min_size=1, max_size=20))
def test_task_attempt_dispatch_identity_is_idempotent(dispatches: list[int]) -> None:
    """Generated duplicate dispatches never create a second row for one ID."""
    with tempfile.TemporaryDirectory(prefix="zf-attempt-hypothesis-") as directory:
        store = TaskAttemptStore(Path(directory) / "task_attempts.json")
        seen: dict[str, dict[str, object]] = {}
        last_new_dispatch = ""
        evidence = ModelEvidence(
            root=Path(directory), model_name="TaskAttemptDispatchSequence", source=Path(__file__),
            settings=str(ATTEMPT_SEQUENCE_SETTINGS),
            reference=lambda: {"seen_dispatches": sorted(seen), "last_new_dispatch": last_new_dispatch},
        )
        with evidence.guard("dispatch_sequence", {"dispatches": dispatches}):
            for index, value in enumerate(dispatches):
                dispatch_id = f"dispatch-{value}"
                result = store.ensure_for_dispatch(
                    run_id="run-hypothesis",
                    task_id="TASK-HYPOTHESIS",
                    dispatch_id=dispatch_id,
                    role="dev",
                    instance_id="dev-1",
                    operation_id="operation-hypothesis",
                    briefing_ref="briefings/hypothesis.md",
                    created_at=f"2026-09-11T00:00:{index:02d}+00:00",
                    lease_expires_at="2026-09-11T01:00:00+00:00",
                    max_attempts=100,
                )
                if dispatch_id in seen:
                    assert result.created is False
                    assert result.attempt["attempt_id"] == seen[dispatch_id]["attempt_id"]
                else:
                    assert result.created is True
                    seen[dispatch_id] = result.attempt
                    last_new_dispatch = dispatch_id

            current = store.current(run_id="run-hypothesis", task_id="TASK-HYPOTHESIS")
            assert current is not None
            assert current["dispatch_id"] == last_new_dispatch
            assert len(store.current_rows()) == 1
            assert len(store.rows()) == len(seen)
