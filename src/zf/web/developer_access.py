"""Server-verified, revocable developer sessions; never expose the Develop Token."""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

COOKIE = "zf_developer_session"
_verified: ContextVar[bool] = ContextVar("settings_developer_verified", default=False)


def developer_request_allowed() -> bool:
    return _verified.get()


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        return False
    if not origin:
        return True  # Local CLI clients still need the token/cookie.
    expected = urlsplit(str(request.base_url))
    supplied = urlsplit(origin)
    return (supplied.scheme, supplied.netloc) == (expected.scheme, expected.netloc)


class DeveloperSessions:
    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "web" / "developer-sessions.sqlite3"
        self._lock = threading.Lock()
        self.failures: dict[str, list[float]] = {}

    @staticmethod
    def configured_token() -> str:
        return os.environ.get("ZF_WEB_DEVELOPER_TOKEN", "")

    @staticmethod
    def lifetime() -> int:
        try:
            return max(3600, min(int(os.environ.get("ZF_WEB_DEVELOPER_SESSION_SECONDS", 30 * 86400)), 365 * 86400))
        except ValueError:
            return 30 * 86400

    def _fingerprint(self) -> str:
        return hmac.new(self.configured_token().encode(), b"zf-developer-session-v1", "sha256").hexdigest()

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # O_NOFOLLOW prevents credential state being redirected outside its directory.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        conn = sqlite3.connect(self.path, timeout=3)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires REAL, fingerprint TEXT)")
            with conn:
                yield conn
        finally:
            conn.close()

    def lookup(self, session: str | None) -> float | None:
        if not session or len(session) > 256 or not self.configured_token() or not self.path.exists():
            return None
        try:
            with self._lock, self._connect() as db:
                row = db.execute("SELECT expires,fingerprint FROM sessions WHERE digest=?", (hashlib.sha256(session.encode()).hexdigest(),)).fetchone()
        except (sqlite3.Error, OSError):
            # Damaged credential state must deny developer access, not take down
            # every public/read-only route through the authentication middleware.
            return None
        if row and row[0] > time.time() and hmac.compare_digest(row[1], self._fingerprint()):
            return float(row[0])
        return None

    def unlock(self, token: str, client: str) -> tuple[str | None, int]:
        now = time.time()
        with self._lock:
            if len(self.failures) > 4096:
                self.failures = {k: v for k, v in self.failures.items() if v and v[-1] > now - 300}
                if len(self.failures) > 4096:
                    return None, 429
            attempts = [t for t in self.failures.get(client, []) if t > now - 300]
            self.failures[client] = attempts
            if len(attempts) >= 5:
                return None, 429
            configured = self.configured_token()
            if not configured or not token or len(token) > 8192 or not hmac.compare_digest(token.encode(), configured.encode()):
                attempts.append(now)
                return None, 403
            session = secrets.token_urlsafe(32)
            with self._connect() as db:
                db.execute("DELETE FROM sessions WHERE expires <= ? OR fingerprint != ?", (now, self._fingerprint()))
                db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(session.encode()).hexdigest(), now + self.lifetime(), self._fingerprint()))
            self.failures.pop(client, None)
            return session, 200

    def revoke(self, session: str | None):
        if session and self.path.exists():
            with self._lock, self._connect() as db:
                db.execute("DELETE FROM sessions WHERE digest=?", (hashlib.sha256(session.encode()).hexdigest(),))


def install_developer_access(app, state_dir: Path) -> None:
    sessions = DeveloperSessions(state_dir)
    router = APIRouter()

    @app.middleware("http")
    async def developer_context(request: Request, call_next):
        allowed = sessions.lookup(request.cookies.get(COOKIE)) is not None
        if allowed and request.method not in {"GET", "HEAD", "OPTIONS"} and not same_origin(request):
            return JSONResponse({"ok": False, "reason": "Cross-origin mutation rejected."}, status_code=403)
        token = _verified.set(allowed)
        try:
            return await call_next(request)
        finally:
            _verified.reset(token)

    def status(request: Request) -> dict:
        expires = sessions.lookup(request.cookies.get(COOKIE))
        return {"configured": bool(sessions.configured_token()), "verified": expires is not None, "expires_at": expires}

    @router.get("/api/settings/access")
    def get_access(request: Request):
        return JSONResponse(status(request), headers={"Cache-Control": "no-store"})

    @router.post("/api/settings/access/unlock")
    async def unlock_access(request: Request):
        if not same_origin(request):
            return JSONResponse({"ok": False, "reason": "Cross-origin request rejected."}, status_code=403)
        raw = await request.body()
        if len(raw) > 10000:
            return JSONResponse({"ok": False, "reason": "Invalid request."}, status_code=413)
        try:
            import json
            body = json.loads(raw)
            value = body.get("token") if isinstance(body, dict) else None
        except (ValueError, TypeError):
            value = None
        if not isinstance(value, str):
            return JSONResponse({"ok": False, "reason": "A Develop Token is required."}, status_code=422)
        try:
            session, code = sessions.unlock(value, request.client.host if request.client else "unknown")
        except (sqlite3.Error, OSError):
            return JSONResponse({"ok": False, "reason": "Developer session storage is unavailable."}, status_code=503, headers={"Cache-Control": "no-store"})
        if not session:
            return JSONResponse({"ok": False, "reason": "Too many attempts. Retry in five minutes." if code == 429 else "Invalid or unconfigured Develop Token."}, status_code=code, headers={"Cache-Control": "no-store"})
        old = request.cookies.get(COOKIE)
        if old:
            sessions.revoke(old)
        response = JSONResponse({"ok": True, "configured": True, "verified": True, "expires_at": time.time() + sessions.lifetime()}, headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE, session, httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=sessions.lifetime(), path="/")
        return response

    @router.post("/api/settings/access/forget")
    def forget_access(request: Request):
        if not same_origin(request):
            return JSONResponse({"ok": False, "reason": "Cross-origin request rejected."}, status_code=403)
        sessions.revoke(request.cookies.get(COOKIE))
        response = JSONResponse({"ok": True, "verified": False}, headers={"Cache-Control": "no-store"})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return response

    app.include_router(router)
