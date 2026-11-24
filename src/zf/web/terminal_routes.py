"""HTTP and WebSocket routes for project-scoped Web terminal sessions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse

from zf.core.config.backend_identity import canonical_backend_id
from zf.core.config.schema import RuntimeWebTerminalConfig
from zf.core.workspace import stable_project_id
from zf.web.terminal_backend import TERMINAL_PROVIDER_KINDS, TerminalRuntimeError
from zf.web.terminal_gateway import (
    AttachmentTicketStore,
    WS_SUBPROTOCOL,
    relay_terminal_websocket,
    ticket_from_subprotocol_header,
)
from zf.web.terminal_service import TerminalCapabilityCache, TerminalService


MutationAuthorizer = Callable[..., dict[str, object] | None]
ServiceFactory = Callable[[str, Any], TerminalService]


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _terminal_config(ctx: Any) -> RuntimeWebTerminalConfig:
    return terminal_host_config(getattr(ctx, "config", None))


def terminal_host_config(config: Any) -> RuntimeWebTerminalConfig:
    """Return the Web host policy without depending on a target Project."""

    runtime = getattr(config, "runtime", None)
    value = getattr(runtime, "web_terminal", None)
    return value if isinstance(value, RuntimeWebTerminalConfig) else RuntimeWebTerminalConfig()


_TERMINAL_PROVIDER_BY_BACKEND = {
    "claude-code": "claude-code",
    "claude-headless": "claude-code",
    "codex": "codex",
    "codex-headless": "codex",
    "opencode": "opencode",
    "pi": "pi",
}


def terminal_project_providers(config: Any) -> tuple[str, ...]:
    """Project effective backends projected as launchable terminal providers."""

    candidates: list[object] = []
    orchestrator = getattr(config, "orchestrator", None)
    candidates.append(getattr(orchestrator, "backend", ""))
    for role in getattr(config, "roles", ()) or ():
        candidates.append(getattr(role, "backend", ""))
        candidates.extend(getattr(role, "backends", ()) or ())

    providers: list[str] = []
    for backend in candidates:
        provider = _TERMINAL_PROVIDER_BY_BACKEND.get(canonical_backend_id(backend), "")
        if (
            provider
            and provider in TERMINAL_PROVIDER_KINDS
            and provider not in providers
        ):
            providers.append(provider)
    return tuple(providers)


class TerminalServiceHub:
    def __init__(self, *, host_config: RuntimeWebTerminalConfig | None = None) -> None:
        self._lock = Lock()
        self._services: dict[tuple[str, str, str], TerminalService] = {}
        self._host_config = host_config
        self._probe_caches: dict[tuple[str, str], TerminalCapabilityCache] = {}

    def service(self, project_id: str, ctx: Any) -> TerminalService:
        state_dir = Path(ctx.state_dir).resolve(strict=False)
        project_root = Path(ctx.project_root).resolve(strict=False)
        canonical_project_id = (
            stable_project_id(
                name=str(
                    getattr(
                        getattr(getattr(ctx, "config", None), "project", None),
                        "name",
                        "",
                    )
                ),
                root=project_root,
            )
            if project_id == "default"
            else project_id
        )
        key = (canonical_project_id, str(project_root), str(state_dir))
        with self._lock:
            service = self._services.get(key)
            # Web Terminal enablement, Herdr and resource/security limits belong
            # to the Dashboard host. Provider truth belongs to the resolved
            # target Project's effective orchestrator/roles configuration.
            config = self._host_config or _terminal_config(ctx)
            providers = terminal_project_providers(getattr(ctx, "config", None))
            if (
                service is None
                or service.config != config
                or service.allowed_providers != providers
            ):
                policy = (config.herdr_binary, config.minimum_herdr_version)
                if policy not in self._probe_caches:
                    self._probe_caches[policy] = TerminalCapabilityCache()
                service = TerminalService(
                    project_id=canonical_project_id,
                    project_root=project_root,
                    state_dir=state_dir,
                    config=config,
                    allowed_providers=providers,
                    capability_cache=self._probe_caches[policy],
                )
                self._services[key] = service
            return service


def _error_response(exc: TerminalRuntimeError) -> JSONResponse:
    return JSONResponse(exc.projection(), status_code=exc.status_code)


def _raise_http(exc: TerminalRuntimeError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.projection()) from exc


def _authorized(
    request: Request,
    *,
    action: str,
    authorize_mutation: MutationAuthorizer,
    authorization: str | None,
    x_zf_web_token: str | None,
) -> JSONResponse | None:
    error = authorize_mutation(
        action,
        authorization=authorization,
        x_zf_web_token=x_zf_web_token,
        web_session_token=request.cookies.get("zf_web_session"),
    )
    if error is None:
        return None
    body = dict(error)
    status_code = int(body.pop("_status_code", 403))
    return JSONResponse(body, status_code=status_code)


def _same_origin(websocket: WebSocket, config: RuntimeWebTerminalConfig) -> bool:
    origin = websocket.headers.get("origin", "").rstrip("/")
    if not origin:
        return False
    from zf.integrations.controller.access import request_authority
    if request_authority.get() is True:
        # The private upstream Host differs from the public browser origin. Trust
        # only the ingress-verified claim, never a client-supplied forwarded host.
        return origin == websocket.scope.get("zf.access", {}).get("origin")
    if origin in config.allowed_origins:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    expected_scheme = "https" if websocket.url.scheme == "wss" else "http"
    return (
        parsed.scheme == expected_scheme
        and origin == f"{parsed.scheme}://{parsed.netloc}"
        and parsed.netloc.lower() == websocket.headers.get("host", "").lower()
    )


def build_terminal_router(
    *,
    resolve_ctx: Callable[[str], Any],
    authorize_mutation: MutationAuthorizer,
    host_config: RuntimeWebTerminalConfig | None = None,
    service_factory: ServiceFactory | None = None,
    ticket_store: AttachmentTicketStore | None = None,
) -> APIRouter:
    router = APIRouter()
    hub = TerminalServiceHub(host_config=host_config)
    tickets = ticket_store or AttachmentTicketStore()

    def service(project_id: str) -> TerminalService:
        ctx = resolve_ctx(project_id)
        return service_factory(project_id, ctx) if service_factory else hub.service(project_id, ctx)

    @router.get("/api/projects/{project_id}/terminal-sessions")
    def list_terminal_sessions(project_id: str) -> dict[str, object]:
        try:
            return service(project_id).list_sessions()
        except TerminalRuntimeError as exc:
            _raise_http(exc)

    @router.post("/api/projects/{project_id}/terminal-sessions/reconcile")
    def reconcile_terminal_sessions(
        project_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        auth = _authorized(
            request,
            action="terminal-session-reconcile",
            authorize_mutation=authorize_mutation,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
        )
        if auth is not None:
            return auth
        try:
            terminal_service = service(project_id)
            records = terminal_service.reconcile()
        except TerminalRuntimeError as exc:
            return _error_response(exc)
        return JSONResponse(
            {
                "ok": True,
                "schema_version": "terminal-reconcile.v1",
                "sessions": [terminal_service.project_session(record) for record in records],
            }
        )

    @router.post("/api/projects/{project_id}/terminal-sessions")
    def create_terminal_session(
        project_id: str,
        body: dict[str, object],
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        auth = _authorized(
            request,
            action="terminal-session-create",
            authorize_mutation=authorize_mutation,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
        )
        if auth is not None:
            return auth
        terminal_service = service(project_id)
        try:
            record = terminal_service.create_session(
                provider=str(body.get("provider") or "").strip(),
                slot=str(body.get("slot") or "").strip(),
                title=str(body.get("title") or ""),
            )
        except TerminalRuntimeError as exc:
            return _error_response(exc)
        receipt = terminal_service.record_action_receipt("create", record.session_id)
        return JSONResponse(
            {
                "ok": True,
                "session": terminal_service.project_session(record),
                "receipt": receipt,
            }
        )

    @router.get("/api/projects/{project_id}/terminal-sessions/{session_id}")
    def get_terminal_session(project_id: str, session_id: str) -> dict[str, object]:
        try:
            terminal_service = service(project_id)
            return {
                "schema_version": "terminal-session.v1",
                "session": terminal_service.project_session(
                    terminal_service.get_session(session_id)
                ),
            }
        except TerminalRuntimeError as exc:
            _raise_http(exc)

    @router.post("/api/projects/{project_id}/terminal-sessions/{session_id}/rename")
    def rename_terminal_session(
        project_id: str,
        session_id: str,
        body: dict[str, object],
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        auth = _authorized(
            request,
            action="terminal-session-rename",
            authorize_mutation=authorize_mutation,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
        )
        if auth is not None:
            return auth
        terminal_service = service(project_id)
        try:
            record = terminal_service.rename_session(
                session_id,
                str(body.get("title") or ""),
            )
        except TerminalRuntimeError as exc:
            return _error_response(exc)
        receipt = terminal_service.record_action_receipt("rename", session_id)
        return JSONResponse(
            {
                "ok": True,
                "session": terminal_service.project_session(record),
                "receipt": receipt,
            }
        )

    @router.post("/api/projects/{project_id}/terminal-sessions/{session_id}/stop")
    def stop_terminal_session(
        project_id: str,
        session_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        auth = _authorized(
            request,
            action="terminal-session-stop",
            authorize_mutation=authorize_mutation,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
        )
        if auth is not None:
            return auth
        terminal_service = service(project_id)
        try:
            record = terminal_service.stop_session(session_id)
        except TerminalRuntimeError as exc:
            return _error_response(exc)
        receipt = terminal_service.record_action_receipt("stop", session_id)
        return JSONResponse(
            {
                "ok": True,
                "session": terminal_service.project_session(record),
                "receipt": receipt,
            }
        )

    def issue_attachment(
        project_id: str,
        session_id: str,
        body: dict[str, object],
        request: Request,
        authorization: str | None,
        x_zf_web_token: str | None,
        *,
        takeover: bool,
    ) -> JSONResponse:
        action = "terminal-session-takeover" if takeover else "terminal-session-attach"
        auth = _authorized(
            request,
            action=action,
            authorize_mutation=authorize_mutation,
            authorization=authorization,
            x_zf_web_token=x_zf_web_token,
        )
        if auth is not None:
            return auth
        terminal_service = service(project_id)
        mode = "control" if takeover else str(body.get("mode") or "observe")
        raw_cols = body.get("cols", 120)
        raw_rows = body.get("rows", 40)
        try:
            if isinstance(raw_cols, bool) or isinstance(raw_rows, bool):
                raise ValueError
            cols = int(raw_cols)
            rows = int(raw_rows)
        except (TypeError, ValueError):
            return _error_response(
                TerminalRuntimeError(
                    "invalid_terminal_geometry", "cols and rows must be integers", status_code=422
                )
            )
        try:
            terminal_service.bridge_spec(
                session_id,
                mode=mode,
                takeover=takeover,
                cols=cols,
                rows=rows,
            )
            from zf.web.local_login import current_grant
            grant = current_grant.get()
            ticket = tickets.issue(
                browser_binding=grant.binding if grant else "",
                project_id=terminal_service.project_id,
                session_id=session_id,
                mode=mode,
                takeover=takeover,
                cols=cols,
                rows=rows,
                ttl_seconds=terminal_service.config.ticket_ttl_seconds,
                max_attachments=terminal_service.config.max_attachments_per_session,
            )
        except TerminalRuntimeError as exc:
            return _error_response(exc)
        receipt = (
            terminal_service.record_action_receipt("takeover", session_id)
            if takeover
            else {
                "schema_version": "terminal-action-receipt.v1",
                "action": "attach",
                "project_id": terminal_service.project_id,
                "session_id": session_id,
                "at": _timestamp(),
            }
        )
        return JSONResponse(
            {
                "ok": True,
                "schema_version": "terminal-attachment-ticket.v1",
                "ticket": ticket.token,
                "subprotocol": WS_SUBPROTOCOL,
                "mode": mode,
                "expires_in_seconds": terminal_service.config.ticket_ttl_seconds,
                "receipt": receipt,
            }
        )

    @router.post("/api/projects/{project_id}/terminal-sessions/{session_id}/attachments")
    def create_terminal_attachment(
        project_id: str,
        session_id: str,
        body: dict[str, object],
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        return issue_attachment(
            project_id,
            session_id,
            body,
            request,
            authorization,
            x_zf_web_token,
            takeover=False,
        )

    @router.post("/api/projects/{project_id}/terminal-sessions/{session_id}/takeover")
    def takeover_terminal_session(
        project_id: str,
        session_id: str,
        body: dict[str, object],
        request: Request,
        authorization: str | None = Header(default=None),
        x_zf_web_token: str | None = Header(default=None),
    ) -> JSONResponse:
        return issue_attachment(
            project_id,
            session_id,
            body,
            request,
            authorization,
            x_zf_web_token,
            takeover=True,
        )

    async def terminal_socket(
        websocket: WebSocket,
        *,
        project_id: str,
        session_id: str,
        mode: str,
    ) -> None:
        if any(key in websocket.query_params for key in ("token", "ticket", "access_token")):
            await websocket.close(code=1008)
            return
        terminal_service = service(project_id)
        if not _same_origin(websocket, terminal_service.config):
            await websocket.close(code=1008)
            return
        token = ticket_from_subprotocol_header(
            websocket.headers.get("sec-websocket-protocol", "")
        )
        try:
            from zf.web.local_login import current_grant
            grant = current_grant.get()
            attachment = tickets.consume(
                token,
                browser_binding=grant.binding if grant else "",
                project_id=terminal_service.project_id,
                session_id=session_id,
                mode=mode,
            )
            spec = terminal_service.bridge_spec(
                session_id,
                mode=mode,
                takeover=attachment.ticket.takeover,
                cols=attachment.ticket.cols,
                rows=attachment.ticket.rows,
            )
        except TerminalRuntimeError:
            await websocket.close(code=1008)
            return
        await websocket.accept(subprotocol=WS_SUBPROTOCOL)
        try:
            await relay_terminal_websocket(
                websocket,
                spec=spec,
                config=terminal_service.config,
            )
        finally:
            tickets.release(attachment.attachment_id)
            if mode == "control":
                await asyncio.to_thread(terminal_service.settle_usage, session_id)

    @router.websocket(
        "/api/projects/{project_id}/terminal-sessions/{session_id}/observe"
    )
    async def observe_terminal(
        websocket: WebSocket, project_id: str, session_id: str
    ) -> None:
        await terminal_socket(
            websocket,
            project_id=project_id,
            session_id=session_id,
            mode="observe",
        )

    @router.websocket(
        "/api/projects/{project_id}/terminal-sessions/{session_id}/control"
    )
    async def control_terminal(
        websocket: WebSocket, project_id: str, session_id: str
    ) -> None:
        await terminal_socket(
            websocket,
            project_id=project_id,
            session_id=session_id,
            mode="control",
        )

    return router
