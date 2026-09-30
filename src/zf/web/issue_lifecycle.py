"""Read-only lifecycle display metadata derived from accepted Issue projections.

This does not decide workflow transitions or infer missing earlier stages. The
caller passes only changes already accepted by the existing projection.
"""
from __future__ import annotations

from typing import Any

STAGES = ("repository", "triage", "decision", "fix", "delivery", "complete")
_DECISION = {"needs_info", "needs_info_exhausted", "unverified_candidate", "awaiting_fix_approval"}
_DELIVERY = {"verified_candidate", "approved_for_pr", "owner_changes_requested", "owner_rejected", "publication_prepared", "pr_open", "pr_changes_requested", "pr_approved", "pr_closed_without_merge"}
_ATTENTION = {"triage_paused", "triage_recovering", "fix_paused", "fix_recovering", "needs_info", "needs_info_exhausted", "unverified_candidate", "awaiting_fix_approval", "owner_changes_requested", "publication_prepared", "pr_changes_requested", "triage_cancelled", "fix_cancelled"}
_ERRORS = {"triage_blocked", "fix_blocked", "blocked", "failed", "owner_rejected", "pr_closed_without_merge"}


def stage_for_state(state: str, previous: str = "repository") -> str:
    if state == "merged":
        return "complete"
    if state.startswith("triage_") or state == "triaging":
        return "triage"
    if state in _DECISION:
        return "decision"
    if state.startswith("fix_") or state in {"fixing", "verifying"}:
        return "fix"
    if state in _DELIVERY:
        return "delivery"
    if state in {"blocked", "failed"}:
        return previous  # Failure belongs to the stage that actually stopped.
    return "repository"


def state_outcome(state: str) -> str:
    if state == "merged":
        return "completed"
    if state in _ERRORS:
        return "failed"
    if state in _ATTENTION:
        return "attention"
    return "active"


class IssueLifecycleProjection:
    """Track exact last states and completion only across observed transitions."""

    def __init__(self) -> None:
        self.current = "repository"
        self.stages: dict[str, dict[str, Any]] = {
            stage: {"status": "unknown", "state": "", "outcome": "unknown", "evidence_refs": []}
            for stage in STAGES
        }

    def record_repository(self, github_state: str, issue_key: str) -> None:
        """Seed only the repository from this Issue's persisted upstream mirror.

        Historical/manual intake may not have an external_issue.received event.
        The mirror is still evidence of repository presence; it is not evidence
        that Triage, approval, or any later stage has completed.
        """
        if github_state not in {"open", "closed"} or not issue_key:
            return
        self.stages["repository"].update(
            status="recorded", state=github_state, outcome="active",
            evidence_refs=[issue_key],
        )

    def observe(self, previous_state: str, state: str, event_id: str) -> None:
        target = stage_for_state(state, self.current)
        previous = self.current
        if state == "triage_queued" and self.stages["triage"]["state"]:
            # A new attempt invalidates the old attempt's later-stage display,
            # but leaves its event references available for historical inspection.
            for stage in STAGES[2:]:
                self.stages[stage].update(state="", outcome="unknown")
        if previous != target and STAGES.index(previous) < STAGES.index(target):
            entry = self.stages[previous]
            observed_previous = entry["state"] == previous_state or (
                previous == "repository" and entry["state"] in {"open", "closed", "mirrored"}
            )
            if observed_previous and entry["outcome"] in {"active", "attention"} and not previous_state.endswith("_cancelled"):
                entry["outcome"] = "completed"
        entry = self.stages[target]
        # Receipt proves presence, but does not replace the mirror's current
        # upstream Open/Closed state with the internal intake marker.
        exact_state = (
            entry["state"]
            if target == "repository" and state == "mirrored" and entry["state"] in {"open", "closed"}
            else state
        )
        entry.update(status="recorded", state=exact_state, outcome=state_outcome(state))
        if event_id and event_id not in entry["evidence_refs"]:
            entry["evidence_refs"].append(event_id)
        self.current = target

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": "issue-triage-lifecycle.v1", "current": self.current, "stages": self.stages}
