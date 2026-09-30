"""Synthetic failing models, collected only by explicit runner contract tests."""

from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule, run_state_machine_as_test

from tests.testkit.model_evidence import ModelEvidence, record_action, record_invariant
from zf.core.events import EventLog, ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore


PROBE_SETTINGS = settings(
    max_examples=10, stateful_step_count=5, deadline=None,
    database=None, derandomize=True, print_blob=True,
)


class CounterModel(RuleBasedStateMachine):
    def __init__(self, *, broken: bool = True, fail_in_rule: bool = False) -> None:
        super().__init__()
        self.temporary = TemporaryDirectory(prefix="zf-model-evidence-probe-")
        self.root = Path(self.temporary.name)
        self.store = TaskStore(self.root / "kanban.json")
        self.store.add(Task(id="PROBE", title="synthetic counter", priority=0))
        self.log = EventLog(self.root / "events.jsonl")
        self.total = 0
        self.broken = broken
        self.fail_in_rule = fail_in_rule
        self.evidence = ModelEvidence(
            root=self.root, model_name=type(self).__qualname__, source=Path(__file__),
            settings=str(PROBE_SETTINGS), reference=lambda: {
                "total": self.total, "broken": self.broken, "fail_in_rule": self.fail_in_rule,
            },
        )

    @rule(value=st.integers(min_value=0, max_value=3))
    @record_action
    def add(self, value: int) -> None:
        self.total += value
        self.log.append(ZfEvent(type="probe.increment", task_id="PROBE", payload={"value": value}))
        # Deliberate fixture bug, not ZaoFu production behavior.
        observed = self.total + int(self.broken and self.total >= 2)
        self.store.update("PROBE", priority=observed)
        if self.fail_in_rule:
            assert observed == self.total, "synthetic rule counter bug"

    @invariant()
    @record_invariant
    def consistent(self) -> None:
        assert sum(event.payload["value"] for event in self.log.read_all()) == self.total
        assert self.store.get("PROBE").priority == self.total, "synthetic invariant counter bug"

    def teardown(self) -> None:
        self.temporary.cleanup()
        super().teardown()


def test_generated_invariant_failure() -> None:
    run_state_machine_as_test(CounterModel, settings=PROBE_SETTINGS)


def test_generated_rule_failure() -> None:
    run_state_machine_as_test(lambda: CounterModel(fail_in_rule=True), settings=PROBE_SETTINGS)


def test_fixed_model() -> None:
    run_state_machine_as_test(lambda: CounterModel(broken=False), settings=PROBE_SETTINGS)
