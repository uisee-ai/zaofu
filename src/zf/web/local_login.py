"""Browser authentication for an explicitly local Dashboard installation."""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import os
import secrets

from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection, Request
from starlette.responses import HTMLResponse, JSONResponse

from zf.core.workspace.web_account import LocalAccount, session_key
from zf.integrations.controller import binding


@dataclass(frozen=True)
class LocalGrant:
    writable: bool
    binding: str


current_grant: ContextVar[LocalGrant | None] = ContextVar("local_web_grant", default=None)
_BASE = "/auth/local"
_PAGE = Path(__file__).with_name("static") / "local-login.html"


def _json(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


class LocalLoginMiddleware:
    def __init__(self, app, account: LocalAccount, read_only: bool):
        self.login_lock = asyncio.Lock()
        self.app = app
        self.account = account
        self.read_only = read_only
        self.cookie = "zf_local_" + session_key(str(account.path.resolve()))[:16]

    def managed_binding_present(self) -> bool:
        # An already-running local server must retire when the installation is taken over.
        return os.path.lexists(self.account.path.parent / "controller-binding.json") or os.path.lexists(binding.SYSTEM_CONFIG)

    def page(self, account_settings: bool = False) -> HTMLResponse:
        return HTMLResponse(_PAGE.read_text().replace("__PAGE__", "account" if account_settings else "login"),
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                     "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})

    async def account_action(self, request: Request, path: str, token: str):
        from zf.web.server import (_web_session_ttl_seconds, _web_unlock_retry_after,
                                   _record_web_unlock_failure, _WEB_UNLOCK_FAILURES)
        try:
            data = await request.json()
        except ValueError:
            return _json({"error": "请求格式错误"}, 400)
        if not isinstance(data, dict) or any(not isinstance(v, str) for v in data.values()):
            return _json({"error": "请求格式错误"}, 400)
        client_id = request.client.host if request.client else "local"
        if path == _BASE + "/login":
            async with self.login_lock:
                if _web_unlock_retry_after(client_id):
                    return _json({"error": "登录尝试过多，请稍后再试"}, 429)
                ttl = _web_session_ttl_seconds()
                new = await run_in_threadpool(self.account.login, data.get("username", ""), data.get("password", ""), ttl)
                if not new:
                    _record_web_unlock_failure(client_id)
                    return _json({"error": "用户名或密码错误"}, 401)
                _WEB_UNLOCK_FAILURES.pop(client_id, None)
            result = _json({"ok": True})
            result.set_cookie(self.cookie, new, max_age=ttl, httponly=True, samesite="strict",
                              secure=request.url.scheme == "https")
            return result
        if not self.account.valid(token):
            return _json({"error": "请重新登录"}, 401)
        if path == _BASE + "/password":
            if self.read_only:
                return _json({"error": "工作区为只读"}, 403)
            try:
                changed = await run_in_threadpool(self.account.change_password, token,
                                                  data.get("current", ""), data.get("password", ""))
            except ValueError:
                return _json({"error": "新密码须为 1 到 1024 字节"}, 400)
            if not changed:
                return _json({"error": "当前密码错误"}, 403)
        elif path in {_BASE + "/logout", "/api/web-session/lock"}:
            self.account.logout(token)
        else:
            return _json({"error": "未知操作"}, 404)
        result = _json({"ok": True})
        result.delete_cookie(self.cookie)
        return result

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        conn = HTTPConnection(scope)
        path = scope["path"]
        method = scope.get("method", "WEBSOCKET")
        token = conn.cookies.get(self.cookie, "")
        socket = kind == "websocket"
        safe = method in {"GET", "HEAD", "OPTIONS"}
        expected_origin = f"{'https' if scope['scheme'] in {'wss', 'https'} else 'http'}://{conn.headers.get('host', '')}"
        api_secret = os.environ.get("ZF_WEB_API_TOKEN", "")
        auth = conn.headers.get("authorization", "")
        api = bool(api_secret and auth.startswith("Bearer ") and path.startswith("/api/") and not socket
                   and secrets.compare_digest(auth[7:].encode(), api_secret.encode()))
        managed = self.managed_binding_present()
        valid = not managed and self.account.valid(token)
        response = None
        if path == "/readyz" and method == "GET":
            response = _json({"ok": True})
        elif managed:
            response = _json({"error": "managed_workspace"}, 403)
        elif not safe and not api and conn.headers.get("origin") != expected_origin:
            response = _json({"error": "invalid_origin"}, 403)
        elif method == "POST" and (path.startswith(_BASE + "/") or path == "/api/web-session/lock"):
            response = await self.account_action(Request(scope, receive), path, token)
        elif path == "/api/web-session/unlock":
            response = _json({"error": "local_account_required"}, 403)
        elif not (valid or api):
            if method == "GET" and not path.startswith(("/api/", "/__zf")) and "text/html" in conn.headers.get("accept", ""):
                response = self.page()
            else:
                response = _json({"error": "local_login_required"}, 401)
                response.headers["X-ZF-Login"] = "required"
        elif not safe and self.read_only:
            response = _json({"error": "read_only"}, 403)
        elif path == _BASE + "/account" and method == "GET":
            response = self.page(account_settings=True)
        if response is not None:
            if socket:
                return await send({"type": "websocket.close", "code": 4403})
            return await response(scope, receive, send)

        grant = current_grant.set(LocalGrant(not self.read_only, session_key(token) if valid else "api"))
        async def checked_receive():
            event = await receive()
            if socket and event["type"] == "websocket.receive" and (self.managed_binding_present() or not self.account.valid(token)):
                return {"type": "websocket.disconnect", "code": 4401}
            return event

        started = False

        async def private_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() != b"cache-control"]
                message = {**message, "headers": [*headers, (b"cache-control", b"private, no-store")]}
            await send(message)

        try:
            # Long-lived responses and idle terminal bridges must release on revocation.
            task = asyncio.create_task(self.app(scope, checked_receive, private_send))
            try:
                while True:
                    done, _ = await asyncio.wait({task}, timeout=2)
                    if done:
                        return await task
                    if self.managed_binding_present() or (not api and not self.account.valid(token)):
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        if socket:
                            await send({"type": "websocket.close", "code": 4401})
                        elif started:
                            await send({"type": "http.response.body", "body": b""})
                        else:
                            await _json({"error": "local_login_required"}, 401)(scope, receive, send)
                        return
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            current_grant.reset(grant)
