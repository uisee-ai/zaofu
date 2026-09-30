"""Proposal execution, result-loss and lifecycle interleavings at real seams.

BaseException faults unwind this service's lock; S4 separately tests os._exit.
The observer counts every successful local Store update and never deduplicates.
These trusted service callers do not prove HTTP token authorization or v0
operator-intent approval, which are separate entrypoints.
"""

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings, strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from tests.conformance.test_zaofu_testkit_s4_action_crash import DuplicateActionEffect
from tests.testkit.evidence import write_json_atomic
from tests.testkit.model_evidence import (
    ModelEvidence,
    record_action,
    record_invariant,
    replay_steps,
)
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.kanban_proposals import pending_kanban_proposals


MODEL_SETTINGS = settings(
    max_examples=20,
    stateful_step_count=20,
    deadline=None,
    database=None,
    derandomize=True,
    print_blob=True,
)
PREFIX_SETTINGS = settings(
    max_examples=12, deadline=None, database=None, derandomize=True, print_blob=True
)
SURFACES = {
    "operator": ("operator.action.proposed", "operator.action.resolved"),
    "legacy": ("kanban.agent.action.proposed", "kanban.agent.proposal.resolved"),
}
SAFE_LOSS = ("normal", "before_store", "after_resolution", "client_reply")
COMMIT_LOSS = ("after_store", "before_resolution")
TASK = "ACTION-LOSS-TASK"
RUN = "ACTION-LOSS-RUN"


class ActionInterrupted(BaseException):
    """Known test-only interruption; bypasses handler's Exception catch."""


@dataclass
class Proposal:
    event_id: str
    family: str
    revision: int
    priority: int
    expires: int
    digest: str
    effect_request: str = ""
    update_request: str = ""


class ActionLossMachine(RuleBasedStateMachine):
    def __init__(self, surface: str = "operator") -> None:
        super().__init__()
        self.temporary = TemporaryDirectory(prefix="zf-action-loss-model-")
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.store = TaskStore(self.state / "kanban.json")
        self.store.add(Task(id=TASK, title="loss target", priority=3))
        self.surface = surface
        self.proposed_type, self.resolved_type = SURFACES[surface]
        self.seconds = 0
        self.proposals: list[Proposal] = []
        self.latest: dict[str, int] = {}
        self.superseded: set[str] = set()
        self.resolutions: dict[str, dict] = {}
        self.expected_effects: list[dict] = []
        self.expected_priority = 3
        self.attempts: dict[str, dict] = {}
        self.requests: dict[str, str] = {}
        self.rejections: dict[str, str] = {}
        self.faults: list[dict] = []
        self.patch = pytest.MonkeyPatch()
        owner = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(
                    seconds=owner.seconds
                )

        self.patch.setattr("zf.runtime.kanban_proposals.datetime", Clock)
        self.evidence = ModelEvidence(
            root=self.root,
            model_name=f"ActionLossMachine-{surface}",
            source=Path(__file__),
            settings=f"model={MODEL_SETTINGS}; prefix={PREFIX_SETTINGS}",
            reference=lambda: {
                "seconds": self.seconds,
                "proposals": [asdict(p) for p in self.proposals],
                "latest": self.latest,
                "superseded": sorted(self.superseded),
                "resolutions": self.resolutions,
                "expected_effects": self.expected_effects,
                "expected_priority": self.expected_priority,
                "attempts": self.attempts,
                "requests": self.requests,
                "rejections": self.rejections,
                "faults": self.faults,
            },
        )
        self._open()

    def _open(self) -> None:
        self.log = EventLog(self.state / "events.jsonl")
        self.writer = EventWriter(self.log)
        self.service = ControlledActionService(
            self.state,
            self.writer,
            project_root=self.root,
            actor="operator",
            source="web",
            surface="web",
        )

    @rule()
    @record_action
    def reopen(self) -> None:
        self._open()  # No seed, observer reset, ledger truncation or lock deletion.

    def _propose(self, priority: int, family: str = "", supersedes: str = "") -> None:
        index = len(self.proposals)
        family = family or f"family-{index}"
        revision = self.latest.get(family, 0) + 1
        payload = {"task_id": TASK, "priority": priority}
        encoded = json.dumps(
            {"action": "update-task", "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        proposal = Proposal(
            f"proposal-{index}", family, revision, priority, self.seconds + 10, digest
        )
        expires_at = datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(
            seconds=proposal.expires
        )
        self.writer.append(
            ZfEvent(
                id=proposal.event_id,
                type=self.proposed_type,
                actor="kanban-agent",
                origin="worker",
                task_id=TASK,
                correlation_id=RUN,
                payload={
                    "conversation_id": "model-conversation",
                    "project_id": "model-project",
                    "proposal": {
                        "proposal_id": family,
                        "revision": revision,
                        "proposal_digest": digest,
                        "expires_at": expires_at.isoformat(),
                        "supersedes": supersedes,
                        "action": "update-task",
                        "valid": True,
                        "payload": payload,
                    },
                },
            )
        )
        self.proposals.append(proposal)
        self.latest[family] = revision
        if supersedes:
            self.superseded.add(supersedes)

    @precondition(lambda self: len(self.proposals) < 6)
    @rule(priority=st.integers(0, 3))
    @record_action
    def propose(self, priority: int) -> None:
        self._propose(priority)

    @precondition(lambda self: bool(self.proposals) and len(self.proposals) < 6)
    @rule(index=st.integers(0, 20), new_family=st.booleans())
    @record_action
    def replace(self, index: int, new_family: bool) -> None:
        old = self.proposals[index % len(self.proposals)]
        self._propose(
            (old.priority + 1) % 4,
            family="" if new_family else old.family,
            supersedes=old.family if new_family else "",
        )

    @rule(seconds=st.sampled_from([0, 9, 10, 11]))
    @record_action
    def elapse(self, seconds: int) -> None:
        self.seconds += seconds

    def _expected_status(
        self, proposal: Proposal, *, tamper: bool, dismiss: bool
    ) -> str:
        if self.seconds >= proposal.expires:
            return "proposal_expired"
        if (
            proposal.family in self.superseded
            or proposal.revision < self.latest[proposal.family]
        ):
            return "proposal_superseded"
        if tamper and not dismiss:
            return "proposal_payload_mismatch"
        if proposal.family in self.resolutions:
            return "already_resolved"
        return "ready"

    def _execute(
        self,
        proposal: Proposal,
        *,
        mode: str = "normal",
        tamper: bool = False,
        dismiss: bool = False,
    ) -> None:
        expected = self._expected_status(proposal, tamper=tamper, dismiss=dismiss)
        action = "kanban-proposal-dismiss" if dismiss else "update-task"
        payload = {
            "task_id": TASK,
            "priority": (proposal.priority + 1) % 4 if tamper else proposal.priority,
            "proposal_event_id": proposal.event_id,
        }
        requested = self.writer.emit(
            "web.action.requested",
            actor="operator",
            task_id=TASK,
            correlation_id=RUN,
            payload={"request": payload},
        )
        self.requests[requested.id] = proposal.event_id
        armed = mode if expected == "ready" and not dismiss else "normal"

        def hit(point: str) -> None:
            if armed == point:
                self.faults.append(
                    {
                        "point": point,
                        "request": requested.id,
                        "proposal": proposal.event_id,
                    }
                )
                write_json_atomic(self.root / "faults.json", {"hits": self.faults})
                raise ActionInterrupted(point)

        original_update = TaskStore.update
        original_emit = self.writer.emit

        def observe(store: TaskStore, task_id: str, **updates):
            target = store.path == self.state / "kanban.json" and task_id == TASK
            if target:
                hit("before_store")
            result = original_update(store, task_id, **updates)
            if target and result is not None:
                with (self.root / "observed-updates.jsonl").open(
                    "a", encoding="utf-8"
                ) as stream:
                    stream.write(
                        json.dumps(
                            {
                                "proposal": proposal.event_id,
                                "request": requested.id,
                                "updates": updates,
                            }
                        )
                        + "\n"
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
            return result

        def emit(event_type: str, **kwargs):
            target = kwargs.get("causation_id") == requested.id
            if target and event_type == "task.updated":
                hit("after_store")
            if target and event_type == self.resolved_type:
                hit("before_resolution")
            event = original_emit(event_type, **kwargs)
            if target and event_type == self.resolved_type:
                hit("after_resolution")
            return event

        def invoke():
            result = self.service.execute(
                action=action,
                requested_action=action,
                payload=payload,
                requested=requested,
            )
            hit(
                "client_reply"
            )  # Execution returned; the simulated client loses the reply.
            return result

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(TaskStore, "update", observe)
            patch.setattr(self.writer, "emit", emit)
            if armed != "normal":
                with pytest.raises(ActionInterrupted, match=f"^{armed}$"):
                    invoke()
                assert self.faults[-1] == {
                    "point": armed,
                    "request": requested.id,
                    "proposal": proposal.event_id,
                }
            else:
                result = invoke()
                wanted = "completed" if expected == "ready" else expected
                assert result["status"] == wanted, result
                assert result["ok"] == (expected in {"ready", "already_resolved"}), (
                    result
                )
                if expected == "already_resolved":
                    resolution = next(
                        e for e in self.log.read_all() if e.id == result["event_id"]
                    )
                    assert resolution.type == self.resolved_type
                    assert resolution.payload["proposal_id"] == proposal.family
                    assert result["proposal_event_id"] == proposal.event_id
                    assert result["proposal_id"] == proposal.family
                    assert result["revision"] == proposal.revision
                    assert result["proposal_digest"] == proposal.digest
                    assert result["task_id"] == TASK
                elif expected == "ready":
                    event = next(
                        e for e in self.log.read_all() if e.id == result["event_id"]
                    )
                    assert event.causation_id == requested.id and event.task_id == TASK
                    assert event.type == (
                        self.resolved_type if dismiss else "task.updated"
                    )
                    assert result["action_result"]["status"] == "completed"
                    assert result["action_result"]["event_id"] == event.id

        if expected != "ready":
            if expected != "already_resolved":
                self.rejections[requested.id] = expected
            return
        completed = armed not in {"before_store", "after_store"}
        self.attempts[requested.id] = {"action": action, "completed": completed}
        if not dismiss and armed != "before_store":
            # An uncertain replay may fill missing evidence/resolution, but
            # may not commit the same operation again or undo a later update.
            if not proposal.effect_request:
                proposal.effect_request = requested.id
                self.expected_effects.append(
                    {
                        "proposal": proposal.event_id,
                        "request": requested.id,
                        "updates": {"priority": proposal.priority},
                    }
                )
                self.expected_priority = proposal.priority
            if armed != "after_store" and not proposal.update_request:
                proposal.update_request = requested.id
        if dismiss or armed in {"normal", "after_resolution", "client_reply"}:
            self.resolutions[proposal.family] = {
                "proposal": proposal.event_id,
                "request": requested.id,
                "resolution": "dismissed" if dismiss else "executed",
            }

    @precondition(lambda self: bool(self.proposals))
    @rule(
        index=st.integers(0, 20), mode=st.sampled_from(SAFE_LOSS), tamper=st.booleans()
    )
    @record_action
    def approve(self, index: int, mode: str = "normal", tamper: bool = False) -> None:
        self._execute(
            self.proposals[index % len(self.proposals)], mode=mode, tamper=tamper
        )

    @precondition(lambda self: bool(self.proposals))
    @rule(index=st.integers(0, 20))
    @record_action
    def dismiss(self, index: int) -> None:
        self._execute(self.proposals[index % len(self.proposals)], dismiss=True)

    @record_action
    def commit_loss(self, index: int, point: str) -> None:
        assert point in COMMIT_LOSS
        self._execute(self.proposals[index], mode=point)

    @invariant()
    @record_invariant
    def consistent(self) -> None:
        observer = self.root / "observed-updates.jsonl"
        effects = (
            [json.loads(row) for row in observer.read_text().splitlines()]
            if observer.exists()
            else []
        )
        counts = Counter(row["proposal"] for row in effects)
        if any(count > 1 for count in counts.values()):
            raise DuplicateActionEffect(
                f"one proposal version committed again: {dict(counts)}"
            )
        assert effects == self.expected_effects
        task = self.store.get(TASK)
        assert task.priority == self.expected_priority and task.status == "backlog"
        assert len(self.store.list_all_with_archive()) == 1
        events = self.log.read_all()
        by_id = {event.id: event for event in events}
        assert len(by_id) == len(events)
        assert not any(
            event.type in {"task.done", "task.created", "run.goal.completed"}
            for event in events
        )
        assert not (self.root / ".zf").exists()

        updates = [event for event in events if event.type == "task.updated"]
        expected_updates = {
            p.update_request: p.priority for p in self.proposals if p.update_request
        }
        if len(updates) > len(expected_updates):
            raise DuplicateActionEffect(
                f"duplicate business update events: {len(updates)} > {len(expected_updates)}"
            )
        assert {
            event.causation_id: event.payload["updates"]["priority"]
            for event in updates
        } == expected_updates
        assert len(updates) == len(expected_updates)
        resolutions = [
            event for event in events if event.type in {v[1] for v in SURFACES.values()}
        ]
        assert len(resolutions) == len(self.resolutions)
        for event in resolutions:
            expected = self.resolutions[event.payload["proposal_id"]]
            proposal = next(
                p for p in self.proposals if p.event_id == expected["proposal"]
            )
            assert event.type == self.resolved_type
            assert event.causation_id == expected["request"]
            assert event.payload["proposal_event_id"] == proposal.event_id
            assert event.payload["revision"] == proposal.revision
            assert event.payload["proposal_digest"] == proposal.digest
            assert event.payload["resolution"] == expected["resolution"]
            assert event.payload["conversation_id"] == "model-conversation"
            assert event.payload["project_id"] == "model-project"
        for event in [*updates, *resolutions]:
            assert event.causation_id in self.requests
            assert event.correlation_id == RUN and event.task_id == TASK

        starts = [e for e in events if e.type == "runtime.action.attempt.started"]
        assert len(starts) == len(self.attempts)
        assert {e.causation_id for e in starts} == self.attempts.keys()
        for event in starts:
            assert (
                event.payload["action"] == self.attempts[event.causation_id]["action"]
            )
            assert (
                event.payload["payload"]["proposal_event_id"]
                == self.requests[event.causation_id]
            )
        finished = [e for e in events if e.type == "runtime.action.attempt.completed"]
        expected_finished = {
            request for request, item in self.attempts.items() if item["completed"]
        }
        assert len(finished) == len(expected_finished)
        assert {
            by_id[e.causation_id].causation_id for e in finished
        } == expected_finished
        for event in finished:
            start = by_id[event.causation_id]
            result = by_id[event.payload["result_event_id"]]
            assert event.payload["attempt_id"] == start.payload["attempt_id"]
            assert result.type in {"task.updated", self.resolved_type}
            assert result.causation_id == start.causation_id
            assert event.task_id == TASK and event.correlation_id == RUN
        assert not [e for e in events if e.type == "runtime.action.attempt.failed"]
        for kind in ("runtime.action.completed", "web.action.completed"):
            completed = [e for e in events if e.type == kind]
            assert len(completed) == len(expected_finished)
            assert {
                by_id[e.causation_id].causation_id for e in completed
            } == expected_finished
        failed = [e for e in events if e.type == "runtime.action.failed"]
        assert {e.causation_id: e.payload["reason"] for e in failed} == self.rejections
        assert len(failed) == len(self.rejections)
        pending = pending_kanban_proposals(events)
        expected_pending = {
            p.event_id
            for p in self.proposals
            if p.revision == self.latest[p.family]
            and p.family not in self.resolutions
            and p.family not in self.superseded
            and self.seconds < p.expires
        }
        assert {p["proposal_event_id"] for p in pending} == expected_pending

    def teardown(self) -> None:
        try:
            self.patch.undo()
        finally:
            self.temporary.cleanup()


def _step(action: str, **arguments) -> dict:
    return {"action": action, "arguments": arguments}


@pytest.mark.parametrize("surface", SURFACES)
def test_generated_proposal_loss_and_lifecycle(surface: str) -> None:
    run_state_machine_as_test(
        lambda: ActionLossMachine(surface), settings=MODEL_SETTINGS
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_fixed_reply_loss_expiry_dismiss_and_revision(surface: str) -> None:
    model = ActionLossMachine(surface)
    try:
        replay_steps(
            model,
            [
                _step("propose", priority=1),
                _step("approve", index=0, mode="before_store"),
                _step("reopen"),
                _step("approve", index=0, mode="after_resolution"),
                _step("reopen"),
                _step("approve", index=0),
                _step("approve", index=0, tamper=True),
                _step("elapse", seconds=10),
                _step("approve", index=0),
                _step("propose", priority=0),
                _step("approve", index=1, mode="client_reply"),
                _step("reopen"),
                _step("approve", index=1),
                _step("replace", index=1, new_family=False),
                _step("approve", index=1),
                _step(
                    "approve", index=2
                ),  # Completed family blocks the new revision too.
                _step("propose", priority=2),
                _step("dismiss", index=3),
                _step("reopen"),
                _step("approve", index=3),
            ],
            model.consistent,
        )
    finally:
        model.teardown()


PREFIX = st.lists(
    st.sampled_from(["reopen", "tamper", "before_store"]), min_size=0, max_size=6
)


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("point", COMMIT_LOSS)
@pytest.mark.xfail(
    strict=True,
    raises=DuplicateActionEffect,
    reason="ZF-TEST-032: committed update repeats when proposal resolution was lost",
)
@PREFIX_SETTINGS
@given(prefix=PREFIX)
def test_missing_resolution_replay_never_recommits(
    surface: str, point: str, prefix: list[str]
) -> None:
    model = ActionLossMachine(surface)
    try:
        steps = [_step("propose", priority=1)]
        for action in prefix:
            steps.append(
                _step("reopen")
                if action == "reopen"
                else _step("approve", index=0, tamper=True)
                if action == "tamper"
                else _step("approve", index=0, mode="before_store")
            )
        steps += [
            _step("commit_loss", index=0, point=point),
            _step("reopen"),
            _step("approve", index=0),
            _step("reopen"),
            _step("approve", index=0),
        ]
        replay_steps(model, steps, model.consistent)
    finally:
        model.teardown()


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("point", COMMIT_LOSS)
@pytest.mark.parametrize("interleave", ["expiry", "dismiss", "revision", "supersedes"])
def test_partial_commit_lifecycle_does_not_undo_history_or_reapply_old(
    surface: str,
    point: str,
    interleave: str,
) -> None:
    model = ActionLossMachine(surface)
    try:
        steps = [
            _step("propose", priority=1),
            _step("commit_loss", index=0, point=point),
            _step("reopen"),
        ]
        if interleave == "expiry":
            steps += [_step("elapse", seconds=10)]
        elif interleave == "dismiss":
            steps += [_step("dismiss", index=0)]
        else:
            steps += [
                _step("replace", index=0, new_family=interleave == "supersedes"),
                _step("approve", index=1),
            ]
        steps += [_step("approve", index=0), _step("reopen"), _step("approve", index=0)]
        replay_steps(model, steps, model.consistent)
        # No rollback: one historical effect, plus one independently approved
        # replacement when present. This does not prove missing receipts heal.
        assert len(model.expected_effects) == (
            2 if interleave in {"revision", "supersedes"} else 1
        )
    finally:
        model.teardown()


@pytest.mark.parametrize("fault", ["store_without_event", "duplicate_event"])
def test_action_loss_oracle_retains_actual_update_and_event_faults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    output = tmp_path / "capture"
    monkeypatch.setenv("ZF_TESTKIT_MODEL_EVIDENCE", str(output))
    model = ActionLossMachine()
    root = model.root
    try:
        model.propose(priority=1)
        if fault == "store_without_event":
            original = model.service.execute

            def double_update(**kwargs):
                result = original(**kwargs)
                # Real extra successful Store call, within the active observer;
                # no extra task.updated or resolution hides behind equal values.
                TaskStore(model.state / "kanban.json").update(TASK, priority=1)
                return result

            monkeypatch.setattr(model.service, "execute", double_update)
        model.approve(index=0)
        if fault == "duplicate_event":
            original = next(e for e in model.log.read_all() if e.type == "task.updated")
            model.writer.append(
                ZfEvent(
                    type=original.type,
                    task_id=original.task_id,
                    causation_id=original.causation_id,
                    correlation_id=RUN,
                    payload=dict(original.payload),
                )
            )
        with pytest.raises(DuplicateActionEffect) as caught:
            model.consistent()
        assert any("TestKit model evidence:" in note for note in caught.value.__notes__)
    finally:
        model.teardown()
    assert not root.exists()
    snapshots = list(output.glob("*/latest.json"))
    assert len(snapshots) == 1
    d = json.loads(snapshots[0].read_text())
    assert d["complete"] and d["failed_check"] == "consistent"
    assert {"state/kanban.json", "state/events.jsonl", "observed-updates.jsonl"} <= {
        f["path"] for f in d["files"]
    }
    for row in d["files"]:
        body = base64.b64decode(row["base64"], validate=True)
        assert (
            len(body) == row["bytes"]
            and hashlib.sha256(body).hexdigest() == row["sha256"]
        )
