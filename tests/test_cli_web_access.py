"""Launch the actual Dashboard and exercise standalone credentials at its HTTP boundary."""
import argparse
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from zf.cli import web
from zf.cli.web_access import configure_web_access, dashboard_launch_url
from zf.core.workspace.web_account import LocalAccount
from zf.web.server import _web_mutation_auth_error, _web_session_projection

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture(autouse=True)
def isolate_access(tmp_path, monkeypatch):
    with patch.dict(os.environ):
        for key in ("ZF_WEB_API_TOKEN", "ZF_WEB_ACTION_TOKEN", "ZF_WEB_PASSCODE",
                    "ZF_WEB_TRUSTED_SESSION", "ZF_WEB_ACTION_TOKEN_FILE"):
            os.environ.pop(key, None)
        monkeypatch.setenv("ZF_WORKSPACE_HOME", str(tmp_path / "workspace"))
        yield


@pytest.fixture
def launch(tmp_path, monkeypatch):
    import uvicorn
    monkeypatch.chdir(tmp_path)
    (tmp_path / "zf.yaml").write_text('version: "1.0"\nproject: {name: access-test, state_dir: runtime-state}\n')
    (tmp_path / "runtime-state").mkdir()
    monkeypatch.setattr(web, "_load_project_env", lambda _: None)
    apps = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **_: apps.append(app))

    def start(host="127.0.0.1", read_only=False):
        args = argparse.Namespace(host=host, port=8002, state_dir=None, reload=False,
                                  workspace_only=False, read_only=read_only)
        assert web.run(args) == 0
        return apps[-1]
    return start


def login(client, password="zaofu"):
    return client.post("/auth/local/login", json={"username": "zaofu", "password": password},
                       headers=ORIGIN)


@pytest.mark.parametrize("host,read_only", [("127.0.0.1", False), ("0.0.0.0", False), ("127.0.0.1", True)])
def test_cli_launch_access_controls_real_task_creation(launch, tmp_path, capsys, host, read_only):
    from zf.core.task.store import TaskStore
    payload = {"title": "Write permission proof", "contract": {"behavior": "render board", "verification": "typecheck"}}
    with TestClient(launch(host, read_only)) as client:
        assert client.get("/readyz").json() == {"ok": True}
        assert client.get("/api/workspace/onboarding").status_code == 401
        assert client.get("/health").status_code == 401
        assert "登录本地工作区" in client.get("/?page=project", headers={"accept": "text/html"}).text
        font = client.get("/auth/local/geist-latin.woff2")
        assert font.headers["content-type"] == "font/woff2" and font.content.startswith(b"wOF2")
        assert client.get("/auth/local/other.woff2").status_code == 401
        assert login(client, "wrong").status_code == 401
        assert login(client).status_code == 200
        session = client.get("/api/web-session").json()
        assert session["mode"] == ("read_only" if read_only else "local_account")
        assert session["username"] == "zaofu"
        account_link = client.get("/auth/local/account", follow_redirects=False)
        assert account_link.status_code == 303
        assert account_link.headers["location"] == "/?page=settings&settings=password"
        assert client.post("/api/actions/create-task", json=payload, headers={"origin": "http://other"}).status_code == 403
        created = client.post("/api/actions/create-task", json=payload, headers=ORIGIN)
        assert created.status_code == (403 if read_only else 201), created.text
        if not read_only:
            assert TaskStore(tmp_path / "runtime-state" / "kanban.json").get(created.json()["task_id"])
    assert "#" not in dashboard_launch_url(host, 8002)
    assert f"http://{host}:8002/" in capsys.readouterr().out


def test_password_change_logout_and_restart(launch):
    app = launch()
    with TestClient(app) as a, TestClient(app) as b:
        assert login(a).is_success and login(b).is_success
        old = dict(a.cookies)
        assert a.post("/auth/local/logout", headers=ORIGIN, json={}).is_success
        a.cookies.update(old)
        assert a.get("/api/workspace/projects").status_code == 401
        assert b.get("/api/workspace/projects").is_success
        assert login(a).is_success
        assert b.post("/auth/local/password", headers=ORIGIN,
                      json={"current": "zaofu", "password": "replacement"}).is_success
        assert a.get("/api/workspace/projects").status_code == 401
    with TestClient(launch()) as new:
        assert login(new).status_code == 401
        assert login(new, "replacement").is_success
    assert LocalAccount().path.stat().st_mode & 0o777 == 0o600
    assert "replacement" not in LocalAccount().path.read_text()


def test_legacy_token_and_explicit_migration(launch, monkeypatch):
    account = LocalAccount()
    account.path.parent.mkdir()
    account.path.with_name("web-action-token").write_text("legacy-token")
    assert configure_web_access(host="0.0.0.0") is None
    assert _web_session_projection()["mode"] == "token_required"
    assert dashboard_launch_url("0.0.0.0", 8002).endswith("#zf-action-token=legacy-token")
    from zf.cli.web_access import setup_account
    monkeypatch.setattr("getpass.getpass", lambda prompt: "migrated")
    setup_account(reset=False)
    monkeypatch.setenv("ZF_WEB_API_TOKEN", "script-credential")
    with TestClient(launch()) as client:
        assert client.get("/api/workspace/projects", headers={"x-zf-web-token": "legacy-token"}).status_code == 401
        assert login(client).status_code == 401
        machine = {"authorization": "Bearer script-credential"}
        assert client.get("/api/workspace/projects", headers=machine).is_success
        assert client.post("/auth/local/password", headers={**machine, **ORIGIN}, json={}).status_code == 401
        assert login(client, "migrated").is_success


@pytest.mark.parametrize("key,value,mode", [
    ("ZF_WEB_PASSCODE", "explicit-passcode", "remote_passcode"),
    ("ZF_WEB_ACTION_TOKEN", "explicit-token", "token_required"),
    ("ZF_WEB_TRUSTED_SESSION", "1", "local_trusted"),
])
def test_existing_access_policy_is_preserved(monkeypatch, key, value, mode):
    monkeypatch.setenv(key, value)
    assert configure_web_access(host="127.0.0.1") is None
    assert os.environ[key] == value
    assert _web_session_projection()["mode"] == mode


def test_explicit_read_only_disables_old_write_credentials(monkeypatch):
    for key in ("ZF_WEB_ACTION_TOKEN", "ZF_WEB_PASSCODE", "ZF_WEB_TRUSTED_SESSION"):
        monkeypatch.setenv(key, "1")
    configure_web_access(host="0.0.0.0", read_only=True)
    assert _web_session_projection()["mode"] == "read_only"
    assert _web_mutation_auth_error("create-task", authorization=None, x_zf_web_token="1")["status"] == "disabled"


def test_live_takeover_retires_standalone_credentials(launch, monkeypatch):
    monkeypatch.setenv("ZF_WEB_API_TOKEN", "explicit-api")
    with TestClient(launch("127.0.0.1")) as client:
        assert login(client, "zaofu").is_success
        pin = LocalAccount().path.parent / "controller-binding.json"
        pin.write_text("{}")
        assert login(client, "zaofu").status_code == 403
        assert client.get("/api/workspace/projects").status_code == 403
        assert client.get("/api/workspace/projects", headers={"Authorization": "Bearer explicit-api"}).status_code == 403


def test_corrupt_record_never_reinitializes(tmp_path):
    account = LocalAccount()
    account.initialize("private-password")
    account.path.write_text("{}")
    with pytest.raises(RuntimeError, match="reset-password"):
        configure_web_access(host="127.0.0.1")
    assert account.path.read_text() == "{}"

def test_websocket_and_ticket_follow_the_issuing_login(tmp_path):
    from fastapi import FastAPI, WebSocket
    from starlette.websockets import WebSocketDisconnect
    from zf.web.local_login import LocalLoginMiddleware, current_grant
    from zf.web.terminal_backend import TerminalRuntimeError
    from zf.web.terminal_gateway import AttachmentTicketStore
    account = LocalAccount(tmp_path)
    account.initialize()
    app = FastAPI()
    app.add_middleware(LocalLoginMiddleware, account=account, read_only=False)
    tickets = AttachmentTicketStore()
    @app.post("/ticket")
    def issue():
        return tickets.issue(project_id="p", session_id="s", mode="control", takeover=False,
                             cols=80, rows=24, ttl_seconds=30, max_attachments=8,
                             browser_binding=current_grant.get().binding).token

    @app.websocket("/terminal/{ticket}")
    async def echo(ws: WebSocket, ticket: str):
        try:
            tickets.consume(ticket, project_id="p", session_id="s", mode="control",
                            browser_binding=current_grant.get().binding)
        except TerminalRuntimeError:
            await ws.close(code=4403)
            return
        await ws.accept()
        try:
            while True:
                await ws.send_text(await ws.receive_text())
        except WebSocketDisconnect:
            await ws.close(code=4401)

    with TestClient(app) as a, TestClient(app) as b:
        assert login(a).is_success and login(b).is_success
        ticket = a.post("/ticket", headers=ORIGIN).json()
        with pytest.raises(WebSocketDisconnect):
            with b.websocket_connect("/terminal/" + ticket, headers=ORIGIN):
                pass
        ticket = a.post("/ticket", headers=ORIGIN).json()
        with a.websocket_connect("/terminal/" + ticket, headers=ORIGIN) as ws:
            ws.send_text("input")
            assert ws.receive_text() == "input"
            assert a.post("/auth/local/logout", headers=ORIGIN, json={}).is_success
            ws.send_text("stale input")
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
