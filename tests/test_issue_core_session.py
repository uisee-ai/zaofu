from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from issue_core import (
    AgentResult,
    ContinuousIssueAgent,
    InMemoryIssueSessionStore,
    JsonIssueSessionStore,
    IssueSessionRunner,
    IssueSessionWorker,
    IssueWorkingSet,
    SessionStatus,
    ReadOnlyInvestigationTools,
)


def test_partial_validation_with_investigation_failure_does_not_become_ready():
    class Agent:
        def run(self, *, session, working_set, tools):
            from dataclasses import replace
            return AgentResult(
                status="ready",
                working_set=replace(
                    working_set,
                    report={"title": "Candidate", "description": "Observed"},
                ),
                validation={
                    "status": "partial",
                    "checks": [{
                        "name": "independent_reproduction",
                        "status": "not_run",
                    }],
                    "failures": [{
                        "type": "missing_reproduction_artifact",
                        "field": "reproduction",
                        "message": "No wheel or traceback is available.",
                        "recoverable_by": "investigation",
                    }],
                },
            )

    store = InMemoryIssueSessionStore()
    session = store.create("Import fails")
    result = IssueSessionRunner(store).run_once(session.session_id, Agent(), ReadOnlyInvestigationTools(Path(".")))
    assert result.status == "continue"
    assert result.working_set.workflow["route"] == "investigation"
    check = result.working_set.workflow["validation"]["checks"][0]
    assert check["reason"]
    assert check["meaning"]
    assert check["next_action"]


class FakeTools:
    def read_text(self, path: str) -> str:
        return f"contents:{path}"

    def search(self, pattern: str, *, path: str = ".") -> Sequence[str]:
        return [f"{path}/match.txt"]

    def run_read_only(self, command: Sequence[str], *, timeout_seconds: int) -> Mapping[str, Any]:
        return {"status": "passed", "command": list(command), "timeout": timeout_seconds}


class ContinuousFakeAgent:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, *, session, working_set, tools) -> AgentResult:
        self.calls += 1
        fields = {"title": "Refresh does not update the page"}
        if self.calls == 1:
            return AgentResult(
                status="continue",
                working_set=replace(
                    working_set,
                    fields=fields,
                    claims=({"kind": "reported", "value": session.raw_report},),
                ),
            )
        return AgentResult(
            status="ready",
            working_set=replace(
                working_set,
                fields=fields,
                report={"title": fields["title"], "evidence_summary": "request remains pending"},
                evidences=({"kind": "runtime", "summary": "request remains pending"},),
            ),
        )


def test_continuous_issue_session_runs_without_zf_imports() -> None:
    store = InMemoryIssueSessionStore()
    session = store.create("Click Refresh and the page does not update")
    runner = IssueSessionRunner(store)
    agent = ContinuousFakeAgent()

    first = runner.run_once(session.session_id, agent, FakeTools())
    assert first.status == "continue"
    assert store.load_session(session.session_id).status == SessionStatus.INVESTIGATING

    second = runner.run_once(session.session_id, agent, FakeTools())
    assert second.status == "ready"
    assert store.load_session(session.session_id).status == SessionStatus.READY_FOR_CONFIRMATION
    assert store.load_working_set(session.session_id).report["title"] == "Refresh does not update the page"


def test_working_set_revision_conflict_rejects_late_writer() -> None:
    store = InMemoryIssueSessionStore()
    session = store.create("An issue")
    current = store.load_working_set(session.session_id)
    store.save_working_set(replace(current, revision=2, fields={"title": "new"}), expected_revision=1)
    with pytest.raises(ValueError, match="revision conflict"):
        store.save_working_set(replace(current, revision=2, fields={"title": "late"}), expected_revision=1)


def test_portable_worker_stops_for_user_or_ready_without_runtime() -> None:
    store = InMemoryIssueSessionStore()
    session = store.create("The page does not update")
    worker = IssueSessionWorker(store, max_turns=3)
    result = worker.run(session.session_id, ContinuousFakeAgent(), FakeTools())
    assert result.status == "ready_for_confirmation"
    assert result.turns == 2


def test_portable_worker_honors_cancellation_before_agent_call() -> None:
    store = InMemoryIssueSessionStore()
    session = store.create("The page does not update")
    called = False

    class NeverCalledAgent:
        def run(self, **kwargs):
            nonlocal called
            called = True
            raise AssertionError("agent should not run after cancellation")

    result = IssueSessionWorker(store, cancel_requested=lambda: True).run(
        session.session_id, NeverCalledAgent(), FakeTools()
    )
    assert result.status == "cancelled"
    assert called is False


def test_validation_failure_routes_back_to_recovery_instead_of_confirming() -> None:
    store = InMemoryIssueSessionStore()
    session = store.create("Refresh fails")

    class InvalidReadyAgent:
        def run(self, *, session, working_set, tools):
            return AgentResult(
                status="ready",
                working_set=replace(
                    working_set,
                    report={"title": "Refresh fails"},
                    evidences=({"kind": "user_report", "summary": "reported"},),
                ),
                validation={
                    "status": "fail",
                    "failures": [{
                        "type": "missing_user_intent",
                        "field": "expected_behavior",
                        "message": "Expected refresh behavior is unknown.",
                        "recoverable_by": "ask_user",
                    }],
                },
            )

    result = IssueSessionRunner(store).run_once(session.session_id, InvalidReadyAgent(), FakeTools())
    assert result.status == "ask_user"
    assert "Expected refresh behavior" in result.question
    assert store.load_session(session.session_id).status == SessionStatus.WAITING_FOR_USER


def test_continuous_agent_reconciles_raw_report_and_investigation() -> None:
    store = InMemoryIssueSessionStore()
    session = store.create(
        "Refresh fails.\n\nReproduction:\n1. Open the page\n2. Click Refresh\n\nExpected:\nThe page updates"
    )

    def investigate(session, working_set, tools):
        return {
            "field_updates": {"actual_behavior": "The request remains pending"},
            "evidences": ({"kind": "trace", "summary": "request remains pending"},),
        }

    agent = ContinuousIssueAgent(investigator=investigate)
    result = IssueSessionWorker(store, max_turns=2).run(session.session_id, agent, FakeTools())
    assert result.status == "ready_for_confirmation"
    assert result.last_result is not None
    assert result.last_result.working_set.report["actual_behavior"] == "The request remains pending"
    assert result.last_result.working_set.report["evidence_summary"]


def test_user_reply_resumes_the_same_issue_session() -> None:
    store = InMemoryIssueSessionStore()
    session = store.create("Refresh fails")

    class AskThenReady:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, *, session, working_set, tools):
            self.calls += 1
            if self.calls == 1:
                return AgentResult(
                    status="ask_user",
                    working_set=working_set,
                    question="Does it reproduce behind the company proxy?",
                )
            return AgentResult(
                status="ready",
                working_set=replace(
                    working_set,
                    report={"title": "Refresh fails", "proxy": session.user_replies[-1]},
                ),
            )

    runner = IssueSessionRunner(store)
    agent = AskThenReady()
    first = runner.run_once(session.session_id, agent, FakeTools())
    assert first.status == "ask_user"
    second = runner.answer_user(session.session_id, "Only behind the company proxy", agent, FakeTools())
    assert second.status == "ready"
    saved = store.load_session(session.session_id)
    assert saved.session_id == session.session_id
    assert saved.user_replies == ("Only behind the company proxy",)


def test_resolved_standard_gaps_do_not_repeat_after_user_reply() -> None:
    store = InMemoryIssueSessionStore()
    session = store.create("Refresh fails")
    runner = IssueSessionRunner(store)
    agent = ContinuousIssueAgent()
    first = runner.run_once(session.session_id, agent, FakeTools())
    assert first.status == "ask_user"
    second = runner.answer_user(
        session.session_id,
        "Reproduction:\n1. Open page\n2. Click Refresh\n\nExpected:\nPage updates",
        agent,
        FakeTools(),
    )
    assert second.status == "ready"
    assert second.working_set.gaps == ()
    assert len(second.working_set.claims) == 1


def test_json_store_survives_new_store_instance(tmp_path) -> None:
    first_store = JsonIssueSessionStore(tmp_path)
    session = first_store.create("Refresh fails")
    runner = IssueSessionRunner(first_store)
    result = runner.run_once(session.session_id, ContinuousFakeAgent(), FakeTools())
    assert result.status == "continue"

    second_store = JsonIssueSessionStore(tmp_path)
    restored = second_store.load_session(session.session_id)
    assert restored.session_id == session.session_id
    assert second_store.load_working_set(session.session_id).fields["title"] == "Refresh does not update the page"


def test_session_cancel_and_confirm_are_persisted(tmp_path) -> None:
    store = JsonIssueSessionStore(tmp_path)
    session = store.create("Refresh fails")
    runner = IssueSessionRunner(store)
    ready_agent = ContinuousFakeAgent()
    runner.run_once(session.session_id, ready_agent, FakeTools())
    ready = runner.run_once(session.session_id, ready_agent, FakeTools())
    assert ready.status == "ready"
    completed = runner.confirm(session.session_id, report_ref=ready.working_set.digest)
    assert completed.status == SessionStatus.COMPLETED
    assert completed.final_report_ref == ready.working_set.digest
    assert completed.publication_snapshot_ref.startswith("sha256:")
    snapshot = store.load_publication_snapshot(session.session_id)
    assert snapshot.working_set_digest == ready.working_set.digest
    assert snapshot.confirmed_by == "user"

    cancelled = store.create("Another issue")
    assert runner.cancel(cancelled.session_id).status == SessionStatus.CANCELLED
