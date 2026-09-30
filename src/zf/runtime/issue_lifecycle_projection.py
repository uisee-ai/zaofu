"""Shared read-only Issue lifecycle projection for Web and label reconciliation."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from zf.core.events import ZfEvent
from zf.core.issue_triage.models import IssueMirror
from zf.core.issue_triage.lifecycle_states import REMOTE_LIFECYCLE_STATE_SET
from zf.runtime.issue_candidate_delivery import issue_candidate_event_matches
from zf.web.issue_lifecycle import IssueLifecycleProjection


def issue_lifecycle_event_index(
    events: list[ZfEvent], issue_keys: set[str],
) -> dict[str, list[ZfEvent]]:
    """Index identities once per project, preserving ledger order per Issue.

    This is only a read optimization; the reducer still checks the active Run.
    Historical identities are included so retries and stale events have exactly
    the same semantics as reducing the full ledger.
    """
    relevant = [event for event in events if event.type.startswith((
        "external_issue.", "workflow.invoke.", "run.", "candidate.",
        "forge.pull_request.",
    )) or event.type.endswith(".failed") or event.type in {
        "operator.action.proposed", "task.dispatched", "fanout.child.dispatched",
        "fanout.child.dispatch_deferred", "judge.passed", "delivery.merged",
    }]
    tasks: dict[str, set[str]] = defaultdict(set)
    runs: dict[str, set[str]] = defaultdict(set)

    def identities(event: ZfEvent) -> tuple[set[str], str, str]:
        payload = event.payload
        task_ids = {str(value) for value in (
            event.task_id, payload.get("task_id"), payload.get("parent_task_id"),
            payload.get("owner_task_id"),
        ) if value}
        run = str(payload.get("workflow_run_id") or payload.get("run_id")
                  or payload.get("trace_id") or event.correlation_id or "")
        return task_ids, run, str(payload.get("source_key") or "")

    indexed = [(event, *identities(event)) for event in relevant]
    for _, task_ids, run, source in indexed:
        if source not in issue_keys:
            continue
        for task in task_ids:
            tasks[task].add(source)
        if run:
            runs[run].add(source)
    # Fix requests can inherit the Task without repeating source_key. Register
    # their Run before selecting taskless candidate/judge/PR events.
    for event, task_ids, run, _ in indexed:
        if event.type == "workflow.invoke.requested" and run:
            for task in task_ids:
                runs[run].update(tasks.get(task, ()))
    result: dict[str, list[ZfEvent]] = defaultdict(list)
    for event, task_ids, run, source in indexed:
        keys = {source} if source in issue_keys else set()
        for task in task_ids:
            keys.update(tasks.get(task, ()))
        keys.update(runs.get(run, ()))
        for key in keys:
            result[key].append(event)
    return dict(result)


# Stage-level ``*.failed`` events describe a failed attempt or a rework
# decision.  They do not release the workflow's run slot and therefore must
# not make the Issue look restartable.  Only these run/admission events end the
# current Triage Run for the mirror projection.
_TERMINAL_RUN_FAILURE_EVENTS = {
    "run.failed",
    "run.abandoned",
}

_TRIAGE_ACTIVE_STATES = {
    "triage_queued", "triaging", "triage_paused", "triage_recovering",
}
_FIX_ACTIVE_STATES = {
    "fix_queued", "fixing", "fix_paused", "fix_recovering", "verifying",
}

_DISPATCH_RECOVERY_REASON_PREFIXES = (
    "worker_transport_not_alive",
    "worker_state_not_dispatchable:respawning",
    "worker_state_not_dispatchable:pending_recycle",
    "role_activation_failed:",
    "dispatch_preparation",
)


def _is_dispatch_recovery_reason(reason: str) -> bool:
    """Return whether a dispatch delay represents runtime recovery.

    Busy workers, active provider turns, and the short briefing de-duplication
    window are ordinary queueing. A dead/restarting transport or failed
    dispatch preparation is different: the operator needs the exact reason,
    while the Run remains active and self-healing.
    """

    return any(
        part.strip().startswith(_DISPATCH_RECOVERY_REASON_PREFIXES)
        for part in reason.split(";")
    )


def _remote_lifecycle_state(item: IssueMirror) -> str | None:
    """Read the public lifecycle label from the remote Issue mirror.

    A developer may open a different project/state directory from the one
    that emitted the original events.  In that case the remote label remains
    the cross-project authority and is used as the projection fallback (and
    final override) rather than inventing ``mirrored``/``triage_queued`` from
    an empty local ledger.
    """

    for raw in item.labels:
        state = str(raw or "").strip()
        if state in REMOTE_LIFECYCLE_STATE_SET:
            return state
    return None


def _workflow_projection(
    events: list[ZfEvent],
    item: IssueMirror,
    *,
    prefer_remote: bool = True,
) -> dict[str, Any] | None:
    remote_lifecycle_state = _remote_lifecycle_state(item)
    state = "mirrored"
    task_id = ""
    source_revision = ""
    source_code_revision = ""
    proposal_id = ""
    run_id = ""
    workflow_id = ""
    fix_run_id = ""
    paused_from = ""
    terminal_state = ""
    failure_event = ""
    failure_reason = ""
    candidate_identities: tuple[str, ...] = ()
    clarification_ref: dict[str, Any] | None = None
    clarification_request_status = ""
    clarification_request_url = ""
    disposition: str = ""
    verification_status = ""
    verification_gaps: list[str] = []
    seen = False
    state_event_id = ""
    state_event_at = ""
    admitted_at = ""
    observed_states: set[str] = set()
    triage_history: list[dict[str, Any]] = []
    triage_by_id: dict[str, dict[str, Any]] = {}
    task_history: list[dict[str, str]] = []
    task_ids: set[str] = set()
    lifecycle_projection = IssueLifecycleProjection()
    lifecycle_projection.record_repository(item.github_state, item.issue_key)
    for event in events:
        previous_state = state
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_source_key = str(payload.get("source_key") or "")
        same_source = event_source_key == item.issue_key
        same_task = bool(task_id and event.task_id == task_id)
        event_run = str(payload.get("workflow_run_id") or payload.get("run_id") or event.correlation_id or "")
        if event_run.startswith("external-issue:"):
            event_run = ""  # Issue correlation is not a workflow Run identity.
        admission = event.type in {"external_issue.triage.queued", "external_issue.fix.queued", "workflow.invoke.requested"}
        # Cleanup, review and PR events from a prior run must not change the
        # current attempt, even when the Task is reused on retry.
        if not admission and run_id and event_run and event_run != run_id and (
            same_task or same_source
        ) and event.type != "external_issue.received":
            continue
        if terminal_state and not admission:
            continue
        if same_source and event.type in {"external_issue.triage.queued_cancelled", "external_issue.fix.queued_cancelled"} and not seen:
            seen = True
            run_id = event_run
            task_id = str(event.task_id or "")
            observed_states.add("fix_queued" if event.type.endswith("fix.queued_cancelled") else "triage_queued")
        if event.type == "external_issue.received" and seen and run_id:
            continue
        if event.type == "external_issue.received" and same_source:
            seen = True
            state = "mirrored"
            source_revision = str(payload.get("source_revision") or "")
        elif same_source and (
            event.type == "external_issue.triage.queued"
            or (event.type == "workflow.invoke.requested" and payload.get("flow_kind") != "issue")
        ):
            if run_id and event_run == run_id and state != "triage_queued":
                continue
            seen = True
            state = "triage_queued"
            terminal_state = ""
            failure_event = ""
            failure_reason = ""
            task_id = str(event.task_id or payload.get("task_id") or task_id)
            source_revision = str(payload.get("source_revision") or source_revision)
            if task_id and task_id not in task_ids:
                task_ids.add(task_id)
                task_history.append({
                    "task_id": task_id,
                    "source_revision": source_revision,
                })
            next_run = str(payload.get("workflow_run_id") or payload.get("run_id") or "")
            if next_run != run_id or not admitted_at:
                admitted_at = event.ts
            run_id = next_run
            fix_run_id = ""
            candidate_identities = ()
            workflow_id = str(payload.get("workflow_id") or payload.get("pattern_id") or workflow_id)
            source_code_revision = str(payload.get("source_code_revision") or payload.get("target_commit") or source_code_revision)
            if run_id and run_id not in triage_by_id:
                entry = {
                    "triage_id": run_id,
                    "source_revision": str(payload.get("source_revision") or ""),
                    "attempt": int(payload.get("triage_attempt") or len(triage_history) + 1),
                    "state": "triage_queued",
                    "queued_at": event.ts,
                    "retry_of": str(payload.get("retry_of_workflow_run_id") or ""),
                }
                triage_history.append(entry)
                triage_by_id[run_id] = entry
        elif event.type == "workflow.invoke.accepted" and (same_task or (run_id and event_run == run_id)):
            event_flow = str(payload.get("flow_kind") or "")
            event_run = str(
                payload.get("workflow_run_id") or event.correlation_id or ""
            )
            if not run_id or event_run == run_id:
                # ``workflow.invoke.accepted`` is emitted for both the
                # read-only Triage flow and the write-capable Fix flow.  A
                # generic accepted handler used to overwrite ``fix_queued``
                # with ``triaging`` when the Fix acceptance arrived after
                # the request event.  Keep the two lanes distinct and fail
                # closed when an old event has no flow identity.
                current_fix_run = bool(fix_run_id and event_run == fix_run_id)
                is_fix_accept = event_flow == "issue" or current_fix_run or (
                    not event_flow
                    and (bool(fix_run_id) or state in _FIX_ACTIVE_STATES)
                )
                if is_fix_accept:
                    if state not in {"fixing", "verifying", "verified_candidate"}:
                        state = "fix_queued"
                    fix_run_id = event_run or fix_run_id or run_id
                elif event_flow == "workflow" or not event_flow:
                    state = "triaging"
                run_id = event_run or run_id
                workflow_id = str(
                    payload.get("workflow_id")
                    or payload.get("pattern_id")
                    or workflow_id
                )
                if not is_fix_accept and event_run in triage_by_id:
                    triage_by_id[event_run]["state"] = "triaging"
        elif event.type in {
            "external_issue.triage.needs_info",
            "external_issue.triage.needs_info_exhausted",
        } and same_source:
            event_run = str(payload.get("workflow_run_id") or event.correlation_id or "")
            if not run_id or not event_run or event_run == run_id:
                state = (
                    "needs_info_exhausted"
                    if event.type.endswith("needs_info_exhausted")
                    else "needs_info"
                )
                run_id = event_run or run_id
                raw_ref = payload.get("clarification_ref")
                clarification_ref = dict(raw_ref) if isinstance(raw_ref, dict) else None
                if run_id in triage_by_id:
                    triage_by_id[run_id]["state"] = state
                disposition = state
        elif event.type in {
            "external_issue.clarification.request_published",
            "external_issue.clarification.request_pending",
        } and same_source:
            clarification_request_status = str(payload.get("status") or (
                "published" if event.type.endswith("published") else "pending"
            ))
            clarification_request_url = str(payload.get("comment_url") or "")
        elif event.type == "external_issue.triage.unverified_candidate" and same_source:
            event_run = str(payload.get("workflow_run_id") or event.correlation_id or "")
            if not run_id or not event_run or event_run == run_id:
                state = "unverified_candidate"
                run_id = event_run or run_id
                clarification_ref = None
                disposition = state
                verification_status = str(payload.get("verification_status") or "")
                verification_gaps = [
                    str(item) for item in payload.get("verification_gaps") or []
                    if str(item).strip()
                ]
                if run_id in triage_by_id:
                    triage_by_id[run_id]["state"] = state
        elif event.type == "external_issue.triage.ready_to_fix" and same_source:
            # A verified artifact-delivery run may be rejected by the generic
            # Goal completion projection (for example when a legacy claim is
            # replayed), but the External Issue disposition is still a valid
            # triage handoff.  Project it into the approval state so the
            # operator can review the generated Fix proposal instead of
            # leaving the Issue stuck at ``triaging``.
            event_run = str(payload.get("workflow_run_id") or event.correlation_id or "")
            if not run_id or not event_run or event_run == run_id:
                state = "awaiting_fix_approval"
                run_id = event_run or run_id
                clarification_ref = None
                disposition = "ready_to_fix"
                if run_id in triage_by_id:
                    triage_by_id[run_id]["state"] = state
        elif event.type == "operator.action.proposed" and same_task:
            proposal = payload.get("proposal")
            proposal = proposal if isinstance(proposal, dict) else {}
            proposal_payload = proposal.get("payload")
            proposal_payload = (
                proposal_payload if isinstance(proposal_payload, dict) else {}
            )
            if str(proposal_payload.get("route_id") or "").startswith(
                "delivery:issue:"
            ):
                # Once a Fix workflow request has been submitted, a later
                # poll may replay the same delivery proposal.  That replay
                # must not regress the Issue from ``fix_queued`` back to the
                # approval state while its Run is waiting for admission.
                if not fix_run_id:
                    state = "awaiting_fix_approval"
                    proposal_id = str(proposal.get("proposal_id") or "")
                    clarification_ref = None
        elif event.type == "run.goal.completed":
            # A successful External Issue workflow has no separate semantic
            # "triage completed" event. Once the kernel-admitted run reaches
            # its terminal success, the Issue must leave ``triaging`` so the
            # operator can refresh and review the generated Fix proposal.
            event_run = str(
                payload.get("workflow_run_id") or payload.get("run_id")
                or event.correlation_id or ""
            )
            if (
                state in _TRIAGE_ACTIVE_STATES
                and event_run
                and (same_task or not run_id or event_run == run_id)
            ):
                state = "awaiting_fix_approval"
                run_id = event_run
                clarification_ref = None
                disposition = ""
                if run_id in triage_by_id:
                    triage_by_id[run_id]["state"] = state
        elif event.type in {"workflow.invoke.requested", "external_issue.fix.queued"} and (same_task or same_source):
            if str(payload.get("flow_kind") or "") == "issue" or event.type == "external_issue.fix.queued":
                if fix_run_id and event_run == fix_run_id and state != "fix_queued":
                    continue
                seen = True
                task_id = str(event.task_id or payload.get("task_id") or task_id)
                state = "fix_queued"
                fix_run_id = str(payload.get("workflow_run_id") or run_id)
                if fix_run_id != run_id or not admitted_at:
                    admitted_at = event.ts
                run_id = fix_run_id or run_id
                terminal_state = ""
            elif str(payload.get("flow_kind") or "") == "workflow":
                workflow_id = str(
                    payload.get("workflow_id")
                    or payload.get("pattern_id")
                    or workflow_id
                )
        elif event.type == "run.cancelled":
            cancelled_run_id = str(
                payload.get("workflow_run_id") or payload.get("run_id")
                or event.correlation_id or ""
            )
            if not terminal_state and (same_task or (run_id and cancelled_run_id == run_id)) and (
                not cancelled_run_id or not run_id or cancelled_run_id == run_id
            ):
                state = (
                    "fix_cancelled"
                    if state in _FIX_ACTIVE_STATES
                    else "triage_cancelled"
                )
                terminal_state = state
                if cancelled_run_id in triage_by_id:
                    triage_by_id[cancelled_run_id]["state"] = "triage_cancelled"
        elif event.type in {"external_issue.triage.queued_cancelled", "external_issue.fix.queued_cancelled"}:
            cancelled_run_id = str(payload.get("workflow_run_id") or payload.get("run_id") or event.correlation_id or "")
            if not terminal_state and cancelled_run_id and (same_task or cancelled_run_id == run_id):
                state = "fix_cancelled" if event.type.endswith("fix.queued_cancelled") else "triage_cancelled"
                terminal_state = state
                if cancelled_run_id in triage_by_id:
                    triage_by_id[cancelled_run_id]["state"] = state
        elif event.type in {"workflow.invoke.rejected", "run.admission.rejected"}:
            rejected_run_id = str(
                payload.get("workflow_run_id") or payload.get("run_id")
                or event.correlation_id or ""
            )
            if not terminal_state and (same_task or (run_id and rejected_run_id == run_id)) and (
                not rejected_run_id or not run_id or rejected_run_id == run_id
            ):
                # A rejected admission is terminal.  Do not leave the mirror
                # looking queued (which incorrectly exposes Cancel queued and
                # then fails with rejected -> run-cancel).
                state = "failed"
                terminal_state = state
                run_id = rejected_run_id or run_id
                if rejected_run_id in triage_by_id:
                    triage_by_id[rejected_run_id]["state"] = "failed"
        elif event.type == "run.paused":
            event_run = str(
                payload.get("workflow_run_id") or payload.get("run_id")
                or event.correlation_id or ""
            )
            if not terminal_state and (same_task or (run_id and event_run == run_id)) and (
                not event_run or not run_id or event_run == run_id
            ):
                paused_from = state
                state = (
                    "fix_paused"
                    if state in _FIX_ACTIVE_STATES
                    else "triage_paused"
                )
        elif event.type == "run.resumed":
            event_run = str(
                payload.get("workflow_run_id") or payload.get("run_id")
                or event.correlation_id or ""
            )
            if not terminal_state and (same_task or (run_id and event_run == run_id)) and (
                not event_run or not run_id or event_run == run_id
            ):
                state = paused_from or (
                    "fixing" if state == "fix_paused" else "triaging"
                )
                paused_from = ""
        elif event.type == "fanout.child.dispatch_deferred":
            dispatch_run = str(
                payload.get("workflow_run_id")
                or payload.get("run_id")
                or event.correlation_id
                or ""
            )
            reason = str(payload.get("reason") or "").strip()
            if (
                not terminal_state
                and _is_dispatch_recovery_reason(reason)
                and state in _TRIAGE_ACTIVE_STATES | _FIX_ACTIVE_STATES
                and (same_task or (dispatch_run and dispatch_run == run_id))
                and (not dispatch_run or not run_id or dispatch_run == run_id)
            ):
                failure_event = event.type
                failure_reason = reason
                state = (
                    "fix_recovering"
                    if state in _FIX_ACTIVE_STATES
                    else "triage_recovering"
                )
        elif event.type in {"task.dispatched", "fanout.child.dispatched"}:
            dispatch_parent = str(
                payload.get("parent_task_id")
                or payload.get("task_id")
                or payload.get("owner_task_id")
                or ""
            )
            dispatch_run = str(
                payload.get("workflow_run_id")
                or payload.get("run_id")
                or event.correlation_id
                or ""
            )
            dispatch_matches = (
                same_task
                or (dispatch_parent and dispatch_parent == task_id)
                or (dispatch_run and dispatch_run in {run_id, fix_run_id})
            )
            if dispatch_matches and (not dispatch_run or not run_id or dispatch_run == run_id) and state in {
                "fix_queued", "fixing", "fix_recovering",
            }:
                state = "fixing"
                failure_event = ""
                failure_reason = ""
        elif event.type == "candidate.ready" and issue_candidate_event_matches(
            event,
            task_id=task_id,
            workflow_run_id=run_id,
            require_candidate_identity=True,
        ):
            state = "verifying"
            candidate_identities = tuple(
                str(payload.get(key) or "").strip()
                for key in ("pdd_id", "feature_id")
                if str(payload.get(key) or "").strip()
            )
        elif event.type == "judge.passed" and issue_candidate_event_matches(
            event,
            task_id=task_id,
            workflow_run_id=run_id,
            candidate_identities=candidate_identities,
        ):
            state = "verified_candidate"
        elif event.type == "candidate.owner_review.approved" and same_task:
            state = "approved_for_pr"
        elif event.type == "candidate.owner_review.changes_requested" and same_task:
            state = "owner_changes_requested"
        elif event.type == "candidate.owner_review.declined" and same_task:
            state = "owner_rejected"
        elif event.type == "candidate.publication.prepared" and same_task:
            state = "publication_prepared"
        elif event.type in {"forge.pull_request.recorded", "forge.pull_request.synced"} and same_task:
            state = str(payload.get("status") or "pr_open")
        elif event.type == "delivery.merged" and same_task:
            state = "merged"
        elif event.type == "run.goal.blocked":
            event_run = str(
                payload.get("workflow_run_id") or payload.get("run_id")
                or event.correlation_id or ""
            )
            if not terminal_state and (same_task or (run_id and event_run == run_id)) and (
                not event_run or not run_id or event_run == run_id
            ):
                state = (
                    "fix_blocked" if state in _FIX_ACTIVE_STATES
                    else "triage_blocked" if state in _TRIAGE_ACTIVE_STATES
                    else "blocked"
                )
                terminal_state = state
                failure_event = event.type
                failure_reason = str(payload.get("reason") or "")
                if run_id in triage_by_id:
                    triage_by_id[run_id]["state"] = state
        elif event.type in _TERMINAL_RUN_FAILURE_EVENTS:
            event_run = str(
                payload.get("workflow_run_id") or payload.get("run_id")
                or event.correlation_id or ""
            )
            if not terminal_state and (same_task or (run_id and event_run == run_id)) and (
                not event_run or not run_id or event_run == run_id
            ):
                state = "failed"
                terminal_state = state
                failure_event = event.type
                failure_reason = str(payload.get("reason") or "")
                if run_id in triage_by_id:
                    triage_by_id[run_id]["state"] = "failed"
        elif event.type.endswith(".failed") and same_task:
            # A stage/attempt failure may schedule recovery while the parent
            # Run remains active.  Surface that distinction to operators so
            # they use Manage Run instead of incorrectly starting a duplicate.
            event_run = str(
                payload.get("workflow_run_id") or payload.get("run_id")
                or event.correlation_id or ""
            )
            if not terminal_state and (
                not event_run or not run_id or event_run == run_id
            ):
                failure_event = event.type
                failure_reason = str(payload.get("reason") or "")
                if state in _TRIAGE_ACTIVE_STATES:
                    state = "triage_recovering"
                elif state in _FIX_ACTIVE_STATES:
                    state = "fix_recovering"
        if seen and (not terminal_state or state == terminal_state) and (state != previous_state or (same_source and (admission or event.type == "external_issue.received"))):
            state_event_id = event.id
            state_event_at = event.ts
            observed_states.add(state)
            lifecycle_projection.observe(previous_state, state, event.id)
    if not seen and (not prefer_remote or remote_lifecycle_state is None):
        return None
    if terminal_state:
        # Once the current Run has emitted a terminal event, later diagnostic
        # or cleanup failures cannot reopen it as a recovering Run.
        state = terminal_state
    local_queue_state = state
    queued_cancel_available = bool(run_id) and state in {"triage_queued", "fix_queued"}
    if prefer_remote and remote_lifecycle_state is not None and not terminal_state:
        # Remote lifecycle labels are public authority.  Local events may be
        # absent (another project) or stale (a delayed poll); neither may
        # overwrite a newer remote state in the developer-facing projection.
        state = remote_lifecycle_state
        lifecycle_projection.observe(
            lifecycle_projection.current,
            remote_lifecycle_state,
            f"remote-label:{item.issue_key}:{remote_lifecycle_state}",
        )
    lifecycle = lifecycle_projection.to_dict()
    return {
        **({"observed_states": sorted(observed_states)} if not prefer_remote else {}),
        "state": state,
        "state_event_id": state_event_id,
        "state_event_at": state_event_at,
        "admitted_at": admitted_at,
        "remote_lifecycle_state": remote_lifecycle_state or "",
        "task_id": task_id,
        "task_history": list(reversed(task_history)),
        "source_revision": source_revision,
        "source_code_revision": source_code_revision,
        "proposal_id": proposal_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "triage_id": run_id,
        "queued_cancel_available": queued_cancel_available and state == local_queue_state,
        "triage_history": list(reversed(triage_history)),
        "failure_event": failure_event,
        "failure_reason": failure_reason,
        "clarification_ref": clarification_ref,
        "clarification_request_status": clarification_request_status,
        "clarification_request_url": clarification_request_url,
        "disposition": disposition,
        "verification_status": verification_status,
        "verification_gaps": verification_gaps,
        "lifecycle": lifecycle,
    }
