from __future__ import annotations

import json
import base64
import hashlib
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import pytest

from zf.core.config.schema import OpenClawRemoteBindingConfig
from zf.runtime.openclaw_provider import OpenClawGatewayClient


class _GatewayHandler(BaseHTTPRequestHandler):
    server: _GatewayServer

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length).decode("utf-8")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"raw": raw}
        self.server.seen.append({
            "path": self.path,
            "authorization": self.headers.get("Authorization") or "",
            "message_channel": self.headers.get("x-openclaw-message-channel") or "",
            "account_id": self.headers.get("x-openclaw-account-id") or "",
            "message_to": self.headers.get("x-openclaw-message-to") or "",
            "body": body,
        })
        status, payload = self.server.respond(body)
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return


class _GatewayServer(ThreadingHTTPServer):
    def __init__(
        self,
        responder: Callable[[dict[str, Any]], tuple[int, dict[str, Any]]],
    ) -> None:
        super().__init__(("127.0.0.1", 0), _GatewayHandler)
        self.respond = responder
        self.seen: list[dict[str, Any]] = []


class _ControlSocketServer:
    def __init__(self, *, reply: str) -> None:
        self.reply = reply
        self.seen: list[dict[str, Any]] = []
        self._closed = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._sock.settimeout(0.2)
        host, port = self._sock.getsockname()
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._closed.set()
        try:
            self._sock.close()
        finally:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._closed.is_set():
            try:
                conn, _addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with conn:
                self._handle(conn)
            return

    def _handle(self, conn: socket.socket) -> None:
        request = b""
        while b"\r\n\r\n" not in request:
            chunk = conn.recv(4096)
            if not chunk:
                return
            request += chunk
        headers = request.decode("ascii", errors="ignore")
        key = ""
        for line in headers.splitlines():
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
                break
        accept = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        conn.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "\r\n"
            ).encode("ascii")
        )
        while True:
            opcode, data = _read_client_frame(conn)
            if opcode == 8:
                return
            if opcode != 1:
                continue
            frame = json.loads(data.decode("utf-8"))
            self.seen.append(frame)
            method = frame.get("method")
            request_id = str(frame.get("id") or "")
            if method == "connect":
                _send_server_frame(
                    conn,
                    {
                        "type": "res",
                        "id": request_id,
                        "ok": True,
                        "payload": {
                            "auth": {
                                "role": "operator",
                                "scopes": ["operator.read", "operator.write"],
                            }
                        },
                    },
                )
                continue
            if method == "chat.send":
                params = frame.get("params") if isinstance(frame.get("params"), dict) else {}
                run_id = str(params.get("idempotencyKey") or "run-test")
                session_key = str(params.get("sessionKey") or "")
                _send_server_frame(
                    conn,
                    {
                        "type": "res",
                        "id": request_id,
                        "ok": True,
                        "payload": {"runId": run_id, "status": "started"},
                    },
                )
                _send_server_frame(
                    conn,
                    {
                        "type": "event",
                        "event": "agent",
                        "payload": {
                            "runId": run_id,
                            "sessionKey": session_key,
                            "stream": "assistant",
                            "data": {"text": self.reply},
                        },
                    },
                )
                _send_server_frame(
                    conn,
                    {
                        "type": "event",
                        "event": "agent",
                        "payload": {
                            "runId": run_id,
                            "sessionKey": session_key,
                            "stream": "lifecycle",
                            "data": {"phase": "end"},
                        },
                    },
                )
                return


def _read_client_frame(conn: socket.socket) -> tuple[int, bytes]:
    first = _recv_exact(conn, 2)
    opcode = first[0] & 0x0F
    masked = bool(first[1] & 0x80)
    length = first[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(conn, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(conn, 8))[0]
    mask = _recv_exact(conn, 4) if masked else b""
    payload = _recv_exact(conn, length) if length else b""
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


def _send_server_frame(conn: socket.socket, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode("utf-8")
    header = bytearray([0x81])
    if len(data) < 126:
        header.append(len(data))
    elif len(data) < 65536:
        header.append(126)
        header.extend(struct.pack("!H", len(data)))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", len(data)))
    conn.sendall(bytes(header) + data)


def _recv_exact(conn: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = conn.recv(remaining)
        if not chunk:
            raise RuntimeError("connection closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


@pytest.fixture
def gateway(
    request: pytest.FixtureRequest,
) -> tuple[str, list[dict[str, Any]]]:
    responder = request.param
    server = _GatewayServer(responder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", server.seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _binding(base_url: str) -> OpenClawRemoteBindingConfig:
    return OpenClawRemoteBindingConfig(
        id="remote",
        base_url=base_url,
        token_env="OPENCLAW_GATEWAY_TOKEN",
        timeout_seconds=2.0,
    )


def test_openclaw_run_turn_uses_control_rpc_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _ControlSocketServer(reply="OpenClaw channel reply")
    server.start()
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test-token")
    try:
        result = OpenClawGatewayClient().run_turn(
            _binding(server.base_url),
            agent_id="zaofu_demo_ch_openclaw_reviewer",
            prompt="channel prompt",
            system_prompt="system prompt",
            timeout_seconds=5.0,
            metadata={"request_id": "reply-test", "channel_id": "ch-demo"},
        )
    finally:
        server.close()

    assert result.ok is True
    assert result.reply == "OpenClaw channel reply"
    assert result.provider_session_id.startswith(
        "agent:main:zaofu_demo_ch_openclaw_reviewer:"
    )
    methods = [item["method"] for item in server.seen]
    assert methods == ["connect", "chat.send"]
    chat_params = server.seen[1]["params"]
    assert chat_params["sessionKey"] == "agent:main:zaofu_demo_ch_openclaw_reviewer"
    assert "system prompt" in chat_params["message"]
    assert "channel prompt" in chat_params["message"]


@pytest.mark.parametrize(
    "gateway",
    [
        lambda body: (
            200,
            {
                "ok": True,
                "result": {
                    "content": [{"type": "text", "text": "OpenClaw OK"}],
                    "details": {"ok": True, "sessionKey": "agent:main:main"},
                },
            },
        ),
    ],
    indirect=True,
)
def test_openclaw_preflight_uses_core_status_tool(
    gateway: tuple[str, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url, seen = gateway
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test-token")

    result = OpenClawGatewayClient().preflight(_binding(base_url))

    assert result.ok is True
    assert result.reply == "OpenClaw OK"
    assert seen[0]["path"] == "/tools/invoke"
    assert seen[0]["authorization"] == "Bearer test-token"
    assert seen[0]["body"]["tool"] == "session_status"
    assert seen[0]["body"]["name"] == "session_status"
    assert seen[0]["body"]["args"] == {}
    assert seen[0]["body"]["arguments"] == {}


@pytest.mark.parametrize(
    "gateway",
    [
        lambda body: (
            200,
            {
                "ok": True,
                "result": {
                    "ok": True,
                    "payload": {
                        "details": {
                            "messageId": "om_sent",
                            "chatId": "oc_group",
                        },
                    },
                },
            },
        ),
    ],
    indirect=True,
)
def test_openclaw_send_message_uses_message_tool(
    gateway: tuple[str, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url, seen = gateway
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test-token")

    result = OpenClawGatewayClient().send_message(
        _binding(base_url),
        channel="feishu",
        account_id="default",
        target="chat:oc_group",
        message="hello from ZaoFu",
        agent_id="zaofu-bridge",
        idempotency_key="idem-1",
    )

    assert result.ok is True
    assert seen[0]["authorization"] == "Bearer test-token"
    assert seen[0]["message_channel"] == "feishu"
    assert seen[0]["account_id"] == "default"
    assert seen[0]["message_to"] == "chat:oc_group"
    assert seen[0]["body"]["tool"] == "message"
    assert seen[0]["body"]["name"] == "message"
    assert seen[0]["body"]["action"] == "send"
    assert seen[0]["body"]["idempotencyKey"] == "idem-1"
    assert seen[0]["body"]["sessionKey"] == "agent:zaofu-bridge:message"
    assert seen[0]["body"]["args"]["to"] == "chat:oc_group"
    assert seen[0]["body"]["args"]["message"] == "hello from ZaoFu"
    assert seen[0]["body"]["args"]["accountId"] == "default"
    assert seen[0]["body"]["args"]["channel"] == "feishu"
    assert seen[0]["body"]["arguments"] == seen[0]["body"]["args"]


@pytest.mark.parametrize(
    "gateway",
    [
        lambda body: (
            (
                404,
                {
                    "ok": False,
                    "error": {
                        "type": "not_found",
                        "message": "Tool not available: session_status",
                    },
                },
            )
            if body.get("tool") == "session_status"
            else (
                200,
                {
                    "ok": True,
                    "result": {
                        "content": [{"type": "text", "text": '{"count": 0}'}],
                        "details": {"count": 0, "sessions": []},
                    },
                },
            )
        ),
    ],
    indirect=True,
)
def test_openclaw_preflight_falls_back_when_status_tool_is_missing(
    gateway: tuple[str, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_url, seen = gateway
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "test-token")

    result = OpenClawGatewayClient().preflight(_binding(base_url))

    assert result.ok is True
    assert [item["body"]["tool"] for item in seen] == [
        "session_status",
        "sessions_list",
    ]
