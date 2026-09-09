"""Read-only GitHub Issue Triage mirror routes and optional webhook ingress."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse, Response

from zf.core.config.schema import ZfConfig
from zf.core.config.self_issue_policy import inherit_workspace_self_issue_config
from zf.core.events.factory import event_log_from_project
from zf.core.events.model import ZfEvent
from zf.core.events.segments import build_event_manifest
from zf.core.issue_triage.models import IssueMirror
from zf.core.issue_triage.store import IssueMirrorStore
from zf.integrations.forge.github_issues import (
    GitHubIssueReconciler,
    normalize_github_issue,
)
from zf.runtime.external_issue_ingress import ExternalIssueIngressService
from zf.runtime.external_issue_clarification import (
    hydrate_external_issue_clarification,
)
from zf.runtime.issue_candidate_delivery import (
    CandidateDeliveryError,
    IssueCandidateDeliveryService,
    issue_candidate_event_matches,
)

ReconcilerFactory = Callable[[Any, str], GitHubIssueReconciler]
MutationAuthorizer = Callable[..., dict[str, Any] | None]
SessionCookieReader = Callable[[Request], str | None]
DeliveryServiceFactory = Callable[[Any, ZfConfig], IssueCandidateDeliveryService]

_GITHUB_IMAGE_HOSTS = {
    "github.com",
    "user-images.githubusercontent.com",
    "camo.githubusercontent.com",
}
_MAX_GITHUB_IMAGE_BYTES = 20_000_000

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


def _is_github_image_url(value: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _GITHUB_IMAGE_HOSTS
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    return parsed.hostname != "github.com" or parsed.path.startswith("/user-attachments/")


def _read_github_image(value: str) -> tuple[bytes, str]:
    request = urllib.request.Request(
        value,
        headers={"Accept": "image/*", "User-Agent": "ZaoFu-Issue-Triage"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0]
        if not content_type.startswith("image/"):
            raise ValueError("GitHub attachment is not an image")
        raw = response.read(_MAX_GITHUB_IMAGE_BYTES + 1)
    if len(raw) > _MAX_GITHUB_IMAGE_BYTES:
        raise ValueError("GitHub image exceeds the 20 MB safe limit")
    return raw, content_type


def _repository(config: ZfConfig | None) -> str:
    if config is None or not config.self_issue.enabled:
        return ""
    target = config.self_issue.targets.get("github")
    if target is not None:
        return target.project
    if config.self_issue.provider == "github":
        return config.self_issue.target_project
    return ""


def _parse_issue_reference(value: object, repository: str) -> int:
    text = str(value or "").strip()
    if text.startswith("#"):
        text = text[1:].strip()
    if text.isdigit():
        number = int(text)
    else:
        if len(text) > 500:
            raise ValueError("GitHub Issue reference is too long")
        try:
            parsed = urllib.parse.urlparse(text)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Enter a GitHub Issue URL or number") from exc
        parts = [part for part in parsed.path.split("/") if part]
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or len(parts) != 4
            or parts[2] != "issues"
            or not parts[3].isdigit()
            or f"{parts[0]}/{parts[1]}".casefold() != repository.casefold()
        ):
            raise ValueError(f"Enter an Issue URL or number from {repository}")
        number = int(parts[3])
    if number < 1 or number > 2_147_483_647:
        raise ValueError("GitHub Issue number is outside the supported range")
    return number


def _public(
    item: IssueMirror,
    *,
    workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = item.to_dict()
    if workflow is not None:
        value["workflow"] = workflow
    return value


def _workflow_projection(
    events: list[ZfEvent],
    item: IssueMirror,
) -> dict[str, Any] | None:
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
    disposition: str = ""
    verification_status = ""
    verification_gaps: list[str] = []
    seen = False
    triage_history: list[dict[str, Any]] = []
    triage_by_id: dict[str, dict[str, Any]] = {}
    task_history: list[dict[str, str]] = []
    task_ids: set[str] = set()
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_source_key = str(payload.get("source_key") or "")
        same_source = event_source_key == item.issue_key
        same_task = bool(task_id and event.task_id == task_id)
        if event.type == "external_issue.received" and same_source:
            seen = True
            state = "mirrored"
            source_revision = str(payload.get("source_revision") or "")
        elif event.type == "external_issue.triage.queued" and same_source:
            seen = True
            state = "triage_queued"
            terminal_state = ""
            failure_event = ""
            failure_reason = ""
            task_id = str(event.task_id or "")
            source_revision = str(payload.get("source_revision") or source_revision)
            if task_id and task_id not in task_ids:
                task_ids.add(task_id)
                task_history.append({
                    "task_id": task_id,
                    "source_revision": source_revision,
                })
            run_id = str(payload.get("workflow_run_id") or "")
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
        elif (
            event.type == "workflow.invoke.requested"
            and same_source
            and str(payload.get("flow_kind") or "") == "workflow"
        ):
            # External Issue intake pins the exact committed ZaoFu checkout
            # separately from the Issue-content revision.  Keep that binding
            # visible in the read projection so the UI cannot imply that the
            # human-entered version string is a source commit.
            task_id = str(payload.get("task_id") or task_id)
            source_code_revision = str(
                payload.get("source_code_revision")
                or payload.get("target_commit")
                or source_code_revision
            )
            workflow_id = str(
                payload.get("workflow_id")
                or payload.get("pattern_id")
                or workflow_id
            )
        elif event.type == "workflow.invoke.accepted" and same_task:
            event_run = str(
                payload.get("workflow_run_id") or event.correlation_id or ""
            )
            if not run_id or event_run == run_id:
                state = "triaging"
                run_id = event_run or run_id
                workflow_id = str(
                    payload.get("workflow_id")
                    or payload.get("pattern_id")
                    or workflow_id
                )
                if event_run in triage_by_id:
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
        elif event.type == "workflow.invoke.requested" and same_task:
            if str(payload.get("flow_kind") or "") == "issue":
                state = "fix_queued"
                fix_run_id = str(payload.get("workflow_run_id") or run_id)
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
                    if state in {"fix_queued", "fixing", "verifying"}
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
        elif (
            event.type in {"task.dispatched", "fanout.child.dispatched"}
            and same_task
            and state in {"fix_queued", "fixing"}
        ):
            state = "fixing"
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
    if not seen:
        return None
    if terminal_state:
        # Once the current Run has emitted a terminal event, later diagnostic
        # or cleanup failures cannot reopen it as a recovering Run.
        state = terminal_state
    return {
        "state": state,
        "task_id": task_id,
        "task_history": list(reversed(task_history)),
        "source_revision": source_revision,
        "source_code_revision": source_code_revision,
        "proposal_id": proposal_id,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "triage_id": run_id,
        "triage_history": list(reversed(triage_history)),
        "failure_event": failure_event,
        "failure_reason": failure_reason,
        "clarification_ref": clarification_ref,
        "disposition": disposition,
        "verification_status": verification_status,
        "verification_gaps": verification_gaps,
    }


def _matches_issue_states(
    item: IssueMirror,
    workflow: dict[str, Any] | None,
    requested: set[str] | None,
) -> bool:
    if requested is None:
        return True
    if not requested:
        return False
    workflow_state = str(
        (workflow or {}).get("state") or "mirrored"
    ).casefold()
    return bool(requested & {item.github_state.casefold(), workflow_state})


def _matches_issue_query(
    item: IssueMirror,
    workflow: dict[str, Any] | None,
    needle: str,
) -> bool:
    if not needle:
        return True
    workflow = workflow or {}
    identifiers = [
        str(workflow.get("task_id") or ""),
        str(workflow.get("triage_id") or ""),
    ]
    for entry in workflow.get("triage_history") or []:
        if isinstance(entry, dict):
            identifiers.append(str(entry.get("triage_id") or ""))
    for entry in workflow.get("task_history") or []:
        if isinstance(entry, dict):
            identifiers.append(str(entry.get("task_id") or ""))
    return any(
        needle in value.casefold()
        for value in [
            item.title,
            item.author_login,
            str(item.number),
            *item.labels,
            *identifiers,
        ]
    )


def build_issue_triage_router(
    *,
    resolve_ctx: Callable[[str], Any],
    workspace_config: ZfConfig | None = None,
    reconciler_factory: ReconcilerFactory | None = None,
    webhook_secret: Callable[[], str] | None = None,
    mutation_auth_error: MutationAuthorizer | None = None,
    session_cookie: SessionCookieReader | None = None,
    delivery_service_factory: DeliveryServiceFactory | None = None,
) -> APIRouter:
    router = APIRouter()
    build_reconciler = reconciler_factory or (
        lambda state_dir, repository: GitHubIssueReconciler(state_dir, repository)
    )
    read_webhook_secret = webhook_secret or (
        lambda: os.environ.get("ZF_GITHUB_TRIAGE_WEBHOOK_SECRET", "").strip()
    )
    read_session_cookie = session_cookie or (lambda request: None)
    build_delivery_service = delivery_service_factory or (
        lambda ctx, config: IssueCandidateDeliveryService(
            state_dir=ctx.state_dir,
            project_root=getattr(ctx, "project_root", ctx.state_dir.parent),
            config=config,
        )
    )
    projection_cache: dict[
        str,
        tuple[tuple[int, int, str], dict[str, dict[str, Any] | None]],
    ] = {}
    projection_cache_lock = threading.Lock()

    def effective_config(ctx: Any) -> ZfConfig | None:
        project_config = getattr(ctx, "config", None)
        return (
            inherit_workspace_self_issue_config(project_config, workspace_config)
            or workspace_config
        )

    def context(project_id: str) -> tuple[Any, str, IssueMirrorStore]:
        ctx = resolve_ctx(project_id)
        repository = _repository(effective_config(ctx))
        if not repository:
            raise ValueError("The centrally managed GitHub Self-Issue target is not configured")
        return ctx, repository, IssueMirrorStore(ctx.state_dir)

    def workflow_projections(
        ctx: Any,
        store: IssueMirrorStore,
        items: list[IssueMirror],
    ) -> dict[str, dict[str, Any] | None]:
        try:
            issues_stat = store.issues_path.stat()
            issues_fingerprint = (int(issues_stat.st_size), int(issues_stat.st_mtime_ns))
        except OSError:
            issues_fingerprint = (0, 0)
        fingerprint = (
            *issues_fingerprint,
            build_event_manifest(ctx.state_dir).digest,
        )
        cache_key = str(ctx.state_dir.resolve())
        with projection_cache_lock:
            cached = projection_cache.get(cache_key)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]
            events = event_log_from_project(
                ctx.state_dir,
                config=effective_config(ctx),
            ).read_all()
            projected = {
                item.issue_key: _workflow_projection(events, item)
                for item in items
            }
            projection_cache[cache_key] = (fingerprint, projected)
            if len(projection_cache) > 8:
                projection_cache.pop(next(iter(projection_cache)))
            return projected

    def mutation_response(
        request: Request,
        authorization: str | None,
        token: str | None,
        *,
        action: str = "issue-triage-start",
    ) -> JSONResponse | None:
        if mutation_auth_error is None:
            return JSONResponse({
                "ok": False,
                "status": "disabled",
                "action": action,
                "reason": "Issue Triage mutation authorization is not configured",
            }, status_code=503)
        error = mutation_auth_error(
            action,
            authorization=authorization,
            x_zf_web_token=token,
            web_session_token=read_session_cookie(request),
        )
        if error is None:
            return None
        body = dict(error)
        return JSONResponse(body, status_code=int(body.pop("_status_code", 403)))

    @router.get("/api/projects/{project_id}/issue-triage/summary")
    def issue_triage_summary(project_id: str) -> JSONResponse:
        try:
            ctx, repository, store = context(project_id)
            items = store.list()
            sync = store.sync_state()
            workflows = workflow_projections(ctx, store, items)
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "unconfigured", "reason": str(exc)}, status_code=409)
        groups = Counter(item.derived_group for item in items)
        states = Counter(item.github_state for item in items)
        states.update(
            str((workflows.get(item.issue_key) or {}).get("state") or "mirrored")
            for item in items
        )
        labels = Counter(label for item in items for label in item.labels)
        label_colors = {
            name: color
            for item in items
            for name, color in item.label_colors.items()
        }
        authors = Counter(item.author_login for item in items)
        author_states = {
            login: {
                "open": sum(1 for item in items if item.author_login == login and item.github_state == "open"),
                "closed": sum(1 for item in items if item.author_login == login and item.github_state == "closed"),
            }
            for login in authors
        }
        return JSONResponse({
            "schema_version": "issue-triage-summary.v1",
            "repository": repository,
            "repository_url": f"https://github.com/{repository}",
            "new_issue_url": f"https://github.com/{repository}/issues/new",
            "total": len(items),
            "groups": dict(groups),
            "states": dict(states),
            "labels": dict(sorted(labels.items())),
            "label_colors": dict(sorted(label_colors.items())),
            "authors": dict(sorted(authors.items())),
            "author_states": dict(sorted(author_states.items())),
            "sync": sync.to_dict(),
        })

    @router.get("/api/projects/{project_id}/issue-triage/attachment")
    def issue_triage_attachment(project_id: str, url: str) -> Response:
        try:
            context(project_id)
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "unconfigured", "reason": str(exc)}, status_code=409)
        if not _is_github_image_url(url):
            return JSONResponse({"ok": False, "status": "invalid_attachment_url"}, status_code=400)
        try:
            raw, content_type = _read_github_image(url)
        except (OSError, ValueError) as exc:
            return JSONResponse({
                "ok": False,
                "status": "attachment_unavailable",
                "reason": str(exc),
            }, status_code=502)
        return Response(
            content=raw,
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=300"},
        )

    @router.get("/api/projects/{project_id}/issue-triage")
    def issue_triage_list(
        project_id: str,
        q: str = "",
        group: str = "",
        state: str = "",
        states: str = "",
        label: str = "",
        labels: str = "",
        author: str = "",
        authors: str = "",
        source: str = "",
        order_by: str = "created",
        order_direction: str = "desc",
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
    ) -> JSONResponse:
        try:
            ctx, repository, store = context(project_id)
            items = store.list()
            sync = store.sync_state()
            workflow_by_issue = workflow_projections(ctx, store, items)
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "unconfigured", "reason": str(exc)}, status_code=409)
        needle = q.strip().casefold()[:200]
        requested_label = label.strip().casefold()
        requested_states: set[str] | None = None
        if states:
            try:
                raw_states = json.loads(states)
            except json.JSONDecodeError:
                raw_states = []
            requested_states = {
                str(value).strip().casefold()
                for value in raw_states
                if str(value).strip()
            } if isinstance(raw_states, list) else set()
        requested_labels: set[str] | None = None
        if labels:
            try:
                raw_labels = json.loads(labels)
            except json.JSONDecodeError:
                raw_labels = []
            requested_labels = {
                str(value).strip().casefold()
                for value in raw_labels
                if str(value).strip()
            } if isinstance(raw_labels, list) else set()
        requested_authors: set[str] | None = None
        if authors:
            try:
                raw_authors = json.loads(authors)
            except json.JSONDecodeError:
                raw_authors = []
            requested_authors = {
                str(value).strip().casefold()
                for value in raw_authors
                if str(value).strip()
            } if isinstance(raw_authors, list) else set()
        filtered = [item for item in items if (
            (not group or item.derived_group == group)
            and (not state or item.github_state == state)
            and (not requested_label or requested_label in {value.casefold() for value in item.labels})
            and (requested_labels is None or bool(requested_labels & {value.casefold() for value in item.labels}))
            and (not author or item.author_login.casefold() == author.casefold())
            and (requested_authors is None or item.author_login.casefold() in requested_authors)
            and (not source or item.source == source)
            and _matches_issue_states(
                item,
                workflow_by_issue.get(item.issue_key),
                requested_states,
            )
            and _matches_issue_query(
                item,
                workflow_by_issue.get(item.issue_key),
                needle,
            )
        )]
        reverse = order_direction.strip().casefold() != "asc"
        order_key = order_by.strip().casefold()
        if order_key in {"created_name", "updated_name", "name"}:
            # The mirror keeps the current title, not a title history.  Both
            # name views therefore sort the available title snapshot while
            # retaining the distinct API fields for future history support.
            filtered.sort(key=lambda item: (item.title.casefold(), item.number), reverse=reverse)
        elif order_key == "updated":
            filtered.sort(key=lambda item: (item.updated_at, item.number), reverse=reverse)
        else:
            filtered.sort(key=lambda item: (item.created_at, item.number), reverse=reverse)
        page = filtered[cursor:cursor + limit]
        next_cursor = cursor + len(page) if cursor + len(page) < len(filtered) else None
        return JSONResponse({
            "schema_version": "issue-triage-page.v1",
            "repository": repository,
            "items": [
                _public(
                    item,
                    workflow=workflow_by_issue.get(item.issue_key),
                )
                for item in page
            ],
            "total": len(filtered),
            "cursor": cursor,
            "next_cursor": next_cursor,
            "sync": sync.to_dict(),
        })

    @router.get("/api/projects/{project_id}/issue-triage/{issue_number}")
    def issue_triage_detail(project_id: str, issue_number: int) -> JSONResponse:
        try:
            ctx, _, store = context(project_id)
            item = store.get(issue_number)
            if item is None:
                return JSONResponse({"ok": False, "status": "not_found"}, status_code=404)
            body = store.read_body(item)
            comments = store.read_comments(item)
            config = effective_config(ctx)
            workflows = workflow_projections(ctx, store, store.list())
            delivery = (
                build_delivery_service(ctx, config).projection(item)
                if config is not None else None
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "invalid_mirror", "reason": str(exc)}, status_code=409)
        return JSONResponse({
            "schema_version": "issue-triage-detail.v1",
            "issue": _public(item, workflow=(lambda workflow: (
                {
                    **workflow,
                    "needs_info": hydrate_external_issue_clarification(
                        ctx.state_dir, workflow.get("clarification_ref"),
                    ),
                }
                if workflow and workflow.get("clarification_ref") else workflow
            ))(workflows.get(item.issue_key))),
            "body": body,
            "comments": [comment.to_dict() for comment in comments],
            "delivery": delivery,
            "trust": "untrusted_external_input",
        })

    @router.post("/api/projects/{project_id}/issue-triage/refresh")
    async def issue_triage_refresh(
        project_id: str,
        force: bool = Query(default=False),
    ) -> JSONResponse:
        try:
            ctx, repository, _ = context(project_id)
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "unconfigured", "reason": str(exc)}, status_code=409)
        config = effective_config(ctx)
        if config is not None and config.self_issue.ingress.enabled:
            service = ExternalIssueIngressService(
                ctx.state_dir,
                config,
                project_root=getattr(ctx, "project_root", ctx.state_dir.parent),
                reconciler=build_reconciler(ctx.state_dir, repository),
            )
            outcome = await asyncio.to_thread(service.poll_once, force=force)
            result = {
                "ok": outcome.ok,
                "status": outcome.status,
                "changed": outcome.changed,
                "received": outcome.received,
                "triage_queued": outcome.triage_queued,
                "error": outcome.error,
            }
        else:
            result = await asyncio.to_thread(
                build_reconciler(ctx.state_dir, repository).refresh,
                force=force,
            )
        return JSONResponse(result, status_code=200 if result.get("ok") else 502)

    @router.post("/api/projects/{project_id}/issue-triage/start-triage")
    async def issue_triage_start(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = mutation_response(request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        try:
            ctx, repository, store = context(project_id)
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Request body must be an object")
            issue_number = _parse_issue_reference(payload.get("issue"), repository)
        except (json.JSONDecodeError, ValueError) as exc:
            return JSONResponse({
                "ok": False,
                "status": "invalid_issue_reference",
                "reason": str(exc),
            }, status_code=422)
        config = effective_config(ctx)
        if config is None or not config.self_issue.ingress.enabled:
            return JSONResponse({
                "ok": False,
                "status": "disabled",
                "reason": "GitHub Issue ingress is disabled",
            }, status_code=409)
        service = ExternalIssueIngressService(
            ctx.state_dir,
            config,
            project_root=getattr(ctx, "project_root", ctx.state_dir.parent),
            reconciler=build_reconciler(ctx.state_dir, repository),
        )
        outcome = await asyncio.to_thread(service.start_manual_triage, issue_number)
        if not outcome.ok:
            status_code = 404 if "not found" in outcome.error.casefold() else 502
            if outcome.status in {"disabled", "syncing"}:
                status_code = 409
            return JSONResponse({
                "ok": False,
                "status": outcome.status,
                "issue_number": outcome.issue_number,
                "reason": outcome.error,
            }, status_code=status_code)
        item = store.get(outcome.issue_number)
        events = event_log_from_project(
            ctx.state_dir,
            config=config,
        ).read_all()
        return JSONResponse({
            "ok": True,
            "status": outcome.status,
            "queued": outcome.queued,
            "issue_number": outcome.issue_number,
            "task_id": outcome.task_id,
            "workflow_run_id": outcome.workflow_run_id,
            "source_revision": outcome.source_revision,
            "issue": _public(item, workflow=_workflow_projection(events, item)) if item else None,
        })

    def delivery_context(project_id: str, issue_number: int):
        ctx, _, store = context(project_id)
        item = store.get(issue_number)
        if item is None:
            raise CandidateDeliveryError("GitHub Issue was not found in the mirror")
        config = effective_config(ctx)
        if config is None:
            raise CandidateDeliveryError("Issue candidate delivery is not configured")
        return item, build_delivery_service(ctx, config)

    async def delivery_payload(request: Request) -> dict[str, Any]:
        try:
            value = await request.json()
        except json.JSONDecodeError as exc:
            raise CandidateDeliveryError("Request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise CandidateDeliveryError("Request body must be an object")
        return value

    @router.post("/api/projects/{project_id}/issue-triage/{issue_number}/candidate-review")
    async def issue_candidate_review(
        project_id: str,
        issue_number: int,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = mutation_response(
            request, authorization, x_zf_web_token,
            action="issue-candidate-review",
        )
        if denied is not None:
            return denied
        try:
            payload = await delivery_payload(request)
            item, service = delivery_context(project_id, issue_number)
            result = service.review(
                item,
                verdict=str(payload.get("verdict") or ""),
                expected_candidate_sha=str(payload.get("candidate_sha") or ""),
                expected_source_revision=str(payload.get("source_revision") or ""),
                reason=str(payload.get("reason") or ""),
            )
        except CandidateDeliveryError as exc:
            return JSONResponse(
                {"ok": False, "status": "rejected", "reason": str(exc)},
                status_code=409,
            )
        return JSONResponse(result)

    @router.post("/api/projects/{project_id}/issue-triage/{issue_number}/prepare-publication")
    async def issue_candidate_prepare_publication(
        project_id: str,
        issue_number: int,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = mutation_response(
            request, authorization, x_zf_web_token,
            action="issue-candidate-prepare-publication",
        )
        if denied is not None:
            return denied
        try:
            payload = await delivery_payload(request)
            item, service = delivery_context(project_id, issue_number)
            result = service.prepare(
                item,
                expected_candidate_sha=str(payload.get("candidate_sha") or ""),
                expected_source_revision=str(payload.get("source_revision") or ""),
            )
        except CandidateDeliveryError as exc:
            return JSONResponse(
                {"ok": False, "status": "rejected", "reason": str(exc)},
                status_code=409,
            )
        return JSONResponse(result)

    @router.post("/api/projects/{project_id}/issue-triage/{issue_number}/pull-request")
    async def issue_candidate_record_pr(
        project_id: str,
        issue_number: int,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = mutation_response(
            request, authorization, x_zf_web_token,
            action="issue-candidate-record-pr",
        )
        if denied is not None:
            return denied
        try:
            payload = await delivery_payload(request)
            item, service = delivery_context(project_id, issue_number)
            result = await asyncio.to_thread(
                service.record_pr,
                item,
                url=str(payload.get("url") or ""),
            )
        except CandidateDeliveryError as exc:
            return JSONResponse(
                {"ok": False, "status": "rejected", "reason": str(exc)},
                status_code=409,
            )
        return JSONResponse(result)

    @router.post("/api/projects/{project_id}/issue-triage/{issue_number}/pull-request/refresh")
    async def issue_candidate_refresh_pr(
        project_id: str,
        issue_number: int,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = mutation_response(
            request, authorization, x_zf_web_token,
            action="issue-candidate-refresh-pr",
        )
        if denied is not None:
            return denied
        try:
            item, service = delivery_context(project_id, issue_number)
            result = await asyncio.to_thread(service.refresh_pr, item)
        except CandidateDeliveryError as exc:
            return JSONResponse(
                {"ok": False, "status": "rejected", "reason": str(exc)},
                status_code=409,
            )
        return JSONResponse(result)

    @router.post("/api/projects/{project_id}/issue-triage/github-webhook")
    async def issue_triage_webhook(
        project_id: str,
        request: Request,
        x_github_event: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
        x_hub_signature_256: str | None = Header(default=None),
    ) -> JSONResponse:
        secret = read_webhook_secret()
        if not secret:
            return JSONResponse({
                "ok": False,
                "status": "disabled",
                "reason": "ZF_GITHUB_TRIAGE_WEBHOOK_SECRET is not configured",
            }, status_code=503)
        raw = await request.body()
        if len(raw) > 2_000_000:
            return JSONResponse({"ok": False, "status": "payload_too_large"}, status_code=413)
        expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if not x_hub_signature_256 or not hmac.compare_digest(expected, x_hub_signature_256):
            return JSONResponse({"ok": False, "status": "invalid_signature"}, status_code=403)
        if not x_github_delivery:
            return JSONResponse({"ok": False, "status": "missing_delivery_id"}, status_code=422)
        try:
            _, repository, store = context(project_id)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("webhook payload must contain an object")
            repository_payload = payload.get("repository")
            if not isinstance(repository_payload, dict):
                raise ValueError("webhook repository is missing")
            repository_id = str(repository_payload.get("id") or "")
            full_name = str(repository_payload.get("full_name") or "")
            if full_name.casefold() != repository.casefold() or not repository_id:
                raise ValueError("webhook repository identity mismatch")
            current = store.sync_state()
            if current.repository_id and current.repository_id != repository_id:
                raise ValueError("webhook repository id mismatch")
            if x_github_event == "ping":
                if not store.claim_webhook_delivery(x_github_delivery):
                    return JSONResponse({"ok": True, "status": "duplicate"})
                return JSONResponse({"ok": True, "status": "pong"})
            if x_github_event != "issues":
                return JSONResponse({"ok": True, "status": "ignored"})
            issue = payload.get("issue")
            if not isinstance(issue, dict):
                raise ValueError("issues webhook is missing issue")
            seen_at = datetime_now_iso()
            mirror, body = normalize_github_issue(
                issue,
                repository=repository,
                repository_id=repository_id,
                seen_at=seen_at,
            )
            if not store.claim_webhook_delivery(x_github_delivery):
                return JSONResponse({"ok": True, "status": "duplicate"})
            _, changed = store.upsert(mirror, body)
            return JSONResponse({"ok": True, "status": "accepted", "changed": changed})
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return JSONResponse({
                "ok": False,
                "status": "invalid_payload",
                "reason": str(exc)[:300],
            }, status_code=422)

    return router


def datetime_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


__all__ = ["build_issue_triage_router"]
