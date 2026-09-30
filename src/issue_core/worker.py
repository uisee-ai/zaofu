"""Portable worker for a continuous Issue Agent session."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Callable

from .session import (
    AgentResult,
    InvestigationTools,
    IssueAgent,
    IssueSessionRunner,
    IssueSessionStore,
)


@dataclass(frozen=True)
class WorkerResult:
    status: str
    turns: int
    last_result: AgentResult | None = None
    reason: str = ""


class IssueSessionWorker:
    """Run one Session until it needs a user, is ready, or reaches a guard."""

    def __init__(
        self,
        store: IssueSessionStore,
        *,
        max_turns: int = 20,
        active_budget_seconds: float = 1200.0,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        if max_turns < 1 or active_budget_seconds <= 0:
            raise ValueError("worker limits must be positive")
        self.runner = IssueSessionRunner(store)
        self.max_turns = max_turns
        self.active_budget_seconds = active_budget_seconds
        self.cancel_requested = cancel_requested or (lambda: False)

    def run(
        self,
        session_id: str,
        agent: IssueAgent,
        tools: InvestigationTools,
    ) -> WorkerResult:
        started = monotonic()
        last: AgentResult | None = None
        for turn in range(1, self.max_turns + 1):
            if self.cancel_requested():
                # A host may persist a PAUSED checkpoint before revoking the
                # provider process. Do not turn that resumable state into a
                # terminal cancellation when the late worker observes it.
                if self.runner.store.load_session(session_id).status.value != "paused":
                    self.runner.cancel(session_id)
                return WorkerResult("cancelled", turn - 1, last, "cancel requested")
            if monotonic() - started >= self.active_budget_seconds:
                return WorkerResult("budget_exhausted", turn - 1, last, "active budget exhausted")
            last = self.runner.run_once(session_id, agent, tools)
            if last.status == "ask_user":
                return WorkerResult("waiting_for_user", turn, last, last.question)
            if last.status == "ready":
                return WorkerResult("ready_for_confirmation", turn, last)
            if last.status == "failed":
                return WorkerResult("failed", turn, last, last.reason)
        return WorkerResult("turn_limit_exhausted", self.max_turns, last, "maximum turns exhausted")


__all__ = ["IssueSessionWorker", "WorkerResult"]
