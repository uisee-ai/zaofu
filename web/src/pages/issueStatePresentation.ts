import type { IssueIconName } from "./IssueIcon";
export const ISSUE_STATE_COPY: Record<string, {
    title: string;
    description: string;
    icon: IssueIconName;
}> = {
    "open": {
        "title": "Ready for manual Triage",
        "description": "GitHub metadata is current, but no ZaoFu workflow has been admitted yet.",
        "icon": "clock"
    },
    "closed": {
        "title": "Closed upstream Issue",
        "description": "The Issue remains available in the mirror for history and inspection.",
        "icon": "clock"
    },
    "mirrored": {
        "title": "Mirror only",
        "description": "This historical Issue is synchronized but has no admitted workflow.",
        "icon": "clock"
    },
    "triage_queued": {
        "title": "Triage queued",
        "description": "The Triage request is persisted and waiting for runtime admission. Execution has not started yet.",
        "icon": "clock"
    },
    "triaging": {
        "title": "Triage Run in progress",
        "description": "The agent is collecting evidence and producing a current Fix proposal.",
        "icon": "dot"
    },
    "triage_paused": {
        "title": "Triage paused at checkpoint",
        "description": "New dispatches are blocked; completed work and persisted evidence are retained.",
        "icon": "pause"
    },
    "triage_recovering": {
        "title": "Recovering Triage Run",
        "description": "ZaoFu is reconciling persisted state after a provider or runtime interruption.",
        "icon": "refresh"
    },
    "needs_info": {
        "title": "Additional information required",
        "description": "Triage cannot produce a safe Fix proposal until the missing facts are supplied.",
        "icon": "warn"
    },
    "needs_info_exhausted": {
        "title": "Clarification rounds exhausted",
        "description": "The maximum clarification budget was reached without enough current evidence.",
        "icon": "xcircle"
    },
    "unverified_candidate": {
        "title": "Candidate awaiting maintainer review",
        "description": "The source scope is sufficient, but dynamic verification is incomplete.",
        "icon": "warn"
    },
    "awaiting_fix_approval": {
        "title": "Fix approval required",
        "description": "A current exact proposal exists and must be approved before any write-capable Fix Run starts.",
        "icon": "warn"
    },
    "fix_queued": {
        "title": "Fix queued",
        "description": "The approved write-capable Run is waiting for runtime admission.",
        "icon": "wrench"
    },
    "fixing": {
        "title": "Fix Run in progress",
        "description": "The agent is editing only the approved paths in an isolated worktree.",
        "icon": "wrench"
    },
    "fix_paused": {
        "title": "Fix paused at checkpoint",
        "description": "No new write dispatches will start until the Run is resumed.",
        "icon": "pause"
    },
    "fix_recovering": {
        "title": "Recovering Fix Run",
        "description": "Persisted Task and worktree state are being reconciled after interruption.",
        "icon": "refresh"
    },
    "verifying": {
        "title": "Verification gates running",
        "description": "The exact candidate is being tested before it can become a verified candidate.",
        "icon": "shield"
    },
    "verified_candidate": {
        "title": "Verified candidate",
        "description": "The exact candidate passed the current ZaoFu gates and is ready for Owner review.",
        "icon": "shield"
    },
    "approved_for_pr": {
        "title": "Approved for PR",
        "description": "Owner approval is bound to the exact candidate SHA and source revision.",
        "icon": "shield"
    },
    "owner_changes_requested": {
        "title": "Owner changes requested",
        "description": "The current candidate was reviewed but is not ready for publication.",
        "icon": "warn"
    },
    "owner_rejected": {
        "title": "Candidate rejected",
        "description": "The Owner declined this exact candidate; it cannot be prepared for publication.",
        "icon": "xcircle"
    },
    "publication_prepared": {
        "title": "Human publication handoff ready",
        "description": "The immutable local review branch is pinned to the approved candidate.",
        "icon": "pr"
    },
    "pr_open": {
        "title": "Pull request open",
        "description": "The recorded GitHub PR is synchronized read-only from the configured repository.",
        "icon": "pr"
    },
    "pr_changes_requested": {
        "title": "GitHub review requested changes",
        "description": "The published candidate needs another Fix Run and a new verified candidate.",
        "icon": "warn"
    },
    "pr_approved": {
        "title": "Pull request approved",
        "description": "GitHub review is approved; merge remains an explicit human action.",
        "icon": "check"
    },
    "pr_closed_without_merge": {
        "title": "Pull request closed without merge",
        "description": "The recorded PR is closed and no merge fact was observed.",
        "icon": "xcircle"
    },
    "merged": {
        "title": "Merged on GitHub",
        "description": "Read-only synchronization verified the target repository, base, head identity, and merge fact.",
        "icon": "check"
    },
    "triage_blocked": {
        "title": "Triage blocked",
        "description": "A required runtime capability is unavailable, so the Run cannot continue safely.",
        "icon": "xcircle"
    },
    "triage_cancelled": {
        "title": "Triage cancelled",
        "description": "The queued or active Triage Run was permanently cancelled by an operator.",
        "icon": "xcircle"
    },
    "fix_blocked": {
        "title": "Fix blocked",
        "description": "The candidate attempted to leave the approved TaskContract scope.",
        "icon": "xcircle"
    },
    "fix_cancelled": {
        "title": "Fix Run cancelled",
        "description": "The write-capable Run was permanently cancelled.",
        "icon": "xcircle"
    },
    "blocked": {
        "title": "Workflow blocked by policy",
        "description": "A deterministic gate rejected the requested transition.",
        "icon": "xcircle"
    },
    "failed": {
        "title": "Workflow failed",
        "description": "The retry budget was exhausted and no safe automatic recovery remains.",
        "icon": "xcircle"
    }
};
export function issueStateTone(state: string): "active" | "attention" | "error" | "done" | "neutral" {
    if (state === 'merged')
        return 'done';
    if (['owner_rejected', 'pr_closed_without_merge', 'triage_blocked', 'fix_blocked', 'blocked', 'failed'].includes(state))
        return 'error';
    if (['triage_paused', 'triage_recovering', 'needs_info', 'needs_info_exhausted', 'unverified_candidate', 'awaiting_fix_approval', 'fix_paused', 'fix_recovering', 'verified_candidate', 'owner_changes_requested', 'publication_prepared', 'pr_changes_requested', 'pr_approved', 'triage_cancelled', 'fix_cancelled'].includes(state))
        return 'attention';
    if (['closed', 'mirrored'].includes(state))
        return 'neutral';
    return 'active';
}
