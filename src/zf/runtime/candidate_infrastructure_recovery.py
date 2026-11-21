"""Typed Candidate execution failures retain a diagnosis owner."""
from zf.core.events import ZfEvent
from zf.runtime.canonical_recovery import classify_recovery_scope


def candidate_infrastructure_failure(event: ZfEvent) -> bool:
    return (event.type in {"test.failed", "verify.failed", "review.rejected"}
        and event.payload.get("failure_kind") == "infra"
        and bool(event.payload.get("fanout_id"))
        and classify_recovery_scope(event) == "candidate")
