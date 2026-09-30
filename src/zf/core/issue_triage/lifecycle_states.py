"""Canonical remote lifecycle state/label contract for Issue Triage.

The remote Forge label is the public state.  Keep this registry provider
neutral so projections, reconciliation and tests cannot drift into separate
lists of "known" states.
"""

from __future__ import annotations

from collections.abc import Iterable


REMOTE_LIFECYCLE_STATES: tuple[str, ...] = (
    "open",
    "closed",
    "mirrored",
    "triage_queued",
    "triaging",
    "triage_paused",
    "triage_recovering",
    "triage_blocked",
    "triage_cancelled",
    "needs_info",
    "needs_info_exhausted",
    "unverified_candidate",
    "awaiting_fix_approval",
    "fix_queued",
    "fixing",
    "fix_paused",
    "fix_recovering",
    "fix_blocked",
    "fix_cancelled",
    "verifying",
    "verified_candidate",
    "approved_for_pr",
    "owner_changes_requested",
    "owner_rejected",
    "publication_prepared",
    "pr_open",
    "pr_changes_requested",
    "pr_approved",
    "pr_closed_without_merge",
    "merged",
    "blocked",
    "failed",
)

REMOTE_LIFECYCLE_STATE_SET = frozenset(REMOTE_LIFECYCLE_STATES)


def validate_lifecycle_state(state: str) -> str:
    """Normalize and validate one public lifecycle state."""

    normalized = str(state or "").strip()
    if normalized not in REMOTE_LIFECYCLE_STATE_SET:
        raise ValueError(f"unknown remote lifecycle state: {normalized!r}")
    return normalized


def reconcile_lifecycle_labels(
    existing_labels: Iterable[str],
    desired_state: str,
) -> tuple[str, ...]:
    """Return labels with exactly one lifecycle label and all metadata kept.

    Only labels in the canonical registry are managed.  User labels such as
    ``bug``, ``area:*`` and ``priority:*`` pass through unchanged.
    """

    desired = validate_lifecycle_state(desired_state)
    result: list[str] = []
    seen: set[str] = set()
    for raw in existing_labels:
        label = str(raw or "").strip()
        if not label or label in REMOTE_LIFECYCLE_STATE_SET:
            continue
        if label not in seen:
            seen.add(label)
            result.append(label)
    if desired not in seen:
        result.append(desired)
    return tuple(result)


__all__ = [
    "REMOTE_LIFECYCLE_STATES",
    "REMOTE_LIFECYCLE_STATE_SET",
    "reconcile_lifecycle_labels",
    "validate_lifecycle_state",
]
