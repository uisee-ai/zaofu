"""LH-4.T1: FailureTaxonomy — classify failure events into 9 categories.

Groups:
  SYSTEM_*     deterministic infra: IO / process / backend API
  BUSINESS_*   contractual rejection: gate / judge / discriminator
  AGENT_*      emergent behaviour: stuck / drift / budget

Each category has a default retry policy (max_retries + backoff floor +
"escalate on exhaust" flag). The reactor / dispatch code consults
``policy_for(category)`` before deciding retry vs escalate.

Pure functions — no I/O, no state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from zf.core.events.model import ZfEvent


class FailureCategory(str, Enum):
    SYSTEM_IO = "system.io"
    SYSTEM_PROCESS = "system.process"
    SYSTEM_BACKEND = "system.backend"
    BUSINESS_GATE = "business.gate"
    BUSINESS_JUDGE = "business.judge"
    BUSINESS_DISCRIMINATOR = "business.discriminator"
    AGENT_STUCK = "agent.stuck"
    AGENT_DRIFT = "agent.drift"
    AGENT_BUDGET = "agent.budget"


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    backoff_seconds: float
    escalate_on_exhaust: bool


# Per-category defaults. Tuned for long-horizon:
# - SYSTEM_BACKEND retries aggressively (Claude API 500s/429s are transient)
# - BUSINESS categories retry few times (human insight usually needed)
# - AGENT_STUCK goes straight to respawn (no retry of the same session)
# - AGENT_BUDGET cannot retry (the wallet is what it is)
_POLICIES: dict[FailureCategory, RetryPolicy] = {
    FailureCategory.SYSTEM_IO: RetryPolicy(3, 1.0, True),
    FailureCategory.SYSTEM_PROCESS: RetryPolicy(2, 2.0, True),
    FailureCategory.SYSTEM_BACKEND: RetryPolicy(5, 2.0, True),
    FailureCategory.BUSINESS_GATE: RetryPolicy(2, 0.0, True),
    FailureCategory.BUSINESS_JUDGE: RetryPolicy(2, 0.0, True),
    FailureCategory.BUSINESS_DISCRIMINATOR: RetryPolicy(1, 0.0, True),
    FailureCategory.AGENT_STUCK: RetryPolicy(0, 0.0, True),
    FailureCategory.AGENT_DRIFT: RetryPolicy(0, 0.0, False),
    FailureCategory.AGENT_BUDGET: RetryPolicy(0, 0.0, True),
}


def policy_for(category: FailureCategory) -> RetryPolicy:
    return _POLICIES[category]


_TYPE_MAP: dict[str, FailureCategory] = {
    "worker.stuck": FailureCategory.AGENT_STUCK,
    "worker.stuck.recovery_failed": FailureCategory.SYSTEM_PROCESS,
    "worker.respawn.failed": FailureCategory.SYSTEM_PROCESS,
    "worker.recycle.failed": FailureCategory.SYSTEM_PROCESS,
    "pane.crash": FailureCategory.SYSTEM_PROCESS,
    "worker.drift.detected": FailureCategory.AGENT_DRIFT,
    "cost.budget.exceeded": FailureCategory.AGENT_BUDGET,
    "agent.api_blocked": FailureCategory.SYSTEM_BACKEND,
    "agent.timeout": FailureCategory.SYSTEM_BACKEND,
    "review.rejected": FailureCategory.BUSINESS_GATE,
    "test.failed": FailureCategory.BUSINESS_GATE,
    "gate.failed": FailureCategory.BUSINESS_GATE,
    "judge.failed": FailureCategory.BUSINESS_JUDGE,
    "discriminator.failed": FailureCategory.BUSINESS_DISCRIMINATOR,
    "hook.write_failed": FailureCategory.SYSTEM_IO,
    "scope.violation": FailureCategory.BUSINESS_GATE,
}


def classify(event: ZfEvent) -> FailureCategory | None:
    """Return the FailureCategory for an event, or None if the event
    type is not a failure we reason about.

    Pure function of event.type; payload details never change the
    classification (we keep it predictable and greppable).
    """
    return _TYPE_MAP.get(event.type)
