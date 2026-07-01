"""OpenClaw Control WebSocket RPC client used by channel providers."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import socket
import ssl
import struct
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any

from zf.core.security.redaction import redact_obj, redact_text


@dataclass(frozen=True)
class OpenClawControlChatResult:
    ok: bool
    status: str
    reason: str = ""
    provider_session_id: str = ""
    reply: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


def run_openclaw_control_chat_turn(
    *,
    base_url: str,
    token: str,
    agent_id: str,
    prompt: str,
    system_prompt: str,
    timeout_seconds: float,
    metadata: dict[str, Any],
) -> OpenClawControlChatResult:
    session_key = _control_session_key(agent_id)
    run_id = str(metadata.get("request_id") or uuid.uuid4())
    message = _control_chat_message(
        prompt=prompt,
        system_prompt=system_prompt,
        metadata=metadata,
    )
    return _OpenClawControlSocket(
        base_url,
        timeout_seconds=min(max(float(timeout_seconds or 120.0), 1.0), 300.0),
    ).run_chat_turn(
        token=token,
        session_key=session_key,
        message=message,
        run_id=run_id,
    )


def _control_session_key(agent_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9_.:-]+", "-", agent_id).strip(".:-")
    return f"agent:main:{suffix[:96] or 'zaofu-channel'}"


def _control_chat_message(
    *,
    prompt: str,
    system_prompt: str,
    metadata: dict[str, Any],
) -> str:
    return "\n\n".join([
        system_prompt.strip(),
        "OpenClaw should answer this ZaoFu Agent Channel request. "
        "Return only the channel reply text; do not call tools or mutate state.",
        f"metadata: {json.dumps(redact_obj(metadata), ensure_ascii=False, sort_keys=True)}",
        prompt.strip(),
    ]).strip()


class _OpenClawControlSocket:
    def __init__(self, base_url: str, *, timeout_seconds: float) -> None:
        self.url = _control_ws_url(base_url)
        self.timeout_seconds = timeout_seconds
        self.sock: socket.socket | ssl.SSLSocket | None = None

    def run_chat_turn(
        self,
        *,
        token: str,
        session_key: str,
        message: str,
        run_id: str,
    ) -> OpenClawControlChatResult:
        started_at = time.monotonic()
        reply = ""
        actual_run_id = run_id
        try:
            self._connect()
            hello = self.request(
                "connect",
                {
                    "minProtocol": 4,
                    "maxProtocol": 4,
                    "client": {
                        "id": "openclaw-tui",
                        "version": "zaofu",
                        "platform": "python",
                        "mode": "backend",
                    },
                    "role": "operator",
                    "scopes": [
                        "operator.admin",
                        "operator.read",
                        "operator.write",
                        "operator.approvals",
                        "operator.pairing",
                    ],
                    "caps": ["tool-events"],
                    "auth": {"token": token},
                    "userAgent": "zaofu-openclaw-provider",
                    "locale": "en-US",
                },
                deadline=started_at + min(self.timeout_seconds, 20.0),
            )
            auth = hello.get("auth") if isinstance(hello, dict) else {}
            scopes = auth.get("scopes") if isinstance(auth, dict) else []
            if "operator.write" not in scopes:
                return OpenClawControlChatResult(
                    ok=False,
                    status="control_rpc_unauthorized",
                    reason="OpenClaw control RPC missing operator.write scope",
                    provider_session_id=session_key,
                    payload=_safe_payload({"hello": hello}),
                )

            sent = self.request(
                "chat.send",
                {
                    "sessionKey": session_key,
                    "message": message,
                    "idempotencyKey": run_id,
                },
                deadline=started_at + min(self.timeout_seconds, 30.0),
            )
            if isinstance(sent, dict) and str(sent.get("runId") or "").strip():
                actual_run_id = str(sent.get("runId") or "")

            while time.monotonic() < started_at + self.timeout_seconds:
                frame = self.recv_json(deadline=started_at + self.timeout_seconds)
                if not isinstance(frame, dict) or frame.get("type") != "event":
                    continue
                if frame.get("event") != "agent":
                    continue
                payload = frame.get("payload")
                if not isinstance(payload, dict):
                    continue
                if str(payload.get("sessionKey") or "") != session_key:
                    continue
                event_run_id = str(payload.get("runId") or "")
                if event_run_id and event_run_id != actual_run_id:
                    continue
                stream = str(payload.get("stream") or "")
                data = payload.get("data")
                data = data if isinstance(data, dict) else {}
                if stream == "assistant":
                    text = str(data.get("text") or "")
                    delta = str(data.get("delta") or "")
                    if text:
                        reply = text
                    elif delta:
                        reply += delta
                    continue
                if stream == "lifecycle":
                    phase = str(data.get("phase") or "")
                    if phase in {"error", "failed"}:
                        return OpenClawControlChatResult(
                            ok=False,
                            status="failed",
                            reason=redact_text(
                                str(
                                    data.get("reason")
                                    or data.get("error")
                                    or "OpenClaw run failed"
                                )
                            ),
                            provider_session_id=f"{session_key}:{actual_run_id}",
                            reply=reply,
                            payload=_safe_payload({"event": frame}),
                        )
                    if phase == "end":
                        return OpenClawControlChatResult(
                            ok=True,
                            status="completed",
                            provider_session_id=f"{session_key}:{actual_run_id}",
                            reply=reply.strip(),
                            payload=_safe_payload({"send": sent}),
                        )
            if reply.strip():
                return OpenClawControlChatResult(
                    ok=True,
                    status="completed",
                    provider_session_id=f"{session_key}:{actual_run_id}",
                    reply=reply.strip(),
                    payload=_safe_payload({"send": sent}),
                )
            return OpenClawControlChatResult(
                ok=False,
                status="timeout",
                reason="OpenClaw control RPC timed out waiting for assistant reply",
                provider_session_id=f"{session_key}:{actual_run_id}",
            )
        finally:
            self.close()

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        deadline: float,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        self.send_json({"type": "req", "id": request_id, "method": method, "params": params})
        while time.monotonic() < deadline:
            frame = self.recv_json(deadline=deadline)
            if not isinstance(frame, dict) or frame.get("type") != "res":
                continue
            if str(frame.get("id") or "") != request_id:
                continue
            if frame.get("ok") is True:
                payload = frame.get("payload")
                return payload if isinstance(payload, dict) else {"value": payload}
            error = frame.get("error")
            if isinstance(error, dict):
                code = str(error.get("code") or "ERR")
                message = str(error.get("message") or "request failed")
                raise RuntimeError(f"{method} {code}: {message}")
            raise RuntimeError(f"{method} request failed")
        raise TimeoutError(f"{method} timed out")

    def send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._send_frame(data, opcode=1)

    def recv_json(self, *, deadline: float) -> dict[str, Any]:
        while time.monotonic() < deadline:
            opcode, data = self._recv_frame(deadline=deadline)
            if opcode == 1:
                try:
                    parsed = json.loads(data.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            if opcode == 8:
                raise RuntimeError("OpenClaw control socket closed")
            if opcode == 9:
                self._send_frame(data, opcode=10)
        raise TimeoutError("OpenClaw control socket timed out")

    def close(self) -> None:
        if self.sock is None:
            return
        try:
            self._send_frame(b"", opcode=8)
        except OSError:
            pass
        try:
            self.sock.close()
        finally:
            self.sock = None

    def _connect(self) -> None:
        parts = urllib.parse.urlsplit(self.url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        raw = socket.create_connection((host, port), timeout=self.timeout_seconds)
        raw.settimeout(self.timeout_seconds)
        if parts.scheme == "wss":
            context = ssl.create_default_context()
            sock: socket.socket | ssl.SSLSocket = context.wrap_socket(raw, server_hostname=host)
        else:
            sock = raw
        self.sock = sock
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        target = parts.path or "/"
        if parts.query:
            target = f"{target}?{parts.query}"
        host_header = host if parts.port is None else f"{host}:{port}"
        origin_scheme = "https" if parts.scheme == "wss" else "http"
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Origin: {origin_scheme}://{host_header}\r\n"
            "User-Agent: zaofu-openclaw-provider\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = self._recv_until_headers()
        status_line = response.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
        if " 101 " not in status_line:
            raise RuntimeError(f"websocket upgrade failed: {status_line}")
        expected = base64.b64encode(
            hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
            ).digest()
        ).decode("ascii")
        if expected not in response.decode("ascii", errors="ignore"):
            raise RuntimeError("websocket upgrade failed: invalid accept key")

    def _recv_until_headers(self) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = self._sock().recv(4096)
            if not chunk:
                raise RuntimeError("websocket upgrade failed: connection closed")
            chunks.append(chunk)
            data = b"".join(chunks)
            if b"\r\n\r\n" in data:
                return data
            if len(data) > 64_000:
                raise RuntimeError("websocket upgrade failed: headers too large")

    def _send_frame(self, payload: bytes, *, opcode: int) -> None:
        mask = secrets.token_bytes(4)
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.extend([0x80 | 126])
            header.extend(struct.pack("!H", length))
        else:
            header.extend([0x80 | 127])
            header.extend(struct.pack("!Q", length))
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._sock().sendall(bytes(header) + mask + masked)

    def _recv_frame(self, *, deadline: float) -> tuple[int, bytes]:
        sock = self._sock()
        sock.settimeout(max(0.1, min(self.timeout_seconds, deadline - time.monotonic())))
        first = self._recv_exact(2)
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _recv_exact(self, count: int) -> bytes:
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = self._sock().recv(remaining)
            if not chunk:
                raise RuntimeError("OpenClaw control socket closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _sock(self) -> socket.socket | ssl.SSLSocket:
        if self.sock is None:
            raise RuntimeError("OpenClaw control socket is not connected")
        return self.sock


def _control_ws_url(base_url: str) -> str:
    parts = urllib.parse.urlsplit(base_url.rstrip("/") or base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    path = parts.path or "/"
    return urllib.parse.urlunsplit((scheme, parts.netloc, path, parts.query, ""))


def _safe_payload(value: Any) -> dict[str, Any]:
    redacted = redact_obj(value)
    return redacted if isinstance(redacted, dict) else {"value": redacted}
