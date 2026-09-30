"""Generated sequences against the real Kernel and proposal execution gate.

The models contain only expected observable state, not copies of production
reducers. Reopening means fresh service objects over the same local files;
hard process crashes remain covered by the separate S4 subprocess matrix.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from tests.conformance.test_zaofu_testkit_s4 import _orchestrator_context
from tests.testkit.oracles import assert_forbidden_events, assert_no_duplicate_event_ids
from tests.testkit.model_evidence import ModelEvidence, record_action, record_invariant
from zf.core.events import EventLog, EventWriter, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService


MODEL_SETTINGS = settings(
    max_examples=25,
    stateful_step_count=20,
    derandomize=True,
    database=None,
    deadline=None,
    print_blob=True,
)


class OrchestratorDependencyMachine(RuleBasedStateMachine):
    """Dependency release, repeated wakeup and reload preserve one dispatch."""

    def __init__(self) -> None:
        super().__init__()
        self.temporary = TemporaryDirectory(prefix="zf-hypothesis-orchestrator-")
        self.project, _, self.store, self.log, self.make_runtime = (
            _orchestrator_context(Path(self.temporary.name))
        )
        self.store.add(Task(id="DEPENDENCY", title="dependency", status="in_progress"))
        self.store.add(Task(
            id=self.project.task_id,
            title="dependent task",
            status="backlog",
            blocked_by=["DEPENDENCY"],
        ))
        self.runtime = self.make_runtime()
        self.wakeup = self.runtime.event_writer.append(ZfEvent(
            id="dependency-model-wakeup",
            type="task.created",
            task_id=self.project.task_id,
            correlation_id=self.project.run_id,
        ))
        self.dependency_done = False
        self.dispatched = False
        self.dispatch_id = ""
        self.evidence = ModelEvidence(
            root=Path(self.temporary.name), model_name=type(self).__qualname__,
            source=Path(__file__), settings=str(MODEL_SETTINGS),
            reference=lambda: {"dependency_done": self.dependency_done, "dispatched": self.dispatched},
        )

    @precondition(lambda self: not self.dependency_done)
    @rule()
    @record_action
    def release_dependency(self) -> None:
        # This models an upstream gate already satisfied, using the existing
        # Kernel transition seam. It is not evidence for judge authorization.
        assert self.runtime._move_task("DEPENDENCY", "done", trigger_event="judge.passed")
        self.dependency_done = True

    @rule(repeat_event=st.booleans())
    @record_action
    def wake(self, repeat_event: bool) -> None:
        decisions = self.runtime.run_once(events=[self.wakeup] if repeat_event else None)
        new_dispatches = [decision for decision in decisions if decision.action == "dispatch"]
        expected_new = int(self.dependency_done and not self.dispatched)
        assert len(new_dispatches) == expected_new, decisions
        if expected_new:
            self.dispatched = True
            task = self.store.get(self.project.task_id)
            assert task is not None and task.active_dispatch_id
            self.dispatch_id = task.active_dispatch_id

    @rule()
    @record_action
    def reopen(self) -> None:
        self.runtime = self.make_runtime()
        self.store = TaskStore(self.project.state_dir / "kanban.json")
        self.log = EventLog(self.project.events_path)

    @invariant()
    @record_invariant
    def truth_matches_model(self) -> None:
        task = self.store.get(self.project.task_id)
        assert task is not None
        assert task.status == ("in_progress" if self.dispatched else "backlog")
        events = self.log.read_all()
        dispatched = [event for event in events if event.type == "task.dispatched"]
        assert len(dispatched) == int(self.dispatched), [event.to_json() for event in dispatched]
        if self.dispatched:
            assert self.dependency_done
            assert task.active_dispatch_id == self.dispatch_id
            assert dispatched[0].task_id == self.project.task_id
            assert dispatched[0].payload["dispatch_id"] == self.dispatch_id
        assert {row.id for row in self.store.list_all_with_archive()} == {
            "DEPENDENCY", self.project.task_id,
        }
        assert_forbidden_events(self.log, ["run.goal.completed", "task.done"])
        assert_no_duplicate_event_ids(self.log)

    def teardown(self) -> None:
        try:
            self.temporary.cleanup()
        finally:
            super().teardown()


class ControlledProposalMachine(RuleBasedStateMachine):
    """Exact proposal identity gates actual TaskStore mutations across reloads.

The service is a trusted caller boundary. Unknown/mismatched/superseded
proposals test its proposal gate, not HTTP token authorization. Task updates
and resolution events count the real effects: no idempotent spy hides repeats.
"""

    def __init__(self) -> None:
        super().__init__()
        self.temporary = TemporaryDirectory(prefix="zf-hypothesis-action-")
        self.root = Path(self.temporary.name)
        self.state_dir = self.root / "state"
        self.state_dir.mkdir()
        self.store = TaskStore(self.state_dir / "kanban.json")
        self.store.add(Task(id="MODEL-TASK", title="proposal target", priority=3))
        self.proposals: list[tuple[str, int, int]] = []
        self.latest: dict[str, int] = {}
        self.applied: set[str] = set()
        self.expected_priority = 3
        self.evidence = ModelEvidence(
            root=self.root, model_name=type(self).__qualname__, source=Path(__file__),
            settings=str(MODEL_SETTINGS), reference=lambda: {
                "proposals": self.proposals, "latest": self.latest,
                "applied": sorted(self.applied), "expected_priority": self.expected_priority,
            },
        )
        self.reopen()
        self.evidence.steps.clear()  # Initial construction is not a replay action.

    @rule()
    @record_action
    def reopen(self) -> None:
        self.log = EventLog(self.state_dir / "events.jsonl")
        self.writer = EventWriter(self.log)
        self.store = TaskStore(self.state_dir / "kanban.json")
        self.service = ControlledActionService(
            self.state_dir, self.writer, project_root=self.root,
            actor="operator", source="web", surface="web",
        )

    def _record_proposal(self, family: str, revision: int, priority: int) -> None:
        index = len(self.proposals)
        self.writer.append(ZfEvent(
            id=f"model-proposal-{index}",
            type="kanban.agent.action.proposed",
            actor="web",
            task_id="MODEL-TASK",
            correlation_id="model-run",
            payload={"proposal": {
                "proposal_id": family,
                "revision": revision,
                "action": "update-task",
                "valid": True,
                "payload": {"task_id": "MODEL-TASK", "priority": priority},
            }},
        ))
        self.proposals.append((family, revision, priority))
        self.latest[family] = revision

    @rule(priority=st.integers(min_value=0, max_value=3))
    @record_action
    def propose(self, priority: int) -> None:
        self._record_proposal(f"family-{len(self.proposals)}", 1, priority)

    @precondition(lambda self: bool(set(self.latest) - self.applied))
    @rule()
    @record_action
    def supersede_pending(self) -> None:
        family = next(family for family in self.latest if family not in self.applied)
        self._record_proposal(family, self.latest[family] + 1, 2)

    def _execute(self, proposal_id: str, priority: int) -> dict:
        payload = {
            "task_id": "MODEL-TASK", "priority": priority,
            "proposal_event_id": proposal_id,
        }
        requested = self.writer.emit(
            "web.action.requested", actor="operator", task_id="MODEL-TASK",
            correlation_id="model-run", payload={"request": payload},
        )
        return self.service.execute(
            action="update-task", requested_action="update-task",
            payload=payload, requested=requested,
        )

    @precondition(lambda self: bool(self.proposals))
    @rule(index=st.integers(min_value=0, max_value=20), tamper=st.booleans())
    @record_action
    def apply(self, index: int, tamper: bool) -> None:
        index %= len(self.proposals)
        family, revision, priority = self.proposals[index]
        result = self._execute(f"model-proposal-{index}", (priority + 1) % 4 if tamper else priority)
        if revision != self.latest[family]:
            assert result["ok"] is False and result["status"] == "proposal_superseded", result
        elif tamper:
            assert result["ok"] is False and result["status"] == "proposal_payload_mismatch", result
        elif family in self.applied:
            assert result["ok"] is True and result["status"] == "already_resolved", result
        else:
            assert result["ok"] is True and result["status"] == "completed", result
            self.applied.add(family)
            self.expected_priority = priority

    @rule()
    @record_action
    def unknown_proposal(self) -> None:
        result = self._execute("not-a-proposal", 0)
        assert result["ok"] is False and result["status"] == "proposal_not_found", result

    @invariant()
    @record_invariant
    def truth_matches_model(self) -> None:
        task = self.store.get("MODEL-TASK")
        assert task is not None and task.priority == self.expected_priority
        assert task.status == "backlog"
        events = self.log.read_all()
        updates = [event for event in events if event.type == "task.updated"]
        resolutions = [event for event in events if event.type == "kanban.agent.proposal.resolved"]
        assert len(updates) == len(self.applied)
        assert Counter(event.payload["proposal_id"] for event in resolutions) == Counter(self.applied)
        requests = {event.id for event in events if event.type == "web.action.requested"}
        assert all(event.causation_id in requests for event in updates)
        assert all(event.correlation_id == "model-run" for event in updates)
        assert len(self.store.list_all()) == 1
        assert_forbidden_events(self.log, ["task.created", "task.done", "run.goal.completed"])
        assert_no_duplicate_event_ids(self.log)

    def teardown(self) -> None:
        try:
            self.temporary.cleanup()
        finally:
            super().teardown()


TestOrchestratorDependencyModel = OrchestratorDependencyMachine.TestCase
TestOrchestratorDependencyModel.settings = MODEL_SETTINGS
TestControlledProposalModel = ControlledProposalMachine.TestCase
TestControlledProposalModel.settings = MODEL_SETTINGS


@pytest.mark.parametrize("release_first", [False, True])
def test_orchestrator_dependency_model_fixed_replay(release_first: bool) -> None:
    """Keep a readable trace even if generated exploration chooses others."""
    model = OrchestratorDependencyMachine()
    try:
        if not release_first:
            model.wake(repeat_event=True)
            model.truth_matches_model()
            model.reopen()
        model.release_dependency()
        model.truth_matches_model()
        for _ in range(3):
            model.wake(repeat_event=True)
            model.truth_matches_model()
            model.reopen()
            model.truth_matches_model()
    finally:
        model.teardown()


def test_controlled_proposal_model_fixed_replay() -> None:
    model = ControlledProposalMachine()
    try:
        model.unknown_proposal()
        model.truth_matches_model()
        model.propose(priority=1)
        for tamper in (True, False, False, True):
            model.apply(index=0, tamper=tamper)
            model.truth_matches_model()
            model.reopen()
            model.truth_matches_model()
        model.propose(priority=0)
        model.supersede_pending()
        for index in (1, 2, 1, 2):
            model.apply(index=index, tamper=False)
            model.truth_matches_model()
    finally:
        model.teardown()


@pytest.mark.parametrize("surface", ["orchestrator", "controlled_action"])
def test_state_model_oracle_detects_duplicate_business_effect(
    surface: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An extra business event must fail even when current Store looks right."""
    # These are deliberate oracle faults, not genuine generated failures.
    monkeypatch.setenv("ZF_TESTKIT_MODEL_EVIDENCE", str(tmp_path / "oracle-probe"))
    model = (
        OrchestratorDependencyMachine() if surface == "orchestrator"
        else ControlledProposalMachine()
    )
    try:
        if isinstance(model, OrchestratorDependencyMachine):
            model.release_dependency()
            model.wake(repeat_event=False)
            event_type = "task.dispatched"
        else:
            model.propose(priority=1)
            model.apply(index=0, tamper=False)
            event_type = "task.updated"
        model.truth_matches_model()
        original = next(event for event in model.log.read_all() if event.type == event_type)
        model.log.append(ZfEvent(
            type=original.type, task_id=original.task_id,
            correlation_id=original.correlation_id, causation_id=original.causation_id,
            payload=dict(original.payload),
        ))
        with pytest.raises(AssertionError):
            model.truth_matches_model()
    finally:
        model.teardown()
    snapshots = list((tmp_path / "oracle-probe").glob("*/*.json"))
    assert len(snapshots) == 2
    for path in snapshots:
        captured = json.loads(path.read_text())
        assert captured["complete"] is True and captured["steps"]
        assert not Path(captured["original_root"]).exists()
        assert {Path(entry["path"]).name for entry in captured["files"]} >= {"events.jsonl", "kanban.json"}
