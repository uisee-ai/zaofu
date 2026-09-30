"""Actual legacy task rework with acknowledged batches and bounded budgets.

TaskAttempt enforcement and Task Pipeline generations are separate protocols.
The initial review state is Given; all later transitions use real run_once.
"""

from collections import Counter
import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from hypothesis import settings, strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from tests.test_reader_fanout_runtime import _RecordingTransport
from tests.testkit.evidence import write_json_atomic
from tests.testkit.model_evidence import (
    ModelEvidence,
    record_action,
    record_invariant,
    replay_steps,
)
from zf.core.config.schema import (
    ContractDConfig,
    ProjectConfig,
    RoleConfig,
    VerificationConfig,
    ZfConfig,
)
from zf.core.events import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.task_attempt_runtime import dispatch_attempt_payload, task_attempt_store


REWORK_SETTINGS = settings(
    max_examples=20,
    stateful_step_count=20,
    deadline=None,
    database=None,
    derandomize=True,
    print_blob=True,
)


class ReworkBudgetReplayed(AssertionError):
    """The same product failure consumed budget again after reconstruction."""


class CheckpointInterrupted(RuntimeError):
    """Test-owned interruption immediately before the consumption checkpoint."""


class ReworkMachine(RuleBasedStateMachine):
    def __init__(self, cap: int = 2) -> None:
        super().__init__()
        self.temporary = TemporaryDirectory(prefix="zf-rework-model-")
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.state.mkdir()
        SessionStore(self.state / "session.yaml").create(project_root=str(self.root))
        SessionStore(self.state / "session.yaml").update(runtime_state="active")
        self.store = TaskStore(self.state / "kanban.json")
        self.store.add(
            Task(
                id="TASK-REWORK",
                title="bounded repair",
                status="review",
                assigned_to="dev",
            )
        )
        self.config = ZfConfig(
            project=ProjectConfig(name="rework-model", state_dir="state"),
            roles=[
                RoleConfig(
                    name="dev",
                    backend="mock",
                    stages=["implement"],
                    publishes=["dev.build.done"],
                    max_rework_attempts=cap,
                ),
                RoleConfig(
                    name="review",
                    backend="mock",
                    stages=["code_review"],
                    publishes=["review.rejected"],
                ),
            ],
            verification=VerificationConfig(
                contract=ContractDConfig(rework_delta_required=True)
            ),
        )
        self.patch = pytest.MonkeyPatch()
        # Replace only this module's clock object, not global time.time or gates.
        self.patch.setattr(
            "zf.core.errors.circuit_breaker.time",
            SimpleNamespace(time=lambda: 1900000000.0),
        )
        self.cap = cap
        self.failures: list[str] = []
        self.dispatch_ids: list[str] = []
        self.completed: dict[str, str] = {}
        self.history: list[ZfEvent] = []
        self.current_epoch: list[ZfEvent] = []
        self.task_status = "review"
        self.active_dispatch = ""
        self.pending_dispatch_clear = False
        self.ack = 0
        self.interrupted: list[int] = []
        self.transport = _RecordingTransport()
        self.evidence = ModelEvidence(
            root=self.root,
            model_name=f"ReworkMachine-cap{cap}",
            source=Path(__file__),
            settings=str(REWORK_SETTINGS),
            reference=lambda: {
                "cap": self.cap,
                "failures": self.failures,
                "dispatch_ids": self.dispatch_ids,
                "completed": self.completed,
                "task_status": self.task_status,
                "active_dispatch": self.active_dispatch,
                "pending_dispatch_clear": self.pending_dispatch_clear,
                "ack": self.ack,
                "interrupted": self.interrupted,
                "transport_roles": [item[0] for item in self.transport.sent],
            },
        )
        self.runtime = self.make_runtime()

    def make_runtime(self) -> Orchestrator:
        return Orchestrator(
            self.state, self.config, self.transport, project_root=self.root
        )

    def events(self, kind: str) -> list[ZfEvent]:
        return [
            event
            for event in self.runtime.event_log.read_all()
            if event.type == kind and event.task_id == "TASK-REWORK"
        ]

    def pump(self, *, drop_checkpoint: bool = False) -> None:
        batch, boundary = self.runtime.event_log.read_from_offset(
            self.runtime._load_offset()
        )
        # A build settles its Attempt immediately but publishes the matching
        # dispatch-clear event for the NEXT immutable watcher batch. Model
        # that pending obligation independently of the observed Task state.
        if self.pending_dispatch_clear:
            self.active_dispatch = ""
            self.pending_dispatch_clear = False
        if drop_checkpoint:

            def interrupt(offset: int) -> None:
                self.interrupted.append(offset)
                raise CheckpointInterrupted(
                    "after effects, before cursor acknowledgment"
                )

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(self.runtime, "_persist_offset", interrupt)
                with pytest.raises(CheckpointInterrupted):
                    self.runtime.run_once(events=batch, consumed_offset=boundary)
            assert self.interrupted[-1] == boundary
        else:
            self.runtime.run_once(events=batch, consumed_offset=boundary)
            self.ack = boundary
        self.current_epoch.extend(batch)

    @precondition(
        lambda self: self.task_status == "review" and len(self.failures) <= self.cap
    )
    @rule(drop_checkpoint=st.just(False))
    @record_action
    def reject(self, drop_checkpoint: bool = False) -> None:
        event = self.runtime.event_writer.append(
            ZfEvent(
                type="review.rejected",
                actor="review",
                origin="worker",
                task_id="TASK-REWORK",
                correlation_id="RUN-REWORK",
                payload={
                    "reason": "deterministic arithmetic mismatch",
                    "dispatch_id": self.dispatch_ids[-1] if self.dispatch_ids else "",
                },
            )
        )
        self.history.append(event)
        self.failures.append(event.id)
        self.task_status = "in_progress"
        self.pump(drop_checkpoint=drop_checkpoint)
        if len(self.failures) <= self.cap:
            dispatch_id = self.store.get("TASK-REWORK").active_dispatch_id
            assert dispatch_id and dispatch_id not in self.dispatch_ids
            self.dispatch_ids.append(dispatch_id)
            self.active_dispatch = dispatch_id

    @precondition(
        lambda self: (
            self.task_status == "in_progress" and len(self.failures) <= self.cap
        )
    )
    @rule()
    @record_action
    def build(self) -> None:
        ref = f"artifacts/repair-{len(self.failures)}.json"
        write_json_atomic(
            self.state / ref, {"round": len(self.failures), "change": "synthetic delta"}
        )
        event = self.runtime.event_writer.append(
            ZfEvent(
                type="dev.build.done",
                actor="dev",
                origin="worker",
                task_id="TASK-REWORK",
                correlation_id="RUN-REWORK",
                payload={
                    **dispatch_attempt_payload(self.transport.sent[-1][3]),
                    "summary": "synthetic repair evidence",
                    "changed_files": [],
                    "artifact_refs": [ref],
                    "evidence_refs": [ref],
                    "residual_risks": ["mock provider"],
                    "next_agent_input": "review the fixture",
                },
            )
        )
        self.history.append(event)
        self.pump()
        self.task_status = "review"
        self.completed[self.dispatch_ids[-1]] = event.id
        self.pending_dispatch_clear = True

    @rule()
    @record_action
    def reopen(self) -> None:
        self.runtime = self.make_runtime()
        self.current_epoch.clear()
        assert self.runtime._load_offset() == self.ack
        self.pump()

    @precondition(lambda self: bool(self.current_epoch))
    @rule(index=st.integers(0, 20))
    @record_action
    def duplicate(self, index: int) -> None:
        # A current-service duplicate. Cross-service old batches have a
        # separate strict detector, not a blanket xfail over the valid model.
        event = self.current_epoch[index % len(self.current_epoch)]
        self.runtime.run_once(events=[event])

    @rule()
    @record_action
    def tick(self) -> None:
        self.pump()

    @invariant()
    @record_invariant
    def consistent(self) -> None:
        task = self.store.get("TASK-REWORK")
        if task.retry_count != len(self.failures):
            raise ReworkBudgetReplayed(
                f"expected {len(self.failures)} failures, stored retry_count={task.retry_count}"
            )
        assert task.status == self.task_status
        count = min(len(self.failures), self.cap)
        assert len(self.dispatch_ids) == count
        assert Counter(item[0] for item in self.transport.sent) == (
            {"dev": count} if count else {}
        )
        assert task.active_dispatch_id == self.active_dispatch
        attempts = task_attempt_store(self.runtime).load()["attempts"]
        assert set(attempts) == {item[3].attempt_id for item in self.transport.sent}
        for _, _, _, context in self.transport.sent:
            attempt = attempts[context.attempt_id]
            for field, expected in dispatch_attempt_payload(context).items():
                if field == "workflow_run_id":
                    field = "run_id"
                assert attempt[field] == expected
            result = self.completed.get(context.dispatch_id)
            assert attempt["status"] == ("succeeded" if result else "sent")
            assert attempt.get("terminal_event_id", "") == (result or "")
        settled = self.events("task.attempt.succeeded")
        assert len(settled) == len(self.completed)
        assert {
            event.payload["dispatch_id"]: event.causation_id for event in settled
        } == self.completed
        requests = self.events("task.rework.requested")
        schedules = self.events("task.attempt.retry_scheduled")
        dispatches = self.events("task.dispatched")
        assert len(requests) == len(schedules) == len(dispatches) == count
        assert [event.causation_id for event in requests] == self.failures[: self.cap]
        assert [event.payload["dispatch_id"] for event in requests] == self.dispatch_ids
        assert [
            event.payload["dispatch_id"] for event in dispatches
        ] == self.dispatch_ids
        for index, (request, schedule) in enumerate(zip(requests, schedules), start=1):
            assert schedule.causation_id == self.failures[index - 1]
            assert schedule.payload["lease_token"] == self.dispatch_ids[index - 1]
            assert schedule.payload["ordinal"] == index + 1
            assert schedule.payload["cap"] == self.cap
            assert request.payload["attempt"] == index
            payload = request.payload
            feedback = self.state / payload["rework_feedback_ref"]
            assert feedback.resolve().is_relative_to(self.state)
            body = feedback.read_bytes()
            assert hashlib.sha256(body).hexdigest() == payload["rework_feedback_digest"]
            parsed = json.loads(body)
            assert parsed["task_id"] == "TASK-REWORK"
            assert parsed["source_event_id"] == self.failures[index - 1]
        capped = self.events("task.rework.capped")
        assert len(capped) == int(len(self.failures) > self.cap)
        if capped:
            assert capped[0].causation_id == self.failures[-1]
            assert capped[0].payload["recovery_owner"] == "run_manager"
            assert capped[0].payload["max_attempts"] == self.cap
            assert capped[0].payload["retry_count"] == self.cap + 1
        assert self.runtime._load_offset() == self.ack
        assert not [
            event
            for event in self.runtime.event_log.read_all()
            if event.type in {"run.goal.completed", "task.done"}
        ]
        assert len(self.store.list_all_with_archive()) == 1
        assert not (self.root / ".zf").exists()

    def teardown(self) -> None:
        self.patch.undo()
        self.temporary.cleanup()


@pytest.mark.parametrize("cap", [1, 2, 3])
def test_generated_acknowledged_rework_sequences(cap: int) -> None:
    run_state_machine_as_test(lambda: ReworkMachine(cap), settings=REWORK_SETTINGS)


def _step(action: str, **arguments: object) -> dict:
    return {"action": action, "arguments": arguments}


@pytest.mark.parametrize("cap", [1, 2, 3])
def test_fixed_rework_exhaustion_survives_checkpointed_reopen(cap: int) -> None:
    model = ReworkMachine(cap)
    try:
        steps = []
        for _ in range(cap):
            steps += [
                _step("reject"),
                _step("duplicate", index=0),
                _step("reopen"),
                _step("build"),
            ]
        steps += [
            _step("reject"),
            _step("duplicate", index=0),
            _step("reopen"),
            _step("tick"),
            _step("reopen"),
        ]
        replay_steps(model, steps, model.consistent)
    finally:
        model.teardown()


@pytest.mark.parametrize("surface", ["old_batch", "checkpoint_interrupted"])
@pytest.mark.xfail(
    strict=True,
    raises=ReworkBudgetReplayed,
    reason="ZF-TEST-038: redelivered rejection spends the product retry budget twice",
)
def test_rework_failure_redelivery_does_not_spend_budget_twice(surface: str) -> None:
    model = ReworkMachine(1)
    try:
        model.reject(drop_checkpoint=surface == "checkpoint_interrupted")
        model.consistent()
        with model.evidence.guard("redeliver_after_reopen", {"surface": surface}):
            if surface == "old_batch":
                model.reopen()
                model.consistent()
                model.runtime.run_once(events=[model.history[0]])
            else:
                assert model.interrupted and model.ack == 0
                model.reopen()  # Actual unacknowledged batch read from the ledger.
            model.consistent()
    finally:
        model.teardown()


def test_missing_delta_reissues_evidence_without_product_budget() -> None:
    model = ReworkMachine()
    try:
        model.reject()
        model.consistent()
        with model.evidence.guard("missing_delta", {}):
            event = model.runtime.event_writer.append(
                ZfEvent(
                    type="dev.build.done",
                    actor="dev",
                    origin="worker",
                    task_id="TASK-REWORK",
                    payload={"dispatch_id": model.dispatch_ids[-1]},
                )
            )
            model.pump()
            failures = model.events("discriminator.failed")
            assert any(
                item.causation_id == event.id
                and "ReworkDeltaD" in item.payload["failed_d"]
                for item in failures
            )
            # Empty delta also violates the completion payload contract.
            # The actual caller may reissue evidence; that is not a second
            # product repair and must not consume the product retry budget.
            task = model.store.get("TASK-REWORK")
            assert task.status == "in_progress" and task.retry_count == 1
            assert len(model.events("task.evidence.reissue.requested")) == 1
            assert len(model.events("task.rework.requested")) == 1
            assert (
                len(model.events("task.dispatched")) == len(model.transport.sent) == 2
            )
            assert not model.events("task.rework.capped")
            ref = "artifacts/evidence-reissued.json"
            write_json_atomic(model.state / ref, {"reissued": True})
            model.runtime.event_writer.append(
                ZfEvent(
                    type="dev.build.done",
                    actor="dev",
                    origin="worker",
                    task_id="TASK-REWORK",
                    payload={
                        **dispatch_attempt_payload(model.transport.sent[-1][3]),
                        "summary": "reissued evidence",
                        "changed_files": [],
                        "evidence_refs": [ref],
                    },
                )
            )
            model.pump()
            task = model.store.get("TASK-REWORK")
            assert task.status == "review" and task.retry_count == 1
            assert len(model.transport.sent) == 2
            assert not [
                item
                for item in model.runtime.event_log.read_all()
                if item.type == "run.goal.completed"
            ]
    finally:
        model.teardown()


def test_rework_oracle_detects_extra_transport_and_keeps_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "capture"
    monkeypatch.setenv("ZF_TESTKIT_MODEL_EVIDENCE", str(output))
    model = ReworkMachine()
    root = model.root
    try:
        model.reject()
        model.consistent()
        model.transport.sent.append(model.transport.sent[0])
        with pytest.raises(AssertionError) as caught:
            model.consistent()
        assert any("TestKit model evidence:" in note for note in caught.value.__notes__)
    finally:
        model.teardown()
    assert not root.exists()
    snapshots = list(output.glob("*/latest.json"))
    assert len(snapshots) == 1
    snapshot = json.loads(snapshots[0].read_text())
    assert snapshot["complete"] and snapshot["failed_check"] == "consistent"
    assert len(snapshot["reference"]["failures"]) == 1
    files = {row["path"]: row for row in snapshot["files"]}
    assert {
        "state/kanban.json",
        "state/events.jsonl",
        "state/session.yaml",
    } <= files.keys()
    for row in files.values():
        body = base64.b64decode(row["base64"], validate=True)
        assert (
            len(body) == row["bytes"]
            and hashlib.sha256(body).hexdigest() == row["sha256"]
        )
