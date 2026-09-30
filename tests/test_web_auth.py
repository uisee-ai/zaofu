"""会话策略迁移后，真实 HTTP/WebSocket 路由仍共享同一授权状态。"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.web import server

# feature keeps the shared authorization state in server; do not import the
# v7-only extraction or replace feature's Web implementation.
web_auth = server


@pytest.fixture
def auth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("ZF_WEB_ACTION_TOKEN", "ZF_WEB_TRUSTED_SESSION", "ZF_WEB_PASSCODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ZF_WEB_PASSCODE", "test-only-passcode")
    state = tmp_path / "custom-state"
    state.mkdir()
    log = EventLog(state / "events.jsonl")
    log.append(ZfEvent(type="loop.started", actor="zf-cli"))
    sessions = dict(web_auth._WEB_SESSIONS)
    failures = dict(web_auth._WEB_UNLOCK_FAILURES)
    web_auth._WEB_SESSIONS.clear()
    web_auth._WEB_UNLOCK_FAILURES.clear()
    try:
        with TestClient(server.create_app(state, project_root=tmp_path)) as client:
            yield client, log
    finally:
        web_auth._WEB_SESSIONS.clear()
        web_auth._WEB_SESSIONS.update(sessions)
        web_auth._WEB_UNLOCK_FAILURES.clear()
        web_auth._WEB_UNLOCK_FAILURES.update(failures)


@pytest.mark.parametrize("revoke", ["lock", "expire"])
def test_route_revocation_invalidates_shared_session(auth_client, revoke: str):
    client, log = auth_client
    response = client.post(
        "/api/web-session/unlock", json={"passcode": "test-only-passcode"},
    )
    assert response.status_code == 200
    token = client.cookies.get("zf_web_session")
    assert token and token in web_auth._WEB_SESSIONS
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/web-session").json()["unlocked"] is True
    if revoke == "lock":
        assert client.post("/api/web-session/lock").status_code == 200
    else:
        web_auth._WEB_SESSIONS[token] = 0
    # Present the original cookie again: clearing the client cookie alone must
    # not be the reason a revoked session is rejected.
    client.cookies.set("zf_web_session", token)
    before = log.path.read_bytes()
    denied = client.post("/api/actions/create-task", json={"title": "must not exist"})
    assert denied.status_code == 403
    assert denied.json()["status"] == "unauthorized"
    assert client.get("/api/web-session").json()["unlocked"] is False
    assert token not in web_auth._WEB_SESSIONS
    assert log.path.read_bytes() == before


@pytest.mark.parametrize("endpoint", ["io", "control"])
def test_operator_socket_rejects_unauthorized_before_manager(auth_client, endpoint: str):
    client, log = auth_client
    before = log.path.read_bytes()
    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect(f"/api/operator/{endpoint}"):
            pytest.fail("unauthorized WebSocket was accepted")
    assert denied.value.code == 1008
    assert log.path.read_bytes() == before
