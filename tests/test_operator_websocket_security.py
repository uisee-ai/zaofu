"""Legacy Operator sockets must enforce the browser origin boundary."""

from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from zf.web import server


@pytest.mark.parametrize("route", ["/api/operator/io", "/api/operator/control",
                                  "/api/projects/default/operator/io",
                                  "/api/projects/default/operator/control"])
@pytest.mark.parametrize("origin", [None, "https://untrusted.example", "https://testserver"])
def test_operator_rejects_foreign_or_missing_origin(tmp_path, monkeypatch, route, origin):
    monkeypatch.setenv("ZF_WEB_TRUSTED_SESSION", "1")
    monkeypatch.setattr(server, "_resolve_api_project", lambda *a, **kw:
                        SimpleNamespace(state_dir=tmp_path, project_root=tmp_path))
    manager = Mock(side_effect=AssertionError("unauthorized terminal access"))
    monkeypatch.setattr(server, "_operator_session_manager", manager)
    app = server.create_app(tmp_path, project_root=tmp_path)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect(route, headers={"origin": origin} if origin else {}):
                pass
    assert denied.value.code == 1008
    manager.assert_not_called()


@pytest.mark.parametrize("mode", ["trusted", "token", "passcode", "unauthorized"])
def test_same_origin_still_requires_authorization(monkeypatch, mode):
    monkeypatch.delenv("ZF_WEB_TRUSTED_SESSION", raising=False)
    monkeypatch.setenv("ZF_WEB_ACTION_TOKEN", "test-only-action-token")
    headers = {"origin": "http://testserver", "host": "testserver"}
    cookies = {}
    if mode == "trusted":
        monkeypatch.setenv("ZF_WEB_TRUSTED_SESSION", "1")
    elif mode == "token":
        headers["x-zf-web-token"] = "test-only-action-token"
    elif mode == "passcode":
        cookies[server._WEB_SESSION_COOKIE] = "test-only-session"
    monkeypatch.setattr(server, "_web_session_token_valid", lambda value: value == "test-only-session")
    socket = SimpleNamespace(headers=headers, query_params={}, cookies=cookies,
                             url=SimpleNamespace(scheme="ws"))
    assert server._operator_websocket_authorized(socket) is (mode != "unauthorized")


@pytest.mark.parametrize("kind", ["io", "control"])
def test_same_origin_operator_socket_preserves_io_and_control(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("ZF_WEB_TRUSTED_SESSION", "1")
    manager = Mock()
    manager.attach_raw_output.return_value = lambda: None
    manager.raw_output_since.return_value = [b"ready"]
    written = threading.Event()
    manager.write_raw.side_effect = lambda data: written.set()
    monkeypatch.setattr(server, "_operator_session_manager", lambda *a, **kw: manager)
    monkeypatch.setattr(server, "_operator_session_status", lambda *a, **kw: {})
    with TestClient(server.create_app(tmp_path, project_root=tmp_path)) as client:
        with client.websocket_connect(f"/api/operator/{kind}", headers={"origin": "http://testserver"}) as socket:
            if kind == "io":
                assert socket.receive_bytes() == b"ready"
                socket.send_bytes(b"input")
                assert written.wait(3)
                manager.write_raw.assert_called_once_with(b"input")
            else:
                assert socket.receive_json()["type"] == "restore"
                assert socket.receive_json()["type"] == "state"
                socket.send_json({"type": "stop"})
                assert socket.receive_json()["type"] == "exit"
                manager.stop.assert_called_once()
