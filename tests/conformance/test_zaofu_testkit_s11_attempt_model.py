"""Generated TaskAttempt transport/admission/recovery sequences with a fake clock.

The real Orchestrator transport seam and runtime attempt functions are used.
This is not full workflow/generation admission or a real Provider test.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from tests.test_scheduler_task_attempt import _RecordingTransport, _send
from tests.testkit.model_evidence import ModelEvidence, record_action, record_invariant
from zf.core.config.schema import ProjectConfig, RoleConfig, WorkflowConfig, WorkflowTaskAttemptConfig, ZfConfig
from zf.core.state.session import SessionStore
from zf.core.state.task_attempts import TaskAttemptLimitError, TaskAttemptStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.events import ZfEvent
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_attempt_runtime import (
    TaskAttemptDeliveryClaimedError, dispatch_attempt_payload,
    reconcile_task_attempts, settle_task_attempt_result, validate_task_attempt_result,
)


ATTEMPT_SETTINGS = settings(
    max_examples=25, stateful_step_count=20, deadline=None,
    database=None, derandomize=True, print_blob=True,
)


class DuplicateBudgetEvent(AssertionError):
    """Only the independently reproduced budget-event duplication defect."""


class TaskAttemptLifecycleModel(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.temporary = TemporaryDirectory(prefix="zf-attempt-model-")
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.state.mkdir()
        SessionStore(self.state / "session.yaml").create(project_root=str(self.root))
        TaskStore(self.state / "kanban.json").add(Task(
            id="TASK-1", title="attempt model", status="in_progress", assigned_to="dev",
            contract=TaskContract(evidence_contract={"workflow_run_id": "RUN-1"}),
        ))
        self.config = ZfConfig(
            project=ProjectConfig(name="attempt-model", state_dir="state"),
            roles=[RoleConfig(name="dev", instance_id="dev", backend="mock", publishes=["dev.build.done"])],
            workflow=WorkflowConfig(task_attempt=WorkflowTaskAttemptConfig(mode="enforce", max_attempts=3)),
        )
        self.seconds = 0
        self.contexts = []
        self.expected: list[dict] = []
        self.transports: list[_RecordingTransport] = []
        self.task_status = "in_progress"
        self.exhausted_series: set[int] = set()
        self.patch = pytest.MonkeyPatch()
        self.patch.setattr("zf.runtime.task_attempt_runtime._now", self.now)
        self.patch.setattr("zf.runtime.task_attempt_runtime._lease_expiry", lambda _: self.now(30))
        self.patch.setattr("zf.runtime.task_attempt_runtime._expired", lambda value: value <= self.now())
        self.evidence = ModelEvidence(
            root=self.root, model_name=type(self).__qualname__, source=Path(__file__),
            settings=str(ATTEMPT_SETTINGS), reference=lambda: {
                "seconds": self.seconds, "attempts": self.expected, "task_status": self.task_status,
                "exhausted_series": sorted(self.exhausted_series),
            },
        )
        self.reopen()
        self.evidence.steps.clear()

    def now(self, delta: int = 0) -> str:
        return (datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=self.seconds + delta)).isoformat()

    @rule()
    @record_action
    def reopen(self) -> None:
        transport = _RecordingTransport(self.state)
        self.transports.append(transport)
        self.runtime = Orchestrator(self.state, self.config, transport, project_root=self.root)
        self.store = TaskAttemptStore(self.state / "task_attempts.json")

    # Keep exploring other transitions after the first cap notification.
    # Repeating an exhausted dispatch is tested separately below, including
    # reopen; that known bug must not terminate every generated exploration.
    @precondition(lambda self: len(self.expected) < 8 and (
        not self.expected or self.expected[-1]["status"] == "succeeded"
        or self.expected[-1]["ordinal"] < 3
        or self.expected[-1]["series"] not in self.exhausted_series
    ))
    @rule()
    @record_action
    def dispatch(self) -> None:
        prior = self.expected[-1] if self.expected else None
        new_series = prior is not None and prior["status"] == "succeeded"
        ordinal = 1 if prior is None or new_series else prior["ordinal"] + 1
        series = 1 if prior is None else prior["series"] + int(new_series)
        dispatch_id = f"model-dispatch-{len(self.contexts)}"
        if ordinal > 3:
            with pytest.raises(TaskAttemptLimitError):
                _send(self.runtime, self.state, dispatch_id=dispatch_id)
            self.task_status = "blocked"
            self.exhausted_series.add(series)
            return
        context = _send(self.runtime, self.state, dispatch_id=dispatch_id)
        assert context is not None
        if prior and prior["status"] not in {"succeeded", "deadlettered"}:
            prior["status"] = "superseded"
        self.contexts.append(context)
        self.expected.append({
            "attempt_id": context.attempt_id, "dispatch_id": dispatch_id,
            "ordinal": ordinal, "series": series, "status": "sent", "expires": self.seconds + 30,
        })
        self.task_status = "in_progress"

    @precondition(lambda self: bool(self.contexts))
    @rule()
    @record_action
    def repeat_delivery(self) -> None:
        # Do not reset TaskStore as _send does: replay the original delivery.
        context = self.contexts[-1]
        briefing = self.state / "briefings" / f"{context.dispatch_id}.md"
        with pytest.raises(TaskAttemptDeliveryClaimedError):
            self.runtime._send_transport_task("dev", briefing, "read briefing", context)

    @rule(seconds=st.sampled_from((0, 29, 30, 31)))
    @record_action
    def elapse_and_recover(self, seconds: int) -> None:
        self.seconds += seconds
        if self.expected:
            current = self.expected[-1]
            if current["status"] == "sent" and self.seconds >= current["expires"]:
                current["status"] = "deadlettered" if current["ordinal"] == 3 else "expired"
                self.task_status = "blocked" if current["ordinal"] == 3 else "backlog"
        reconcile_task_attempts(self.runtime)

    @precondition(lambda self: bool(self.contexts))
    @rule(index=st.integers(min_value=0, max_value=8), wrong_lease=st.booleans())
    @record_action
    def result(self, index: int, wrong_lease: bool) -> None:
        index %= len(self.contexts)
        payload = dispatch_attempt_payload(self.contexts[index])
        if wrong_lease:
            payload["lease_id"] = "wrong-lease"
        event = self.runtime.event_writer.append(ZfEvent(
            type="dev.build.done", actor="dev", origin="worker", task_id="TASK-1", payload=payload,
        ))
        current = self.expected[-1]
        accepted = (
            index == len(self.contexts) - 1 and not wrong_lease
            and current["status"] == "sent" and self.seconds < current["expires"]
        )
        reason = validate_task_attempt_result(
            self.runtime, event, task=self.runtime.task_store.get("TASK-1"),
        )
        assert (reason == "") == accepted, reason
        if accepted:
            settle_task_attempt_result(self.runtime, event)
            current["status"] = "succeeded"

    @invariant()
    @record_invariant
    def consistent(self) -> None:
        rows = self.store.rows()
        assert len(rows) == len(self.expected)
        assert sum(len(transport.contexts) for transport in self.transports) == len(self.contexts)
        by_id = {row["attempt_id"]: row for row in rows}
        for expected in self.expected:
            actual = by_id[expected["attempt_id"]]
            for key in ("attempt_id", "dispatch_id", "ordinal", "series", "status"):
                assert actual[key] == expected[key], (key, actual, expected)
        current = self.store.current(run_id="RUN-1", task_id="TASK-1")
        assert (current is None) == (not self.expected)
        if self.expected:
            assert current["attempt_id"] == self.expected[-1]["attempt_id"]
        assert self.runtime.task_store.get("TASK-1").status == self.task_status
        events = self.runtime.event_log.read_all()
        assert len({event.id for event in events}) == len(events)
        for kind in ("task.attempt.started", "task.attempt.retry_scheduled", "task.attempt.deadlettered", "task.attempt.succeeded"):
            counts = Counter(event.payload["attempt_id"] for event in events if event.type == kind and event.payload.get("attempt_id"))
            assert all(count == 1 for count in counts.values()), (kind, counts)
        assert not any(event.type in {"task.done", "run.goal.completed"} for event in events)
        budget_counts = Counter(
            (event.payload.get("run_id"), event.task_id, event.payload.get("dispatch_id"))
            for event in events if event.type == "task.attempt.deadlettered"
            and event.payload.get("reason") == "TaskAttempt budget exhausted before dispatch"
        )
        if any(count > 1 for count in budget_counts.values()):
            raise DuplicateBudgetEvent(f"same exhausted dispatch emitted repeated deadletter events: {budget_counts}")

    def teardown(self) -> None:
        try:
            self.patch.undo()
            self.temporary.cleanup()
        finally:
            super().teardown()


TestTaskAttemptLifecycleModel = TaskAttemptLifecycleModel.TestCase
TestTaskAttemptLifecycleModel.settings = ATTEMPT_SETTINGS


def test_attempt_lifecycle_fixed_timeout_retry_late_result_trace() -> None:
    model = TaskAttemptLifecycleModel()
    try:
        for _ in range(3):
            model.dispatch()
            model.consistent()
            model.repeat_delivery()
            model.elapse_and_recover(seconds=30)
            model.consistent()
            model.reopen()
            model.result(index=0, wrong_lease=False)
            model.consistent()
        model.dispatch()  # Exhausted; never reaches transport.
        model.consistent()
    finally:
        model.teardown()


def test_attempt_lifecycle_fixed_success_resets_series_trace() -> None:
    model = TaskAttemptLifecycleModel()
    try:
        model.dispatch()
        model.result(index=0, wrong_lease=True)
        model.consistent()
        model.result(index=0, wrong_lease=False)
        model.consistent()
        model.dispatch()
        model.consistent()
        model.result(index=0, wrong_lease=False)
        model.consistent()
        assert model.expected[-1]["series"] == 2 and model.expected[-1]["ordinal"] == 1
    finally:
        model.teardown()


@pytest.mark.parametrize("reopen", [False, True])
@pytest.mark.xfail(strict=True, raises=DuplicateBudgetEvent, reason="ZF-TEST-033: exhausted dispatch replay duplicates budget deadletter events")
def test_exhausted_dispatch_replay_emits_one_budget_event(reopen: bool) -> None:
    model = TaskAttemptLifecycleModel()
    try:
        for _ in range(4):
            model.dispatch()
            model.consistent()
        before = model.runtime.event_log.read_all()
        budget = [event for event in before if event.type == "task.attempt.deadlettered"]
        assert len(budget) == 1
        assert budget[0].payload["dispatch_id"] == "model-dispatch-3"
        if reopen:
            model.reopen()
        model.dispatch()  # Exactly the same rejected dispatch, no new delivery.
        assert len(model.contexts) == 3
        assert model.runtime.task_store.get("TASK-1").status == "blocked"
        model.consistent()
    finally:
        model.teardown()
