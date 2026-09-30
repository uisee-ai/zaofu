"""Bounded reader fanout caller model; not Task Pipeline or writer recovery.

Running runs are Given events, not evidence of workflow admission. Providers
only record dispatches. Reopen constructs services, never kills a process.
"""

from collections import Counter
import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

import pytest
from hypothesis import given, settings, strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from tests.test_reader_fanout_runtime import _config, _RecordingTransport
from tests.testkit.model_evidence import (
    ModelEvidence,
    record_action,
    record_invariant,
    replay_steps,
)
from zf.core.config.schema import RoleConfig
from zf.core.events import ZfEvent
from zf.core.state.session import SessionStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.fanout import FanoutManifestProjector
from zf.runtime.orchestrator import Orchestrator


FANOUT_SETTINGS = settings(
    max_examples=20,
    stateful_step_count=18,
    deadline=None,
    database=None,
    derandomize=True,
    print_blob=True,
)


class PrematureAggregate(AssertionError):
    """Aggregate publication crossed an unsatisfied reader/synth barrier."""


class WrongChildRun(AssertionError):
    """A child report with another dispatch identity was accepted."""


class FanoutMachine(RuleBasedStateMachine):
    def __init__(
        self,
        child_count: int = 2,
        *,
        retry: bool = False,
        max_retries: int | None = None,
    ) -> None:
        super().__init__()
        self.temporary = TemporaryDirectory(prefix="zf-fanout-model-")
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.state.mkdir()
        SessionStore(self.state / "session.yaml").create(project_root=str(self.root))
        self.config = _config(synth=True)
        self.config.project.state_dir = "state"
        if retry or max_retries is not None:
            self.config.workflow.stages[0].timeout_seconds = 1
            self.config.workflow.stages[0].aggregate.max_retries = (
                1 if max_retries is None else max_retries
            )
        if child_count == 3:
            self.config.roles.append(
                RoleConfig(name="review-c", backend="mock", role_kind="reader")
            )
            self.config.workflow.stages[0].roles.append("review-c")
        self.roles = list(self.config.workflow.stages[0].roles)
        for role_config in self.config.roles:
            if role_config.name in self.roles:
                role_config.publishes = [
                    "workflow.child.completed",
                    "workflow.child.failed",
                ]
        self.transport = _RecordingTransport()
        self.rounds: list[dict] = []
        self.inputs: list[ZfEvent] = []
        self.evidence = ModelEvidence(
            root=self.root,
            model_name=f"FanoutMachine{child_count}",
            source=Path(__file__),
            settings=str(FANOUT_SETTINGS),
            reference=lambda: {
                "rounds": self.rounds,
                "dispatch_roles": [item[0] for item in self.transport.sent],
            },
        )
        self.reopen()
        self.start()
        self.evidence.steps.clear()

    def events(self, kind: str, fanout_id: str) -> list[ZfEvent]:
        return [
            event
            for event in self.runtime.event_log.read_all()
            if event.type == kind and event.payload.get("fanout_id") == fanout_id
        ]

    def manifest(self, row: dict) -> dict:
        return json.loads(
            (self.state / "fanouts" / row["fanout_id"] / "manifest.json").read_text()
        )

    @rule()
    @record_action
    def reopen(self) -> None:
        self.runtime = Orchestrator(
            self.state, self.config, self.transport, project_root=self.root
        )
        self.runtime.run_once(events=[])

    @precondition(
        lambda self: (
            len(self.rounds) < 2 and (not self.rounds or self.rounds[-1]["cancelled"])
        )
    )
    @rule()
    @record_action
    def start(self) -> None:
        number = len(self.rounds) + 1
        run_id = f"RUN-FANOUT-{number}"
        self.runtime.event_writer.append(
            ZfEvent(
                type="run.started",
                actor="testkit",
                correlation_id=run_id,
                payload={"run_id": run_id, "workflow_run_id": run_id},
            )
        )
        trigger = self.runtime.event_writer.append(
            ZfEvent(
                id=f"{number:012d}-candidate",
                type="candidate.ready",
                actor="zf-cli",
                correlation_id=run_id,
                payload={
                    "pdd_id": f"F-FANOUT-{number}",
                    "workflow_run_id": run_id,
                    "run_id": run_id,
                },
            )
        )
        self.runtime.run_once(events=[trigger])
        started = [
            event
            for event in self.runtime.event_log.read_all()
            if event.type == "fanout.started" and event.causation_id == trigger.id
        ]
        assert len(started) == 1
        fanout_id = started[0].payload["fanout_id"]
        dispatched = self.events("fanout.child.dispatched", fanout_id)
        assert Counter(event.payload["child_id"] for event in dispatched) == Counter(
            self.roles
        )
        self.rounds.append(
            {
                "run_id": run_id,
                "fanout_id": fanout_id,
                "cancelled": False,
                "children": {role: "dispatched" for role in self.roles},
                "identities": {
                    event.payload["child_id"]: event.payload["run_id"]
                    for event in dispatched
                },
                "dispatch_counts": {role: 1 for role in self.roles},
                "terminal_sources": {},
                "synth_dispatched": False,
                "aggregate": "pending",
            }
        )

    @record_action
    def retry_once(self) -> None:
        """Fixed probe fixture: idle mock workers, then run the real timeout tick."""
        self._timeout_sweep(disable_future=True)
        row = self.rounds[-1]
        for role in self.roles:
            events = [
                event
                for event in self.events("fanout.child.dispatched", row["fanout_id"])
                if event.payload["child_id"] == role
            ]
            assert len(events) == 2
            assert events[-1].payload["retry_of_run_id"] == row["identities"][role]
            assert events[-1].payload["run_id"] != row["identities"][role]
            row["identities"][role] = events[-1].payload["run_id"]
            row["dispatch_counts"][role] = 2
        assert not self.events("fanout.timed_out", row["fanout_id"])

    def _timeout_sweep(self, *, disable_future: bool) -> None:
        """Run one deterministic liveness sweep against the real coordinator."""
        for role in self.roles:
            self.runtime._set_worker_state(role, "idle", force=True)
        with pytest.MonkeyPatch.context() as patch:
            future = time.time() + 10
            patch.setattr(self.runtime, "_now", lambda: future)
            self.runtime.run_once(events=[])
        if disable_future:
            # The existing retry-identity probe does not test exhaustion.
            self.config.workflow.stages[0].timeout_seconds = 3600

    def submit_child(
        self, row: dict, child: str, status: str, *, run_id: str = ""
    ) -> ZfEvent:
        event = self.runtime.event_writer.append(
            ZfEvent(
                type=f"workflow.child.{status}",
                actor=child,
                origin="worker",
                correlation_id=row["run_id"],
                payload={
                    "fanout_id": row["fanout_id"],
                    "workflow_run_id": row["run_id"],
                    "child_id": child,
                    "run_id": run_id or row["identities"][child],
                    "status": status,
                    "summary": "synthetic reader result",
                },
            )
        )
        self.inputs.append(event)
        self.runtime.run_once(events=[event])
        return event

    @rule(index=st.integers(0, 1), child=st.integers(0, 2), failed=st.booleans())
    @record_action
    def child_result(self, index: int, child: int, failed: bool) -> None:
        row = self.rounds[index % len(self.rounds)]
        role = self.roles[child % len(self.roles)]
        status = "failed" if failed else "completed"
        accepted = not row["cancelled"] and row["children"][role] == "dispatched"
        event = self.submit_child(row, role, status)
        if accepted:
            row["children"][role] = status
            row["terminal_sources"][role] = event.id
            if all(
                value in {"completed", "failed"} for value in row["children"].values()
            ):
                row["synth_dispatched"] = True

    def submit_synth(self, row: dict, approve: bool) -> None:
        event = self.runtime.event_writer.append(
            ZfEvent(
                type="fanout.synth.completed",
                actor="review-synth",
                origin="worker",
                correlation_id=row["run_id"],
                payload={
                    "fanout_id": row["fanout_id"],
                    "workflow_run_id": row["run_id"],
                    "recommendation": "approve" if approve else "reject",
                    "summary": "synthetic synthesis",
                },
            )
        )
        self.inputs.append(event)
        self.runtime.run_once(events=[event])

    @precondition(
        lambda self: any(
            row["synth_dispatched"] or row["cancelled"] for row in self.rounds
        )
    )
    @rule(index=st.integers(0, 1), approve=st.booleans())
    @record_action
    def synth_result(self, index: int, approve: bool) -> None:
        candidates = [
            row for row in self.rounds if row["synth_dispatched"] or row["cancelled"]
        ]
        row = candidates[index % len(candidates)]
        self.submit_synth(row, approve)
        if not row["cancelled"] and row["aggregate"] == "pending":
            row["aggregate"] = "completed" if approve else "failed"

    @precondition(lambda self: bool(self.inputs))
    @rule(index=st.integers(0, 30))
    @record_action
    def duplicate(self, index: int) -> None:
        # Duplicate delivery, not a second raw ledger append with the same ID.
        self.runtime.run_once(events=[self.inputs[index % len(self.inputs)]])

    @precondition(
        lambda self: any(
            row["aggregate"] in {"pending", "cancelled"} for row in self.rounds
        )
    )
    @rule(index=st.integers(0, 1))
    @record_action
    def cancel(self, index: int) -> None:
        candidates = [
            row for row in self.rounds if row["aggregate"] in {"pending", "cancelled"}
        ]
        row = candidates[index % len(candidates)]
        requested = self.runtime.event_writer.append(
            ZfEvent(
                type="control.action.requested",
                actor="operator",
                correlation_id=row["run_id"],
                payload={"action": "run-cancel", "run_id": row["run_id"]},
            )
        )
        result = ControlledActionService(
            self.state,
            self.runtime.event_writer,
            config=self.config,
            project_root=self.root,
            actor="operator",
            source="test",
            surface="web",
        ).execute(
            action="run-cancel",
            requested_action="run-cancel",
            requested=requested,
            payload={"run_id": row["run_id"], "reason": "model cancellation"},
        )
        assert result["ok"], result
        assert result["idempotent_replay"] is row["cancelled"]
        cancelled = [
            event
            for event in self.runtime.event_log.read_all()
            if event.type == "run.cancelled"
            and event.payload.get("run_id") == row["run_id"]
        ]
        assert len(cancelled) == 1
        self.runtime.run_once(events=cancelled)
        row.update(cancelled=True, aggregate="cancelled")

    @invariant()
    @record_invariant
    def consistent(self) -> None:
        expected_dispatches: Counter = Counter()
        for row in self.rounds:
            fanout_id = row["fanout_id"]
            manifest = self.manifest(row)
            actual_children = {
                child["child_id"]: child for child in manifest["children"]
            }
            assert set(actual_children) == set(self.roles)
            for role, status in row["children"].items():
                assert actual_children[role]["status"] == status, (row, actual_children)
                assert actual_children[role]["run_id"] == row["identities"][role]
                terminals = [
                    event
                    for kind in ("fanout.child.completed", "fanout.child.failed")
                    for event in self.events(kind, fanout_id)
                    if event.payload["child_id"] == role
                ]
                assert len(terminals) == int(role in row["terminal_sources"])
                if terminals:
                    assert terminals[0].causation_id == row["terminal_sources"][role]
            expected_dispatches.update(row["dispatch_counts"])
            expected_dispatches["review-synth"] += int(row["synth_dispatched"])
            assert (
                Counter(
                    event.payload["child_id"]
                    for event in self.events("fanout.child.dispatched", fanout_id)
                )
                == row["dispatch_counts"]
            )
            assert len(self.events("fanout.synth.dispatched", fanout_id)) == int(
                row["synth_dispatched"]
            )
            aggregates = self.events("fanout.aggregate.completed", fanout_id)
            if row["aggregate"] in {"pending", "cancelled"} and aggregates:
                raise PrematureAggregate(
                    f"unexpected aggregate before accepted synth: {aggregates[0].payload}"
                )
            assert len(aggregates) == int(row["aggregate"] in {"completed", "failed"})
            expected_aggregate = (
                "started"
                if row["aggregate"] == "pending" and row["synth_dispatched"]
                else row["aggregate"]
            )
            assert manifest["aggregate"]["status"] == expected_aggregate
            assert len(self.events("review.approved", fanout_id)) == int(
                row["aggregate"] == "completed"
            )
            assert len(self.events("review.rejected", fanout_id)) == int(
                row["aggregate"] == "failed"
            )
            assert len(self.events("fanout.cancelled", fanout_id)) == int(
                row["cancelled"]
            )
            # Additional replay consistency, never the reference-state oracle.
            assert (
                FanoutManifestProjector(self.state).rebuild(
                    fanout_id, self.runtime.event_log.read_all()
                )
                == manifest
            )
        assert Counter(item[0] for item in self.transport.sent) == +expected_dispatches
        assert not self.runtime.task_store.list_all_with_archive()
        assert not self.runtime.event_log.query(event_type="run.goal.completed")
        assert not (self.root / ".zf").exists()

    def teardown(self) -> None:
        self.temporary.cleanup()


@pytest.mark.parametrize("children", [2, 3])
def test_generated_fanout_prefixes(children: int) -> None:
    run_state_machine_as_test(lambda: FanoutMachine(children), settings=FANOUT_SETTINGS)


def _step(action: str, **arguments: object) -> dict:
    return {"action": action, "arguments": arguments}


@pytest.mark.parametrize("children,failed", [(2, False), (3, True)])
def test_fixed_fanout_reopen_and_completion(children: int, failed: bool) -> None:
    model = FanoutMachine(children)
    try:
        steps = [
            _step("child_result", index=0, child=children - 1, failed=failed),
            _step("duplicate", index=0),
            _step("reopen"),
        ]
        steps.extend(
            _step("child_result", index=0, child=child, failed=False)
            for child in range(children - 1)
        )
        steps.extend(
            [
                _step("synth_result", index=0, approve=not failed),
                _step("duplicate", index=children),
                _step("reopen"),
            ]
        )
        replay_steps(model, steps, model.consistent)
    finally:
        model.teardown()


def test_fixed_cancel_new_run_and_late_old_results() -> None:
    model = FanoutMachine()
    try:
        replay_steps(
            model,
            [
                _step("child_result", index=0, child=0, failed=False),
                _step("cancel", index=0),
                _step("cancel", index=0),
                _step("reopen"),
                _step("start"),
                _step("child_result", index=0, child=1, failed=False),
                _step("synth_result", index=0, approve=True),
                _step("cancel", index=0),
                _step("child_result", index=1, child=1, failed=False),
                _step("child_result", index=1, child=0, failed=False),
                _step("synth_result", index=1, approve=True),
                _step("reopen"),
            ],
            model.consistent,
        )
    finally:
        model.teardown()


@pytest.mark.parametrize("completed_children", [0, 1])
@pytest.mark.xfail(
    strict=True,
    raises=PrematureAggregate,
    reason="ZF-TEST-036: synth completion bypasses the reader barrier",
)
def test_synth_cannot_complete_before_reader_barrier(completed_children: int) -> None:
    model = FanoutMachine()
    try:
        for child in range(completed_children):
            model.child_result(0, child, False)
        model.consistent()
        with model.evidence.guard(
            "premature_synth", {"completed_children": completed_children}
        ):
            model.submit_synth(model.rounds[0], True)
            model.consistent()
    finally:
        model.teardown()


@pytest.mark.parametrize(
    "surface", ["unissued", "previous_retry", "previous_retry_reopen"]
)
@pytest.mark.xfail(
    strict=True,
    raises=WrongChildRun,
    reason="ZF-TEST-037: reader completion accepts a non-current child run",
)
def test_wrong_child_run_cannot_settle_current_dispatch(surface: str) -> None:
    model = FanoutMachine(retry=surface != "unissued")
    try:
        model.consistent()
        row = model.rounds[0]
        old_run = row["identities"]["review-a"]
        if surface != "unissued":
            model.retry_once()
            if surface == "previous_retry_reopen":
                model.reopen()
            model.consistent()
        with model.evidence.guard("wrong_child_run", {"surface": surface}):
            event = model.submit_child(
                row,
                "review-a",
                "completed",
                run_id="run-never-dispatched" if surface == "unissued" else old_run,
            )
            terminals = model.events("fanout.child.completed", row["fanout_id"])
            if any(terminal.causation_id == event.id for terminal in terminals):
                raise WrongChildRun(
                    f"{surface} child run produced fanout.child.completed"
                )
            model.consistent()
    finally:
        model.teardown()


def test_current_retry_results_still_complete_once() -> None:
    model = FanoutMachine(retry=True)
    try:
        replay_steps(
            model,
            [
                _step("retry_once"),
                _step("reopen"),
                _step("child_result", index=0, child=1, failed=False),
                _step("child_result", index=0, child=0, failed=False),
                _step("synth_result", index=0, approve=True),
                _step("reopen"),
            ],
            model.consistent,
        )
    finally:
        model.teardown()


@pytest.mark.parametrize("max_retries", [0, 1, 2])
def test_reader_retry_exhaustion_emits_one_terminal_failure_and_stops(
    max_retries: int,
) -> None:
    """A child may retry up to max_retries, then only one timeout is terminal."""

    model = FanoutMachine(max_retries=max_retries)
    try:
        row = model.rounds[0]
        for sweep in range(max_retries + 1):
            with model.evidence.guard("retry_budget_sweep", {
                "max_retries": max_retries, "sweep": sweep,
            }):
                exhausted = sweep == max_retries
                # Expected counts/status come from input budget, never output.
                for role in model.roles:
                    row["dispatch_counts"][role] = 1 + min(sweep + 1, max_retries)
                    if exhausted:
                        row["children"][role] = "failed"
                        row["terminal_sources"][role] = None
                if exhausted:
                    row["aggregate"] = "failed"
                model._timeout_sweep(disable_future=False)
                if not exhausted:
                    for role in model.roles:
                        dispatches = [
                            event for event in model.events(
                                "fanout.child.dispatched", row["fanout_id"],
                            ) if event.payload["child_id"] == role
                        ]
                        assert len(dispatches) == sweep + 2
                        latest = dispatches[-1].payload
                        assert latest["retry_of_run_id"] == row["identities"][role]
                        assert latest["run_id"] not in {
                            event.payload["run_id"] for event in dispatches[:-1]
                        }
                        row["identities"][role] = latest["run_id"]
                assert len(model.events("fanout.timed_out", row["fanout_id"])) == int(exhausted)
                model.consistent()

        with model.evidence.guard("exhaustion_reopen_and_replay", {"max_retries": max_retries}):
            kinds = ("fanout.child.dispatched", "fanout.child.failed",
                     "fanout.timed_out", "fanout.aggregate.completed", "review.rejected")
            terminal_ids = {
                kind: [event.id for event in model.events(kind, row["fanout_id"])]
                for kind in kinds
            }
            model._timeout_sweep(disable_future=False)
            model.reopen()  # Service reconstruction, not process crash recovery.
            model._timeout_sweep(disable_future=False)
            model.consistent()
            assert terminal_ids == {
                kind: [event.id for event in model.events(kind, row["fanout_id"])]
                for kind in kinds
            }
    finally:
        model.teardown()


@pytest.mark.parametrize("max_retries", [0, 1, 2])
@pytest.mark.parametrize("actions", [
    ["sweep"],
    ["reopen", "sweep", "replay", "sweep"],
    ["sweep", "replay", "reopen", "sweep", "replay", "sweep"],
])
def test_generated_reader_retry_controls_do_not_extend_budget(
    max_retries: int, actions: list[str]
) -> None:
    """Generated control actions cannot create an extra retry or terminal event."""

    model = FanoutMachine(max_retries=max_retries)
    sweeps = 0
    try:
        row = model.rounds[0]
        for action in actions:
            with model.evidence.guard(
                "generated_retry_control",
                {"max_retries": max_retries, "action": action, "sweeps": sweeps},
            ):
                if action == "reopen":
                    model.reopen()
                elif action == "replay":
                    events = model.runtime.event_log.read_all()
                    assert events
                    model.runtime.run_once(events=[events[-1]])
                else:
                    sweeps += 1
                    model._timeout_sweep(disable_future=False)

                exhausted = sweeps > max_retries
                expected_count = 1 + min(sweeps, max_retries)
                for role in model.roles:
                    dispatches = [
                        event
                        for event in model.events(
                            "fanout.child.dispatched", row["fanout_id"]
                        )
                        if event.payload["child_id"] == role
                    ]
                    assert len(dispatches) == expected_count
                    if not exhausted:
                        row["dispatch_counts"][role] = expected_count
                        row["identities"][role] = dispatches[-1].payload["run_id"]
                    else:
                        row["children"][role] = "failed"
                        row["terminal_sources"][role] = None
                if exhausted:
                    row["aggregate"] = "failed"
                assert len(model.events("fanout.timed_out", row["fanout_id"])) == int(
                    exhausted
                )
                model.consistent()
    finally:
        model.teardown()


@st.composite
def _retry_control_sequences(draw: st.DrawFn) -> tuple[int, list[str]]:
    return (
        draw(st.integers(min_value=0, max_value=2)),
        draw(
            st.lists(
                st.sampled_from(["sweep", "reopen", "replay"]),
                min_size=1,
                max_size=5,
            )
        ),
    )


@settings(max_examples=8, deadline=None, database=None, derandomize=True)
@given(_retry_control_sequences())
def test_hypothesis_reader_retry_controls_remain_budget_bounded(
    generated: tuple[int, list[str]]
) -> None:
    """A small deterministic generated action set must preserve the retry budget."""

    max_retries, actions = generated
    model = FanoutMachine(max_retries=max_retries)
    sweeps = 0
    try:
        row = model.rounds[0]
        for action in actions:
            if action == "reopen":
                model.reopen()
            elif action == "replay":
                events = model.runtime.event_log.read_all()
                model.runtime.run_once(events=[events[-1]])
            else:
                sweeps += 1
                model._timeout_sweep(disable_future=False)

            exhausted = sweeps > max_retries
            expected_count = 1 + min(sweeps, max_retries)
            for role in model.roles:
                dispatches = [
                    event
                    for event in model.events("fanout.child.dispatched", row["fanout_id"])
                    if event.payload["child_id"] == role
                ]
                assert len(dispatches) == expected_count
                if not exhausted:
                    row["dispatch_counts"][role] = expected_count
                    row["identities"][role] = dispatches[-1].payload["run_id"]
                else:
                    row["children"][role] = "failed"
                    row["terminal_sources"][role] = None
            if exhausted:
                row["aggregate"] = "failed"
            assert len(model.events("fanout.timed_out", row["fanout_id"])) == int(
                exhausted
            )
            model.consistent()
    finally:
        model.teardown()


def test_fanout_oracle_catches_extra_dispatch_and_retains_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "capture"
    monkeypatch.setenv("ZF_TESTKIT_MODEL_EVIDENCE", str(output))
    model = FanoutMachine()
    root = model.root
    try:
        model.consistent()
        # TEST-ONLY sink corruption: no reducer is used to construct expected.
        model.transport.sent.append(model.transport.sent[0])
        with pytest.raises(AssertionError) as caught:
            model.consistent()
        assert any("TestKit model evidence:" in note for note in caught.value.__notes__)
    finally:
        model.teardown()
    assert not root.exists()
    captures = list(output.glob("*/latest.json"))
    assert len(captures) == 1
    snapshot = json.loads(captures[0].read_text())
    assert snapshot["complete"] and snapshot["failed_check"] == "consistent"
    assert len(snapshot["reference"]["dispatch_roles"]) == 3
    files = {row["path"]: row for row in snapshot["files"]}
    assert "state/events.jsonl" in files
    assert any(path.endswith("/manifest.json") for path in files)
    for row in files.values():
        body = base64.b64decode(row["base64"], validate=True)
        assert (
            len(body) == row["bytes"]
            and hashlib.sha256(body).hexdigest() == row["sha256"]
        )
