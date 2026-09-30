"""Read-only GitHub Issue Triage mirror routes and optional webhook ingress."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
from functools import lru_cache
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request
from fastapi.responses import JSONResponse, Response

from zf.core.config.schema import ZfConfig
from zf.core.config.self_issue_policy import inherit_workspace_self_issue_config
from zf.core.events.factory import event_log_from_project
from zf.core.events import EventWriter
from zf.core.events.model import ZfEvent
from zf.core.events.segments import build_event_manifest
from zf.core.issue_triage.models import IssueMirror
from zf.core.issue_triage.lifecycle_states import REMOTE_LIFECYCLE_STATE_SET
from zf.core.issue_triage.store import IssueMirrorStore
from zf.integrations.forge.github_issues import (
    GitHubIssueReconciler,
    normalize_github_issue,
)
from zf.runtime.external_issue_ingress import (
    ExternalIssueIngressService,
    build_github_lifecycle_label_writer,
)
from zf.runtime.external_issue_clarification import (
    hydrate_external_issue_clarification,
)
from zf.runtime.issue_candidate_delivery import (
    CandidateDeliveryError,
    IssueCandidateDeliveryService,
)

from .issue_ingress_background import IssueIngressBackground
from zf.runtime.issue_lifecycle_projection import _workflow_projection, issue_lifecycle_event_index
from zf.runtime.external_issue_label_sync import _relevant_events

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



def _is_github_media_url(value: str) -> bool:
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


@lru_cache(maxsize=4)
def _read_github_media(value: str) -> tuple[bytes, str]:
    request = urllib.request.Request(
        value,
        headers={"Accept": "*/*", "User-Agent": "ZaoFu-Issue-Triage"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0]
        raw = response.read(_MAX_GITHUB_IMAGE_BYTES + 1)
    if len(raw) > _MAX_GITHUB_IMAGE_BYTES:
        raise ValueError("GitHub media exceeds the 20 MB safe limit")
    if content_type == "application/octet-stream":
        if raw.startswith(b"\x89PNG\r\n\x1a\n"):
            content_type = "image/png"
        elif raw.startswith(b"\xff\xd8\xff"):
            content_type = "image/jpeg"
        elif raw.startswith(b"\x1aE\xdf\xa3"):
            content_type = "video/webm"
        elif len(raw) >= 12 and raw[4:8] == b"ftyp":
            content_type = "video/mp4"
    if not content_type.startswith(("image/", "video/")):
        raise ValueError("GitHub attachment is not supported media")
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
    triage_view_auth_error: MutationAuthorizer | None = None,
    session_cookie: SessionCookieReader | None = None,
    delivery_service_factory: DeliveryServiceFactory | None = None,
    project_ids: Callable[[], list[str]] | None = None,
) -> APIRouter:
    router = APIRouter()
    intake_background = IssueIngressBackground()
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
            lifecycle_label_writer=build_github_lifecycle_label_writer(),
        )
    )
    projection_cache: dict[
        str,
        tuple[tuple[int, int, str], dict[str, dict[str, Any] | None]],
    ] = {}
    projection_cache_lock = threading.Lock()
    single_projection_cache: dict[
        str,
        tuple[tuple[int, int, str, str, str, str], dict[str, Any] | None],
    ] = {}
    single_projection_cache_lock = threading.Lock()

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
            event_log = event_log_from_project(ctx.state_dir, config=effective_config(ctx))
            issue_keys = {item.issue_key for item in items}
            events = _relevant_events(event_log, issue_keys)
            indexed = issue_lifecycle_event_index(events, issue_keys)
            projected = {
                item.issue_key: _workflow_projection(indexed.get(item.issue_key, []), item)
                for item in items
            }
            projection_cache[cache_key] = (fingerprint, projected)
            if len(projection_cache) > 8:
                projection_cache.pop(next(iter(projection_cache)))
            return projected

    def single_workflow_projection(
        ctx: Any,
        store: IssueMirrorStore,
        item: IssueMirror,
    ) -> dict[str, Any] | None:
        try:
            issues_stat = store.issues_path.stat()
            issues_fingerprint = (int(issues_stat.st_size), int(issues_stat.st_mtime_ns))
        except OSError:
            issues_fingerprint = (0, 0)
        fingerprint = (
            *issues_fingerprint,
            build_event_manifest(ctx.state_dir).digest,
            item.issue_key,
            item.body_digest,
            item.comments_digest,
        )
        cache_key = str(ctx.state_dir.resolve())
        with single_projection_cache_lock:
            cached = single_projection_cache.get(cache_key)
            if cached is not None and cached[0] == fingerprint:
                return cached[1]
            event_log = event_log_from_project(ctx.state_dir, config=effective_config(ctx))
            events = _relevant_events(event_log, {item.issue_key})
            projected = _workflow_projection(events, item)
            single_projection_cache[cache_key] = (fingerprint, projected)
            if len(single_projection_cache) > 8:
                single_projection_cache.pop(next(iter(single_projection_cache)))
            return projected

    def owned_workflows(project_id: str, ctx: Any, store: IssueMirrorStore,
                        items: list[IssueMirror]) -> dict[str, tuple[str, Any, dict[str, Any] | None]]:
        """Resolve by repository-scoped issue identity, never a client-supplied owner.

        A mirror alone is not ownership. Use the newest recorded admission across
        registered projects, retaining terminal runs so lagging labels cannot
        resurrect a cancelled request in another project's view.
        """
        local = workflow_projections(ctx, store, items)
        results = {item.issue_key: (project_id, ctx, local.get(item.issue_key)) for item in items}
        if not items:
            return results
        identities = {item.issue_key for item in items}
        newest: dict[str, tuple[str, str]] = {}
        visited: set[str] = set()
        # Snapshot each registered project once per request, not once per Issue.
        # All consumers (list, summary, detail, cancel) share the same owner rule.
        for candidate_id in dict.fromkeys([project_id, *(project_ids() if project_ids else [])]):
            try:
                candidate_ctx = ctx if candidate_id == project_id else resolve_ctx(candidate_id)
                state_key = str(candidate_ctx.state_dir.resolve())
                if state_key in visited:
                    continue
                visited.add(state_key)
                candidate_store = store if candidate_id == project_id else IssueMirrorStore(candidate_ctx.state_dir)
                candidate_items = items if candidate_id == project_id else candidate_store.list()
                projections = local if candidate_id == project_id else workflow_projections(candidate_ctx, candidate_store, candidate_items)
                for candidate_item in candidate_items:
                    if candidate_item.issue_key not in identities:
                        continue
                    workflow = projections.get(candidate_item.issue_key)
                    if not workflow or not workflow.get("run_id"):
                        continue
                    admitted = max((str(entry.get("queued_at") or "") for entry in workflow.get("triage_history", [])), default="")
                    rank = (admitted, candidate_id)
                    if candidate_item.issue_key in newest and rank <= newest[candidate_item.issue_key]:
                        continue
                    newest[candidate_item.issue_key] = rank
                    results[candidate_item.issue_key] = (candidate_id, candidate_ctx, {
                        **workflow, "workflow_project_id": candidate_id,
                        "workflow_project_name": getattr(getattr(candidate_ctx.config, "project", None), "name", candidate_id),
                        "execution_project_root": str(getattr(candidate_ctx, "project_root", candidate_ctx.state_dir.parent)),
                    })
            except (OSError, ValueError):
                # Stale registry entries cannot prevent access to a live project.
                continue
        return results

    def owned_workflow(project_id: str, ctx: Any, store: IssueMirrorStore,
                       item: IssueMirror) -> tuple[str, Any, dict[str, Any] | None]:
        return owned_workflows(project_id, ctx, store, store.list())[item.issue_key]

    def visible_workflows(project_id: str, ctx: Any, store: IssueMirrorStore,
                          items: list[IssueMirror]) -> dict[str, dict[str, Any] | None]:
        return {key: result[2] for key, result in owned_workflows(project_id, ctx, store, items).items()}

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
            x_zf_developer_token=request.headers.get("X-ZF-Developer-Token"),
            web_session_token=read_session_cookie(request),
        )
        if error is None:
            return None
        body = dict(error)
        return JSONResponse(body, status_code=int(body.pop("_status_code", 403)))

    def view_response(
        request: Request,
        authorization: str | None,
        token: str | None,
    ) -> JSONResponse | None:
        """Keep the developer-only Triage projection behind a capability.

        The callback is optional for isolated projection tests and embedded
        read-only fixtures. Production composition supplies the same session
        authorizer used by controlled mutations, so a reporter cannot infer
        maintainer access from the current project directory.
        """
        if triage_view_auth_error is None:
            return None
        error = triage_view_auth_error(
            "issue-triage-view",
            authorization=authorization,
            x_zf_web_token=token,
            x_zf_developer_token=request.headers.get("X-ZF-Developer-Token"),
            web_session_token=read_session_cookie(request),
        )
        if error is None:
            return None
        body = dict(error)
        return JSONResponse(body, status_code=int(body.pop("_status_code", 403)))

    @router.get("/api/projects/{project_id}/issue-triage/summary")
    def issue_triage_summary(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = view_response(request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        try:
            ctx, repository, store = context(project_id)
            items = store.list()
            sync = store.sync_state()
            workflows = visible_workflows(project_id, ctx, store, items)
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
        sources = Counter(item.source for item in items)
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
            "sources": dict(sorted(sources.items())),
            "author_states": dict(sorted(author_states.items())),
            "sync": sync.to_dict(),
            "intake": intake_background.status(ctx.state_dir),
        })

    @router.get("/api/projects/{project_id}/issue-triage/attachment")
    def issue_triage_attachment(
        project_id: str,
        url: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> Response:
        denied = view_response(request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        try:
            _, repository, _ = context(project_id)
        except ValueError as exc:
            return JSONResponse({"ok": False, "status": "unconfigured", "reason": str(exc)}, status_code=409)
        parsed = urllib.parse.urlparse(url)
        release_media = (parsed.scheme == "https" and parsed.netloc == "github.com"
                         and parsed.path.startswith(f"/{repository}/releases/download/"))
        if not _is_github_media_url(url) and not release_media:
            return JSONResponse({"ok": False, "status": "invalid_attachment_url"}, status_code=400)
        try:
            raw, content_type = _read_github_media(url)
        except (OSError, ValueError) as exc:
            return JSONResponse({
                "ok": False,
                "status": "attachment_unavailable",
                "reason": str(exc),
            }, status_code=502)
        headers = {"Cache-Control": "private, max-age=300", "Accept-Ranges": "bytes"}
        requested_range = request.headers.get("range", "")
        if requested_range:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested_range)
            if not match or not any(match.groups()):
                return Response(status_code=416, headers={"Content-Range": f"bytes */{len(raw)}"})
            first, last = match.groups()
            start = int(first) if first else max(0, len(raw) - int(last))
            end = min(int(last), len(raw) - 1) if first and last else len(raw) - 1
            if start > end or start >= len(raw):
                return Response(status_code=416, headers={"Content-Range": f"bytes */{len(raw)}"})
            headers["Content-Range"] = f"bytes {start}-{end}/{len(raw)}"
            return Response(raw[start:end + 1], status_code=206, media_type=content_type, headers=headers)
        return Response(content=raw, media_type=content_type, headers=headers)

    @router.get("/api/projects/{project_id}/issue-triage")
    def issue_triage_list(
        project_id: str,
        request: Request,
        q: str = "",
        group: str = "",
        state: str = "",
        states: str = "",
        label: str = "",
        labels: str = "",
        author: str = "",
        authors: str = "",
        source: str = "",
        sources: str = "",
        order_by: str = "created",
        order_direction: str = "desc",
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = view_response(request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        try:
            ctx, repository, store = context(project_id)
            items = store.list()
            sync = store.sync_state()
            workflow_by_issue = visible_workflows(project_id, ctx, store, items)
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
        requested_sources: set[str] | None = None
        if sources:
            try:
                raw_sources = json.loads(sources)
            except json.JSONDecodeError:
                raw_sources = []
            requested_sources = {
                str(value).strip().casefold()
                for value in raw_sources
                if str(value).strip()
            } if isinstance(raw_sources, list) else set()
        filtered = [item for item in items if (
            (not group or item.derived_group == group)
            and (not state or item.github_state == state)
            and (not requested_label or requested_label in {value.casefold() for value in item.labels})
            and (requested_labels is None or bool(requested_labels & {value.casefold() for value in item.labels}))
            and (not author or item.author_login.casefold() == author.casefold())
            and (requested_authors is None or item.author_login.casefold() in requested_authors)
            and (not source or item.source == source)
            and (requested_sources is None or item.source.casefold() in requested_sources)
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
        if order_key == "number":
            filtered.sort(key=lambda item: item.number, reverse=reverse)
        elif order_key in {"created_name", "updated_name", "name"}:
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
    def issue_triage_detail(
        project_id: str,
        issue_number: int,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = view_response(request, authorization, x_zf_web_token)
        if denied is not None:
            return denied
        try:
            ctx, _, store = context(project_id)
            item = store.get(issue_number)
            if item is None:
                return JSONResponse({"ok": False, "status": "not_found"}, status_code=404)
            body = store.read_body(item)
            comments = store.read_comments(item)
            _, ctx, workflow = owned_workflow(project_id, ctx, store, item)
            config = effective_config(ctx)
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
                    "needs_info": (
                        {
                            **clarification,
                            "request_status": workflow.get("clarification_request_status") or "pending",
                            **({"request_url": workflow.get("clarification_request_url")} if workflow.get("clarification_request_url") else {}),
                        }
                        if isinstance((clarification := hydrate_external_issue_clarification(
                            ctx.state_dir, workflow.get("clarification_ref"),
                        )), dict)
                        else None
                    ),
                }
                if workflow and workflow.get("clarification_ref") else workflow
            ))(workflow)),
            "body": body,
            "comments": [comment.to_dict() for comment in comments],
            "delivery": delivery,
            "trust": "untrusted_external_input",
        })

    @router.post("/api/projects/{project_id}/issue-triage/refresh")
    async def issue_triage_refresh(
        project_id: str,
        background_tasks: BackgroundTasks,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
        force: bool = Query(default=False),
        full: bool = Query(default=False),
    ) -> JSONResponse:
        denied = mutation_response(
            request,
            authorization,
            x_zf_web_token,
            action="issue-triage-refresh",
        )
        if denied is not None:
            return denied
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
                lifecycle_label_writer=build_github_lifecycle_label_writer(),
            )
            result = await asyncio.to_thread(service.refresh_mirror, force=force, full=full)
            if result.get("ok") and result.get("status") != "syncing":
                intake_background.schedule(service, background_tasks)
            result["intake"] = intake_background.status(ctx.state_dir)
        else:
            result = await asyncio.to_thread(
                build_reconciler(ctx.state_dir, repository).refresh,
                force=force,
                **({"full": True} if full else {}),
            )
            if config is not None and result.get("ok") and result.get("status") != "syncing":
                service = ExternalIssueIngressService(
                    ctx.state_dir, config,
                    project_root=getattr(ctx, "project_root", ctx.state_dir.parent),
                    lifecycle_label_writer=build_github_lifecycle_label_writer(),
                )
                label_sync = await asyncio.to_thread(service.reconcile_lifecycle_labels)
                result["lifecycle_label_sync"] = label_sync
        return JSONResponse(result, status_code=200 if result.get("ok") else 502)

    @router.post("/api/projects/{project_id}/issue-triage/start-triage")
    async def issue_triage_start(
        project_id: str,
        background_tasks: BackgroundTasks,
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
        # Manual Start Triage is an explicit developer mutation.  It may
        # refresh and admit one selected Issue even when the background
        # GitHub ingress/poller is disabled.  The ingress switch controls
        # automatic synchronization only; binding this route to it made a
        # cancelled queue entry impossible to restart.
        if config is None or not config.self_issue.enabled:
            return JSONResponse({
                "ok": False,
                "status": "disabled",
                "reason": "Self-Issue is disabled",
            }, status_code=409)
        service = ExternalIssueIngressService(
            ctx.state_dir,
            config,
            project_root=getattr(ctx, "project_root", ctx.state_dir.parent),
            reconciler=build_reconciler(ctx.state_dir, repository),
            lifecycle_label_writer=build_github_lifecycle_label_writer(),
        )
        outcome = await asyncio.to_thread(
            service.start_manual_triage,
            issue_number,
            # A selected Issue normally already has a mirror snapshot.  Keep
            # the historical URL/number path working by fetching only when no
            # local snapshot exists; the common path stays click-fast.
            refresh=store.get(issue_number) is None,
            defer_remote_sync=True,
            defer_attachments=True,
            lock_timeout_seconds=0.1,
        )
        if not outcome.ok:
            status_code = 404 if "not found" in outcome.error.casefold() else 502
            if outcome.status in {"disabled", "syncing"}:
                status_code = 409
            elif outcome.status == "invalid_execution_project":
                status_code = 422
            return JSONResponse({
                "ok": False,
                "status": outcome.status,
                "issue_number": outcome.issue_number,
                "reason": outcome.error,
            }, status_code=status_code)
        owner_id = project_id
        if outcome.execution_project_root and outcome.execution_project_root != str(service.project_root):
            owner_id = outcome.workflow_project_id
            from pathlib import Path
            from zf.runtime.issue_execution import execution_context
            ctx = execution_context(Path(outcome.execution_project_root), config)
            config = effective_config(ctx)
            store = IssueMirrorStore(ctx.state_dir)
            service = ExternalIssueIngressService(ctx.state_dir, config, project_root=ctx.project_root,
                reconciler=build_reconciler(ctx.state_dir, repository),
                lifecycle_label_writer=build_github_lifecycle_label_writer())
        item = store.get(outcome.issue_number)
        # The local queue event is durable at this point.  GitHub label
        # convergence is a projection and can safely run after the response.
        # Keep the mirror fresh without putting GitHub latency on the click.
        # Starlette starts these tasks only after the response body is sent.
        background_tasks.add_task(service.reconciler.refresh_issue, issue_number)
        if item is not None:
            background_tasks.add_task(service._issue_attachment_refs, item)
        background_tasks.add_task(service.reconcile_lifecycle_labels)
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
            "workflow_project_id": owner_id,
            "workflow_project_name": outcome.workflow_project_name,
            "execution_project_root": outcome.execution_project_root,
            "issue": _public(item, workflow={**(_workflow_projection(events, item) or {}),
                "workflow_project_id": owner_id,
                "workflow_project_name": outcome.workflow_project_name,
                "execution_project_root": outcome.execution_project_root}) if item else None,
        })

    @router.post("/api/projects/{project_id}/issue-triage/{issue_number}/cancel-queued")
    async def issue_triage_cancel_queued(
        project_id: str,
        issue_number: int,
        background_tasks: BackgroundTasks,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        denied = mutation_response(request, authorization, x_zf_web_token,
                                   action="issue-triage-queued-cancel")
        if denied is not None:
            return denied
        try:
            ctx, _, store = context(project_id)
            item = store.get(issue_number)
            if item is None:
                return JSONResponse({"ok": False, "status": "not_found"}, status_code=404)
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Request body must be an object")
            owner_id, ctx, resolved_workflow = owned_workflow(project_id, ctx, store, item)
            workflow = resolved_workflow or {}
            triage_id = str(workflow.get("triage_id") or "").strip()
            workflow_state = str(workflow.get("state") or "")
            if workflow_state not in {"triage_queued", "fix_queued"}:
                return JSONResponse({"ok": False, "status": "invalid_transition",
                                     "reason": "Issue is not queued for Triage"}, status_code=409)
            if not triage_id or not workflow.get("queued_cancel_available"):
                # A remote lifecycle label does not identify a current queue
                # entry in this project's event ledger.
                # It must not be turned into a fabricated run id or a generic
                # "triage_id is required" error from the action service.
                return JSONResponse({
                    "ok": False,
                    "status": "not_cancellable",
                    "reason": "No cancellable queued request exists in this project. The displayed state may come from GitHub labels. Refresh or switch to the project that queued this Issue.",
                }, status_code=409)
            supplied_id = str(payload.get("triage_id") or "").strip()
            if supplied_id and supplied_id != triage_id:
                return JSONResponse({"ok": False, "status": "revision_conflict",
                                     "reason": "The queued request changed. Refresh before cancelling."}, status_code=409)
            writer = EventWriter(event_log_from_project(ctx.state_dir, config=effective_config(ctx)))
            requested = writer.emit("external_issue.triage.queued_cancel_requested", actor="web",
                                    task_id=str(workflow.get("task_id") or "") or None,
                                    correlation_id=triage_id,
                                    payload={"issue_number": issue_number, "triage_id": triage_id})
            from zf.runtime.control_actions import ControlledActionService
            action = "external-issue-fix-queued-cancel" if workflow_state == "fix_queued" else "external-issue-triage-queued-cancel"
            service = ControlledActionService(
                ctx.state_dir, writer, config=effective_config(ctx),
                project_root=getattr(ctx, "project_root", ctx.state_dir.parent),
                actor="web", source="issue-triage", surface="web",
            )
            result = await asyncio.to_thread(service.execute, action=action,
                      requested_action="issue-triage-queued-cancel",
                      payload={
                          **payload,
                          "triage_id": triage_id,
                          "defer_lifecycle_label_sync": True,
                      }, requested=requested)
            code = int(result.pop("_status_code", 200))
            result["workflow_project_id"] = owner_id
            if code < 300 and result.get("status") == "cancelled":
                ingress = ExternalIssueIngressService(
                    ctx.state_dir,
                    effective_config(ctx),
                    project_root=getattr(ctx, "project_root", ctx.state_dir.parent),
                    lifecycle_label_writer=build_github_lifecycle_label_writer(),
                )
                background_tasks.add_task(ingress.reconcile_lifecycle_labels)
            return JSONResponse(result, status_code=code)
        except (json.JSONDecodeError, ValueError) as exc:
            return JSONResponse({"ok": False, "status": "invalid_request", "reason": str(exc)}, status_code=422)

    def delivery_context(project_id: str, issue_number: int):
        ctx, _, store = context(project_id)
        item = store.get(issue_number)
        if item is None:
            raise CandidateDeliveryError("GitHub Issue was not found in the mirror")
        _, ctx, _ = owned_workflow(project_id, ctx, store, item)
        config = effective_config(ctx)
        if config is None:
            raise CandidateDeliveryError("Issue candidate delivery is not configured")
        owner_item = IssueMirrorStore(ctx.state_dir).get(issue_number)
        return owner_item or item, build_delivery_service(ctx, config)

    async def delivery_payload(request: Request) -> dict[str, Any]:
        try:
            value = await request.json()
        except json.JSONDecodeError as exc:
            raise CandidateDeliveryError("Request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise CandidateDeliveryError("Request body must be an object")
        return value

    def _schedule_issue_label_sync(
        service: Any,
        item: IssueMirror,
        result: Mapping[str, Any],
        background_tasks: BackgroundTasks,
    ) -> None:
        """Project a completed local delivery transition after the response."""
        writer = getattr(service, "_reconcile_remote_lifecycle_label", None)
        state = str(result.get("status") or "").strip()
        if callable(writer) and state:
            background_tasks.add_task(writer, item, state)

    @router.post("/api/projects/{project_id}/issue-triage/{issue_number}/candidate-review")
    async def issue_candidate_review(
        project_id: str,
        issue_number: int,
        background_tasks: BackgroundTasks,
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
            if hasattr(service, "defer_remote_sync"):
                service.defer_remote_sync = True
            result = await asyncio.to_thread(
                service.review,
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
        _schedule_issue_label_sync(service, item, result, background_tasks)
        return JSONResponse(result)

    @router.post("/api/projects/{project_id}/issue-triage/{issue_number}/prepare-publication")
    async def issue_candidate_prepare_publication(
        project_id: str,
        issue_number: int,
        background_tasks: BackgroundTasks,
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
            if hasattr(service, "defer_remote_sync"):
                service.defer_remote_sync = True
            result = await asyncio.to_thread(
                service.prepare,
                item,
                expected_candidate_sha=str(payload.get("candidate_sha") or ""),
                expected_source_revision=str(payload.get("source_revision") or ""),
            )
        except CandidateDeliveryError as exc:
            return JSONResponse(
                {"ok": False, "status": "rejected", "reason": str(exc)},
                status_code=409,
            )
        _schedule_issue_label_sync(service, item, result, background_tasks)
        return JSONResponse(result)

    @router.post("/api/projects/{project_id}/issue-triage/{issue_number}/pull-request")
    async def issue_candidate_record_pr(
        project_id: str,
        issue_number: int,
        background_tasks: BackgroundTasks,
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
            if hasattr(service, "defer_remote_sync"):
                service.defer_remote_sync = True
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
        _schedule_issue_label_sync(service, item, result, background_tasks)
        return JSONResponse(result)

    @router.post("/api/projects/{project_id}/issue-triage/{issue_number}/pull-request/refresh")
    async def issue_candidate_refresh_pr(
        project_id: str,
        issue_number: int,
        background_tasks: BackgroundTasks,
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
            if hasattr(service, "defer_remote_sync"):
                service.defer_remote_sync = True
            result = await asyncio.to_thread(service.refresh_pr, item)
        except CandidateDeliveryError as exc:
            return JSONResponse(
                {"ok": False, "status": "rejected", "reason": str(exc)},
                status_code=409,
            )
        _schedule_issue_label_sync(service, item, result, background_tasks)
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
