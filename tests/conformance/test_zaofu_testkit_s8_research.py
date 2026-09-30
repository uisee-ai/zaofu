"""Official Research admission -> real Kernel -> durable result, no Provider turns."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import socket
import subprocess
import time

import pytest

from tests.test_control_actions_research import _config, _execute
from tests.test_research_fanout_template import _RecordingTransport
from tests.testkit.model_evidence import ModelEvidence
from zf.core.events import EventLog, EventWriter
from zf.core.events.model import ZfEvent
from zf.core.task.schema import Task
from zf.core.task.store import TaskStore
from zf.runtime.control_actions import ControlledActionService
from zf.runtime.orchestrator import Orchestrator
from zf.runtime.research_generation import research_generation_binding_error
from zf.runtime.workflow_anchor import mark_workflow_managed_task


class PrematureResearchDelivery(AssertionError):
    """An unissued synth result delivered research before the reader barrier."""


class WrongResearchChildRun(AssertionError):
    """An unissued child run settled a real Research dispatch."""


@pytest.fixture
def isolated_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def forbidden(*args, **kwargs):
        pytest.fail(f"Research fixture attempted an unapproved process/network call: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("ZF_PROJECT_ROOT", "ZF_STATE_DIR", "ZF_WORKFLOW_ACTION_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


class ResearchScenario:
    def __init__(self, root: Path, adaptive: bool) -> None:
        self.root = root
        self.adaptive = adaptive
        self.stage = "research-adaptive" if adaptive else "research-fanout"
        self.state = root / "custom-state"
        self.state.mkdir()
        self.config = _config()
        self.config.project.state_dir = "custom-state"
        for stage in self.config.workflow.stages:
            stage.timeout_seconds = 60
        self.store = TaskStore(self.state / "kanban.json")
        task = Task(id="TASK-RESEARCH", title="Controlled Research", status="in_progress")
        mark_workflow_managed_task(task)
        self.store.add(task)
        self.log = EventLog(self.state / "events.jsonl")
        self.writer = EventWriter(self.log)
        self.transport = _RecordingTransport()
        self.orch = Orchestrator(self.state, self.config, self.transport, project_root=root)
        self.service = ControlledActionService(
            self.state, self.writer, config=self.config, project_root=root,
            actor="web", source="kanban-agent", surface="web",
        )
        self.invoke: ZfEvent | None = None
        self.fanout_id = ""

    def events(self, kind: str) -> list[ZfEvent]:
        return [event for event in self.log.read_all() if event.type == kind]

    def start(self) -> list[ZfEvent]:
        result = _execute(self.service, self.writer, "research-start", {
            "task_id": "TASK-RESEARCH", "topic": "Read the isolated fixture",
            "template_id": ("research-adaptive.pilot.v1" if self.adaptive
                            else "research-fanout.fixed.v1"),
        })
        assert result["ok"], result
        [self.invoke] = self.events("workflow.invoke.requested")
        assert self.invoke.payload["request_kind"] == "research"
        assert research_generation_binding_error(
            self.state, config=self.config, task=self.store.get("TASK-RESEARCH"),
            payload=self.invoke.payload,
        ) == ""
        self.orch.run_once(events=[self.invoke])
        assert not self.events("workflow.invoke.rejected")
        [started] = self.events("fanout.started")
        assert started.payload["stage_id"] == self.stage
        self.fanout_id = started.payload["fanout_id"]
        children = self.events("fanout.child.dispatched")
        assert len(children) == (1 if self.adaptive else 4)
        assert len(self.transport.sent) == len(children)
        assert not self.events("fanout.synth.dispatched")
        return children

    def report(
        self, dispatch: ZfEvent, *, synth: bool = False, failed: bool = False,
        malformed: bool = False, empty: bool = False, wrong_run: bool = False,
    ) -> ZfEvent:
        assert self.invoke is not None
        role = dispatch.payload["role_instance"]
        source = self.root / f"{role}-source.txt"
        source.write_text(f"Isolated evidence for {role}.\n", encoding="utf-8")
        report = {
            "summary": f"Verified fixture report from {role}.",
            "findings": [], "recommendation": "reject" if failed else "approve",
            "evidence_refs": [str(source)],
        }
        if self.adaptive:
            report.update(
                architecture={"fixture": True}, acceptance_matrix=[{"id": "AC-1"}],
                test_matrix=[{"id": "T-1"}], task_map=[{"id": "TASK-1"}],
                provider_operation_summary={
                    "schema_version": "provider-operation-summary.v1",
                    "workflow_run_id": self.invoke.payload["workflow_run_id"],
                    "operation_id": "fixture-root", "provider_session_id": "fixture-session",
                    "settlement": "settled", "child_count": 0, "child_status_counts": {},
                    "active_child_count": 0, "peak_parallel_agents": 0,
                    # Synthetic no-Provider receipt, never measured usage/cost.
                    "usage": {}, "cost_usd": 0,
                },
            )
        if malformed:
            report["findings"] = "not a findings list"
        payload = deepcopy(dispatch.payload)
        payload.update(status="failed" if failed else "completed", report={} if empty else report)
        if failed:
            payload["reason"] = "fixture worker failed"
        if wrong_run:
            payload["run_id"] = "never-dispatched-run"
        return self.writer.append(ZfEvent(
            type=("fanout.synth.completed" if synth else
                  "research.child.failed" if failed else "research.child.completed"),
            actor=role, task_id="TASK-RESEARCH", payload=payload,
            origin="worker",
            causation_id=dispatch.id, correlation_id=self.invoke.correlation_id,
        ))

    def reopen(self) -> None:
        self.log = EventLog(self.state / "events.jsonl")
        self.writer = EventWriter(self.log)
        self.store = TaskStore(self.state / "kanban.json")
        self.transport = _RecordingTransport()
        self.orch = Orchestrator(
            self.state, self.config, self.transport, project_root=self.root,
        )

    def duplicate(self, event: ZfEvent) -> ZfEvent:
        return self.writer.append(ZfEvent(
            type=event.type, actor=event.actor, task_id=event.task_id,
            payload=deepcopy(event.payload), causation_id=event.causation_id,
            correlation_id=event.correlation_id, origin="worker",
        ))

    def business_snapshot(self) -> dict:
        kinds = {
            "fanout.child.completed", "fanout.child.failed", "fanout.synth.dispatched",
            "fanout.aggregate.completed", "workflow.result.available", "task.done.evidence",
            "research.fanout.completed", "research.adaptive.completed",
            "research.fanout.failed", "research.adaptive.failed",
        }
        return {
            "events": [event.to_json() for event in self.log.read_all()
                       if event.type in kinds],
            "task": self.store.get("TASK-RESEARCH").status,
            "artifacts": {str(path.relative_to(self.state)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (self.state / "artifacts/research").rglob("*")
                          if path.is_file()},
        }

    def assert_not_delivered(self) -> None:
        assert not self.events("workflow.result.available")
        assert not self.events("task.done.evidence")
        assert self.store.get("TASK-RESEARCH").status == "in_progress"
        assert not list((self.state / "artifacts/research").rglob("*.md"))

    def assert_delivered(self) -> None:
        assert self.invoke is not None
        [aggregate] = self.events("fanout.aggregate.completed")
        assert aggregate.payload["status"] == "completed"
        assert aggregate.payload["fanout_id"] == self.fanout_id
        [result] = self.events("workflow.result.available")
        assert result.payload["workflow_run_id"] == self.invoke.payload["workflow_run_id"]
        assert result.payload["terminal_event_id"] == aggregate.id
        assert result.causation_id == aggregate.id
        ref = aggregate.payload["research_artifact_ref"]
        path = (self.state / ref).resolve(strict=True)
        assert path.is_relative_to(self.state)
        body = path.read_bytes()
        assert b"Verified fixture report" in body
        assert self.invoke.payload["workflow_run_id"].encode() in body
        digest = hashlib.sha256(body).hexdigest()
        assert digest == aggregate.payload["research_artifact_digest"]
        assert (result.payload["artifact_ref"], result.payload["artifact_digest"]) == (ref, digest)
        assert bool(result.payload["root_result_event_id"]) is self.adaptive
        assert bool(result.payload["synth_event_id"]) is not self.adaptive
        [source] = [event for event in self.log.read_all()
                    if event.id == result.payload["result_source_event_id"]]
        assert source.origin == "worker"
        dispatches = self.events("fanout.child.dispatched")
        assert len(dispatches) == (1 if self.adaptive else 4)
        assert len({event.payload["run_id"] for event in dispatches}) == len(dispatches)
        assert source.causation_id in {
            event.id for event in (dispatches if self.adaptive
                                   else self.events("fanout.synth.dispatched"))
        }
        if self.adaptive:
            assert aggregate.payload["provider_operation_summary_status"] == "available"
            descriptor = aggregate.payload["provider_operation_summary_ref"]
            summary_path = (self.state / descriptor["ref"]).resolve(strict=True)
            assert summary_path.is_relative_to(self.state)
            summary_body = summary_path.read_bytes()
            assert hashlib.sha256(summary_body).hexdigest() == descriptor["sha256"]
            summary = json.loads(summary_body)
            assert summary["workflow_run_id"] == self.invoke.payload["workflow_run_id"]
            assert summary["operation_id"] == "fixture-root"
            assert summary["provider_session_id"] == "fixture-session"
            assert summary["settlement"] == "settled"
            assert summary["child_count"] == summary["active_child_count"] == 0
        assert len(self.events("fanout.synth.dispatched")) == (0 if self.adaptive else 1)
        assert self.store.get("TASK-RESEARCH").status == "done"
        assert len(self.events("task.done.evidence")) == 1
        assert len(self.events(f"{self.stage.replace('-', '.')}.completed")) == 1
        terminals = self.events("fanout.child.completed")
        assert len(terminals) == (1 if self.adaptive else 4)
        assert len({event.payload["child_id"] for event in terminals}) == len(terminals)
        assert not (self.root / ".zf").exists()


@contextmanager
def research_case(root: Path, adaptive: bool, case: str):
    evidence = ModelEvidence(
        root=root, model_name="research-controlled-route", source=Path(__file__),
        reference=lambda: {"case": case, "adaptive": adaptive},
        settings="fixed admission matrix; recording transport, no Provider execution",
        max_files=256,
    )
    with evidence.guard(case, {"adaptive": adaptive}):
        yield ResearchScenario(root, adaptive)


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
def test_controlled_research_start_delivers_one_verified_result(
    isolated_research: Path, adaptive: bool,
) -> None:
    with research_case(isolated_research, adaptive, "controlled-start-to-result") as scenario:
        children = scenario.start()
        for dispatch in children:
            scenario.orch.run_once(events=[scenario.report(dispatch)])
        if not adaptive:
            [synth] = scenario.events("fanout.synth.dispatched")
            scenario.orch.run_once(events=[scenario.report(synth, synth=True)])
        scenario.assert_delivered()


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
@pytest.mark.parametrize("malformed", [False, True], ids=["worker-failed", "malformed-report"])
def test_research_failure_never_delivers_success(
    isolated_research: Path, adaptive: bool, malformed: bool,
) -> None:
    with research_case(isolated_research, adaptive, "failed-report") as scenario:
        children = scenario.start()
        for index, dispatch in enumerate(children):
            result = scenario.report(
                dispatch, failed=index == 0 and not malformed, malformed=index == 0 and malformed,
            )
            scenario.orch.run_once(events=[result])
        assert len(scenario.events("fanout.child.failed")) == 1
        if malformed:
            assert scenario.events("fanout.child.failed")[0].payload["reason"] == "malformed_report"
        if not adaptive:
            [synth] = scenario.events("fanout.synth.dispatched")
            scenario.orch.run_once(events=[scenario.report(synth, synth=True, failed=True)])
        [aggregate] = scenario.events("fanout.aggregate.completed")
        assert aggregate.payload["status"] == "failed"
        assert len(scenario.events(f"{scenario.stage.replace('-', '.')}.failed")) == 1
        assert not scenario.events(f"{scenario.stage.replace('-', '.')}.completed")
        scenario.assert_not_delivered()


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
def test_research_waits_for_all_reports_then_accepts_reverse_order(
    isolated_research: Path, adaptive: bool,
) -> None:
    with research_case(isolated_research, adaptive, "pending-and-reverse-order") as scenario:
        children = list(reversed(scenario.start()))
        for dispatch in children[:-1]:
            scenario.orch.run_once(events=[scenario.report(dispatch)])
            assert not scenario.events("fanout.synth.dispatched")
        for _ in range(2):
            scenario.orch.run_once(events=[])
        assert not scenario.events("fanout.aggregate.completed")
        assert not scenario.events("fanout.synth.dispatched")
        scenario.assert_not_delivered()
        scenario.orch.run_once(events=[scenario.report(children[-1])])
        if not adaptive:
            [synth] = scenario.events("fanout.synth.dispatched")
            scenario.orch.run_once(events=[scenario.report(synth, synth=True)])
        scenario.assert_delivered()


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
def test_research_duplicate_facts_do_not_duplicate_effects(
    isolated_research: Path, adaptive: bool,
) -> None:
    with research_case(isolated_research, adaptive, "duplicate-facts") as scenario:
        children = scenario.start()
        for dispatch in children:
            result = scenario.report(dispatch)
            scenario.orch.run_once(events=[result])
            before = scenario.business_snapshot()
            sent = len(scenario.transport.sent)
            scenario.orch.run_once(events=[result, scenario.duplicate(result)])
            assert scenario.business_snapshot() == before
            assert len(scenario.transport.sent) == sent
        if not adaptive:
            [synth] = scenario.events("fanout.synth.dispatched")
            result = scenario.report(synth, synth=True)
            scenario.orch.run_once(events=[result])
        scenario.assert_delivered()
        before = scenario.business_snapshot()
        sent = len(scenario.transport.sent)
        scenario.orch.run_once(events=[result, scenario.duplicate(result)])
        scenario.orch.run_once(events=[])
        assert scenario.business_snapshot() == before
        assert len(scenario.transport.sent) == sent


@pytest.mark.parametrize("adaptive,cut", [
    (False, "partial"), (True, "partial"),
    (False, "persisted"), (True, "persisted"), (False, "synth"),
    (False, "terminal"), (True, "terminal"),
])
def test_research_reconstructs_from_the_same_durable_state(
    isolated_research: Path, adaptive: bool, cut: str,
) -> None:
    with research_case(isolated_research, adaptive, f"reopen-{cut}") as scenario:
        children = scenario.start()
        facts = []
        if cut == "partial":
            for dispatch in children[:2] if not adaptive else []:
                fact = scenario.report(dispatch)
                facts.append(fact)
                scenario.orch.run_once(events=[fact])
            scenario.reopen()
            scenario.orch.run_once(events=facts)
            assert not scenario.transport.sent
        for dispatch in children[len(facts):]:
            fact = scenario.report(dispatch)
            facts.append(fact)
            if cut == "persisted" and dispatch == children[-1]:
                scenario.reopen()
            scenario.orch.run_once(events=[fact])
        if not adaptive:
            [synth] = scenario.events("fanout.synth.dispatched")
            if cut == "synth":
                scenario.reopen()
            fact = scenario.report(synth, synth=True)
            facts.append(fact)
            scenario.orch.run_once(events=[fact])
        scenario.assert_delivered()
        before = scenario.business_snapshot()
        if cut == "terminal":
            scenario.reopen()
        sent = len(scenario.transport.sent)
        scenario.orch.run_once(events=facts)
        scenario.orch.run_once(events=[])
        assert scenario.business_snapshot() == before
        assert len(scenario.transport.sent) == sent


def test_research_synth_timeout_cannot_deliver(isolated_research: Path) -> None:
    with research_case(isolated_research, False, "synth-timeout") as scenario:
        children = scenario.start()
        for dispatch in children:
            scenario.orch.run_once(events=[scenario.report(dispatch)])
        assert len(scenario.events("fanout.synth.dispatched")) == 1
        scenario.orch._now = lambda: time.time() + 120
        scenario.orch.run_once(events=[])
        [aggregate] = scenario.events("fanout.aggregate.completed")
        assert aggregate.payload["status"] == "failed"
        scenario.assert_not_delivered()


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
def test_research_empty_summary_cannot_be_delivered(
    isolated_research: Path, adaptive: bool,
) -> None:
    with research_case(isolated_research, adaptive, "missing-summary") as scenario:
        children = scenario.start()
        for dispatch in children:
            scenario.orch.run_once(events=[scenario.report(dispatch, empty=adaptive)])
        if not adaptive:
            [synth] = scenario.events("fanout.synth.dispatched")
            scenario.orch.run_once(events=[scenario.report(synth, synth=True, empty=True)])
        scenario.assert_not_delivered()


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
def test_research_child_timeout_is_bounded(isolated_research: Path, adaptive: bool) -> None:
    with research_case(isolated_research, adaptive, "child-timeout") as scenario:
        children = scenario.start()
        for dispatch in children[:-1]:
            scenario.orch.run_once(events=[scenario.report(dispatch)])
        scenario.orch._now = lambda: time.time() + 120
        scenario.orch.run_once(events=[])
        [timeout] = scenario.events("fanout.timed_out")
        assert timeout.payload["pending_children"] == [children[-1].payload["child_id"]]
        [aggregate] = scenario.events("fanout.aggregate.completed")
        assert aggregate.payload["status"] == "failed"
        scenario.assert_not_delivered()


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
def test_failed_research_stays_failed_after_reconstruction(
    isolated_research: Path, adaptive: bool,
) -> None:
    with research_case(isolated_research, adaptive, "failed-terminal-reopen") as scenario:
        children = scenario.start()
        facts = []
        for dispatch in children:
            fact = scenario.report(dispatch, failed=True)
            facts.append(fact)
            scenario.orch.run_once(events=[fact])
        if not adaptive:
            [synth] = scenario.events("fanout.synth.dispatched")
            fact = scenario.report(synth, synth=True, failed=True)
            facts.append(fact)
            scenario.orch.run_once(events=[fact])
        [aggregate] = scenario.events("fanout.aggregate.completed")
        assert aggregate.payload["status"] == "failed"
        before = scenario.business_snapshot()
        scenario.reopen()
        scenario.orch.run_once(events=facts)
        scenario.orch.run_once(events=[])
        assert scenario.business_snapshot() == before
        scenario.assert_not_delivered()


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
@pytest.mark.xfail(
    strict=True, raises=WrongResearchChildRun,
    reason="ZF-TEST-037: official Research accepts an unissued child run",
)
def test_research_rejects_unissued_child_run(isolated_research: Path, adaptive: bool) -> None:
    with research_case(isolated_research, adaptive, "unissued-child-run") as scenario:
        children = scenario.start()
        result = scenario.report(children[0], wrong_run=True)
        scenario.orch.run_once(events=[result])
        accepted = scenario.events("fanout.child.completed")
        if (len(accepted) == 1
                and accepted[0].payload["child_id"] == children[0].payload["child_id"]
                and accepted[0].payload["run_id"] == "never-dispatched-run"):
            raise WrongResearchChildRun("unissued run accepted into canonical child completion")
        assert not accepted
        scenario.assert_not_delivered()


@pytest.mark.xfail(
    strict=True, raises=PrematureResearchDelivery,
    reason="ZF-TEST-036: official Research completes Task before reader/synth barrier",
)
def test_research_rejects_synth_before_dispatch(isolated_research: Path) -> None:
    with research_case(isolated_research, False, "early-synth") as scenario:
        scenario.start()
        fake_dispatch = ZfEvent(type="test.fixture.reference", payload={
            "fanout_id": scenario.fanout_id, "stage_id": scenario.stage,
            "role_instance": "synthesizer", "run_id": f"run-{scenario.fanout_id}-synth",
        })
        result = scenario.report(fake_dispatch, synth=True)
        scenario.orch.run_once(events=[result])
        aggregates = scenario.events("fanout.aggregate.completed")
        if (not scenario.events("fanout.synth.dispatched")
                and not scenario.events("fanout.child.completed")
                and len(aggregates) == 1 and aggregates[0].payload["status"] == "completed"
                and len(scenario.events("workflow.result.available")) == 1
                and scenario.store.get("TASK-RESEARCH").status == "done"):
            raise PrematureResearchDelivery(
                "unissued synth completed Research and its Task with all readers pending"
            )
        assert not aggregates
        scenario.assert_not_delivered()
