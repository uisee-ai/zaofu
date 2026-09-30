"""Real task-map/contract rollover and worker-result caller admission.

This tests the legacy dev lane, not Task Pipeline or fanout generations.
The transport is inert; declared verification commands are never executed.
"""

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from tests.test_scheduler_task_attempt import _RecordingTransport, _send
from tests.testkit.evidence import write_json_atomic
from tests.testkit.model_evidence import ModelEvidence, record_action, record_invariant, replay_steps
from zf.core.config.schema import (
    ContractDConfig, ProjectConfig, RoleConfig, VerificationConfig,
    WorkflowConfig, WorkflowTaskAttemptConfig, ZfConfig,
)
from zf.core.events import ZfEvent
from zf.core.state.session import SessionStore
from zf.core.state.task_attempts import TaskAttemptStore
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.product_delivery import ingest_task_map_to_kanban
from zf.runtime.task_attempt_runtime import dispatch_attempt_payload
from zf.runtime.run_admission import task_workflow_run_id


GENERATION_SETTINGS = settings(
    max_examples=25, stateful_step_count=20, deadline=None,
    database=None, derandomize=True, print_blob=True,
)
REVISION_FIELDS = ("source_revision", "contract_revision", "capsule_revision")


class StaleAttemptSettlement(AssertionError):
    """A rejected worker result was nevertheless used to settle its attempt."""


class ContractGenerationMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.temporary = TemporaryDirectory(prefix="zf-generation-model-")
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.state.mkdir()
        SessionStore(self.state / "session.yaml").create(project_root=str(self.root))
        self.config = ZfConfig(
            project=ProjectConfig(name="generation-model", state_dir="state"),
            roles=[RoleConfig(name="dev", instance_id="dev", backend="mock", publishes=["dev.build.done"])],
            verification=VerificationConfig(contract=ContractDConfig(dispatch_token_required=True)),
            workflow=WorkflowConfig(task_attempt=WorkflowTaskAttemptConfig(mode="enforce", max_attempts=10)),
        )
        self.patch = pytest.MonkeyPatch()
        self.patch.setattr("zf.runtime.task_attempt_runtime._now", lambda: datetime(2030, 1, 1, tzinfo=timezone.utc).isoformat())
        self.patch.setattr("zf.runtime.task_attempt_runtime._lease_expiry", lambda _: "2030-01-01T00:01:00+00:00")
        self.patch.setattr("zf.runtime.task_attempt_runtime._expired", lambda value: value <= "2030-01-01T00:00:00+00:00")
        self.epoch = 0
        self.generations: dict[int, dict] = {}
        self.expected: list[dict] = []
        self.payloads: list[dict] = []
        self.results: list[ZfEvent] = []
        self.accepted: list[str] = []
        self.task_status = "backlog"
        self.transports: list[_RecordingTransport] = []
        self.evidence = ModelEvidence(
            root=self.root, model_name=type(self).__qualname__, source=Path(__file__),
            settings=str(GENERATION_SETTINGS), reference=lambda: {
                "epoch": self.epoch, "generations": self.generations,
                "attempts": self.expected, "accepted": self.accepted,
                "task_status": self.task_status,
            },
        )
        self.reopen()
        self.rollover()
        self.evidence.steps.clear()

    @rule()
    @record_action
    def reopen(self) -> None:
        transport = _RecordingTransport(self.state)
        self.transports.append(transport)
        self.runtime = Orchestrator(self.state, self.config, transport, project_root=self.root)
        self.store = TaskAttemptStore(self.state / "task_attempts.json")

    @precondition(lambda self: self.epoch < 5)
    @rule()
    @record_action
    def rollover(self) -> None:
        self.epoch += 1
        generation = f"map-{self.epoch}"
        ref = f"artifacts/maps/{generation}.json"
        task_map = {
            "schema_version": "task-map.v1", "feature_id": "F-ATTEMPT",
            "source_refs": {"task_map_generation": generation},
            "tasks": [{
                "task_id": "TASK-1", "title": "generation model", "owner_role": "dev",
                "scope": ["src/example.py"], "verification": "python -c 'pass'",
                "verification_tiers": ["runtime"], "acceptance": ["only current results advance"],
                "evidence_contract": {"workflow_run_id": "RUN-1"},
            }],
        }
        write_json_atomic(self.state / ref, task_map)
        result = ingest_task_map_to_kanban(
            self.state, task_map, task_map_ref=ref,
            writer=self.runtime.event_writer, correlation_id="RUN-1",
        )
        assert result.passed, result.errors
        task = self.runtime.task_store.get("TASK-1")
        assert task is not None and task.contract is not None
        assert task_workflow_run_id(task) == "RUN-1"
        revisions = {key: getattr(task.contract, key) for key in REVISION_FIELDS}
        assert all(revisions.values())
        if self.epoch > 1:
            assert revisions["contract_revision"] != self.generations[self.epoch - 1]["revisions"]["contract_revision"]
            assert task.contract_authority_sequence > self.generations[self.epoch - 1]["authority_sequence"]
            assert task.contract_authority_revision != self.generations[self.epoch - 1]["authority_revision"]
        self.generations[self.epoch] = {
            "generation": generation, "ref": ref, "revisions": revisions,
            "authority_sequence": task.contract_authority_sequence,
            "authority_revision": task.contract_authority_revision,
            # Freeze the admitted contract; never derive expected admission
            # using the production reducer or actual result status.
            "contract": asdict(task.contract),
        }
        commits = [event for event in self.runtime.event_log.read_all() if event.type == "task_map.materialization.committed"]
        assert len(commits) == self.epoch
        assert commits[-1].payload["task_doc_failures"] == []

    @precondition(lambda self: len(self.expected) < 6)
    @rule()
    @record_action
    def dispatch(self) -> None:
        prior = self.expected[-1] if self.expected else None
        succeeded = prior is not None and prior["status"] == "succeeded"
        context = _send(self.runtime, self.state, dispatch_id=f"dispatch-{len(self.expected) + 1}")
        assert context is not None
        if prior and not succeeded:
            prior["status"] = "superseded"
        self.expected.append({
            "attempt_id": context.attempt_id, "dispatch_id": context.dispatch_id,
            "run_id": "RUN-1", "operation_id": context.operation_id, "lease_id": context.lease_id,
            "ordinal": 1 if prior is None or succeeded else prior["ordinal"] + 1,
            "series": 1 if prior is None else prior["series"] + int(succeeded),
            "status": "sent", "terminal_event_id": "", "epoch": self.epoch,
        })
        self.payloads.append({
            **dispatch_attempt_payload(context), **self.generations[self.epoch]["revisions"],
            "task_map_generation": self.generations[self.epoch]["generation"],
        })
        self.task_status = "in_progress"

    @precondition(lambda self: bool(self.expected))
    @rule(index=st.integers(min_value=0, max_value=5))
    @record_action
    def result(self, index: int) -> None:
        index %= len(self.expected)
        expected = self.expected[index]
        accepted = index == len(self.expected) - 1 and expected["status"] == "sent" and expected["epoch"] == self.epoch
        event = self.runtime.event_writer.append(ZfEvent(
            type="dev.build.done", actor="dev", origin="worker", task_id="TASK-1",
            correlation_id="RUN-1", payload=dict(self.payloads[index]),
        ))
        self.results.append(event)
        decisions = self.runtime.run_once(events=[event])
        if not accepted and self.store.get(expected["attempt_id"])["terminal_event_id"] == event.id:
            raise StaleAttemptSettlement(f"stale result {event.id} settled attempt {expected['attempt_id']}")
        if accepted:
            expected.update(status="succeeded", terminal_event_id=event.id)
            self.accepted.append(event.id)
            self.task_status = "review"
            assert not [row for row in decisions if row.action == "block"], decisions
        else:
            assert any(row.action in {"block", "skip"} for row in decisions), decisions
            if index == len(self.expected) - 1 and expected["status"] == "sent":
                # Same active five-tuple and live lease: must reach the
                # contract gate, not merely fail earlier attempt admission.
                assert any("capsule revision stale" in row.reason for row in decisions), decisions
                rejections = [row for row in self.runtime.event_log.read_all() if row.type == "task.completion.stale_rejected" and row.causation_id == event.id]
                assert len(rejections) == 1
                assert rejections[0].payload["expected"] == self.generations[self.epoch]["revisions"]
        self.truth_matches_model()

    @precondition(lambda self: bool(self.results))
    @rule(index=st.integers(min_value=0, max_value=5))
    @record_action
    def duplicate(self, index: int) -> None:
        # Watcher redelivery of the same occurrence, not raw ledger rewrite.
        self.runtime.run_once(events=[self.results[index % len(self.results)]])
        self.truth_matches_model()

    @invariant()
    @record_invariant
    def truth_matches_model(self) -> None:
        task = self.runtime.task_store.get("TASK-1")
        assert task is not None and task.status == self.task_status
        assert asdict(task.contract) == self.generations[self.epoch]["contract"]
        assert task_workflow_run_id(task) == "RUN-1"
        assert task.contract_authority_revision == self.generations[self.epoch]["authority_revision"]
        assert task.contract.evidence_contract["source_refs"]["task_map_generation"] == f"map-{self.epoch}"
        rows = self.store.rows()
        assert len(rows) == len(self.expected)
        for expected in self.expected:
            row = next(row for row in rows if row["attempt_id"] == expected["attempt_id"])
            assert {key: row.get(key, "") for key in expected if key != "epoch"} == {key: value for key, value in expected.items() if key != "epoch"}
        if self.expected:
            assert self.store.current(run_id="RUN-1", task_id="TASK-1")["attempt_id"] == self.expected[-1]["attempt_id"]
            assert task.active_dispatch_id == self.expected[-1]["dispatch_id"]
        assert sum(len(transport.contexts) for transport in self.transports) == len(self.expected)
        events = self.runtime.event_log.read_all()
        succeeded = [row for row in events if row.type == "task.attempt.succeeded"]
        assert [row.payload["source_event_id"] for row in succeeded] == self.accepted
        assert not [row for row in events if row.type in {"run.goal.completed", "task.done"}]
        counts = Counter(row.id for row in events)
        assert all(count == 1 for count in counts.values())

    def teardown(self) -> None:
        try:
            self.patch.undo()
            self.temporary.cleanup()
        finally:
            super().teardown()


TestContractGeneration = ContractGenerationMachine.TestCase
TestContractGeneration.settings = GENERATION_SETTINGS


def test_three_generation_rollovers_do_not_reset_attempt_identity() -> None:
    model = ContractGenerationMachine()
    steps = [
        {"action": "dispatch", "arguments": {}},
        {"action": "rollover", "arguments": {}},
        {"action": "result", "arguments": {"index": 0}},
        {"action": "rollover", "arguments": {}},
        {"action": "reopen", "arguments": {}},
        {"action": "result", "arguments": {"index": 0}},
        {"action": "duplicate", "arguments": {"index": 0}},
        {"action": "dispatch", "arguments": {}},
        {"action": "result", "arguments": {"index": 1}},
    ]
    try:
        replay_steps(model, steps, model.truth_matches_model)
        assert model.epoch == 3
        assert [(row["series"], row["ordinal"]) for row in model.expected] == [(1, 1), (1, 2)]
    finally:
        model.teardown()


@pytest.mark.parametrize("reopen", [False, True], ids=["live", "reopened"])
def test_rollover_rejects_old_contract_before_attempt_settlement(reopen: bool) -> None:
    model = ContractGenerationMachine()
    steps = [
        {"action": "dispatch", "arguments": {}},
        {"action": "rollover", "arguments": {}},
        *([{"action": "reopen", "arguments": {}}] if reopen else []),
        {"action": "result", "arguments": {"index": 0}},
        {"action": "duplicate", "arguments": {"index": 0}},
        {"action": "dispatch", "arguments": {}},
        {"action": "result", "arguments": {"index": 0}},
        {"action": "result", "arguments": {"index": 1}},
        {"action": "reopen", "arguments": {}},
        {"action": "duplicate", "arguments": {"index": 2}},
        {"action": "dispatch", "arguments": {}},
        {"action": "result", "arguments": {"index": 2}},
    ]
    try:
        replay_steps(model, steps, model.truth_matches_model)
    finally:
        model.teardown()


@pytest.mark.parametrize("surface", ["old_attempt_new_revisions", "new_attempt_old_revisions", "missing_revisions"])
def test_attempt_and_revision_guards_are_both_required(surface: str) -> None:
    model = ContractGenerationMachine()
    try:
        model.dispatch()
        model.rollover()
        model.dispatch()
        model.reopen()
        with model.evidence.guard("mixed_identity", {"surface": surface}):
            payload = dict(model.payloads[0 if surface == "old_attempt_new_revisions" else 1])
            payload.update(model.generations[2 if surface == "old_attempt_new_revisions" else 1]["revisions"])
            if surface == "missing_revisions":
                for key in REVISION_FIELDS:
                    payload.pop(key)
            event = model.runtime.event_writer.append(ZfEvent(
                type="dev.build.done", actor="dev", origin="worker", task_id="TASK-1",
                correlation_id="RUN-1", payload=payload,
            ))
            attempts_before = model.store.load()
            task_before = asdict(model.runtime.task_store.get("TASK-1"))
            decisions = model.runtime.run_once(events=[event])
            reason = "TaskAttempt" if surface == "old_attempt_new_revisions" else "capsule revision stale"
            assert any(row.action == "block" and reason in row.reason for row in decisions), decisions
            assert model.store.load() == attempts_before
            assert asdict(model.runtime.task_store.get("TASK-1")) == task_before
            model.truth_matches_model()
        model.result(1)  # Positive control must still advance exactly once.
    finally:
        model.teardown()


def test_rollover_oracle_detects_bypassed_gate_and_retains_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "captured"
    monkeypatch.setenv("ZF_TESTKIT_MODEL_EVIDENCE", str(output))
    model = ContractGenerationMachine()
    root = model.root
    try:
        model.dispatch()
        model.rollover()
        model.truth_matches_model()
        # Inject a TEST-ONLY broken admission boundary, without mutating src.
        monkeypatch.setattr(model.runtime, "_reject_stale_task_capsule_revision", lambda event, task: None)
        with pytest.raises(StaleAttemptSettlement) as caught:
            model.result(0)
        assert any("TestKit model evidence:" in note for note in caught.value.__notes__)
    finally:
        model.teardown()
    assert not root.exists()
    snapshots = list(output.glob("*/latest.json"))
    assert len(snapshots) == 1
    snapshot = json.loads(snapshots[0].read_text())
    assert snapshot["complete"] and snapshot["failed_check"] == "result"
    assert snapshot["reference"]["epoch"] == 2
    assert snapshot["reference"]["accepted"] == []
    files = {row["path"]: row for row in snapshot["files"]}
    assert {"state/kanban.json", "state/task_attempts.json", "state/events.jsonl"} <= files.keys()
    for row in files.values():
        body = base64.b64decode(row["base64"], validate=True)
        assert len(body) == row["bytes"] and hashlib.sha256(body).hexdigest() == row["sha256"]
    attempts = json.loads(base64.b64decode(files["state/task_attempts.json"]["base64"]))
    assert next(iter(attempts["attempts"].values()))["status"] == "succeeded"
