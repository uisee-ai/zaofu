"""Authenticated, bounded WebSocket relay for Herdr terminal NDJSON."""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
import json
import secrets
from threading import Lock
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from zf.core.config.schema import RuntimeWebTerminalConfig
from zf.web.terminal_backend import TerminalBridgeSpec, TerminalRuntimeError
from zf.web.terminal_environment import terminal_subprocess_env


WS_SUBPROTOCOL = "zf-terminal-v1"


@dataclass(frozen=True)
class AttachmentTicket:
    token: str
    project_id: str
    session_id: str
    mode: str
    takeover: bool
    cols: int
    rows: int
    expires_at: float
    browser_binding: str


@dataclass(frozen=True)
class ConsumedAttachment:
    attachment_id: str
    ticket: AttachmentTicket


class AttachmentTicketStore:
    """Short-lived one-shot tickets held only in server/browser memory."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._tickets: dict[str, AttachmentTicket] = {}
        self._active: dict[str, tuple[str, str]] = {}

    def _cleanup(self, now: float) -> None:
        expired = [token for token, ticket in self._tickets.items() if ticket.expires_at <= now]
        for token in expired:
            self._tickets.pop(token, None)

    def issue(
        self,
        *,
        project_id: str,
        session_id: str,
        mode: str,
        takeover: bool,
        cols: int,
        rows: int,
        ttl_seconds: int,
        max_attachments: int,
        browser_binding: str = "",
    ) -> AttachmentTicket:
        now = time.monotonic()
        with self._lock:
            self._cleanup(now)
            active = sum(
                key == (project_id, session_id) for key in self._active.values()
            )
            pending = sum(
                ticket.project_id == project_id and ticket.session_id == session_id
                for ticket in self._tickets.values()
            )
            if active + pending >= max_attachments:
                raise TerminalRuntimeError(
                    "terminal_attachment_limit",
                    f"terminal attachment limit is {max_attachments}",
                    status_code=429,
                )
            token = secrets.token_urlsafe(32)
            ticket = AttachmentTicket(
                token=token,
                project_id=project_id,
                session_id=session_id,
                mode=mode,
                takeover=takeover,
                cols=cols,
                rows=rows,
                expires_at=now + ttl_seconds,
                browser_binding=browser_binding,
            )
            self._tickets[token] = ticket
            return ticket

    def consume(
        self,
        token: str,
        *,
        project_id: str,
        session_id: str,
        mode: str,
        browser_binding: str = "",
    ) -> ConsumedAttachment:
        now = time.monotonic()
        with self._lock:
            self._cleanup(now)
            ticket = self._tickets.pop(token, None)
            if ticket is None:
                raise TerminalRuntimeError(
                    "invalid_terminal_ticket", "attachment ticket is invalid or expired", status_code=403
                )
            if (
                ticket.project_id != project_id
                or ticket.session_id != session_id
                or ticket.browser_binding != browser_binding
                or ticket.mode != mode
            ):
                raise TerminalRuntimeError(
                    "terminal_ticket_scope_mismatch",
                    "attachment ticket does not match this Project/session/mode",
                    status_code=403,
                )
            attachment_id = f"att-{secrets.token_hex(8)}"
            self._active[attachment_id] = (project_id, session_id)
            return ConsumedAttachment(attachment_id=attachment_id, ticket=ticket)

    def release(self, attachment_id: str) -> None:
        with self._lock:
            self._active.pop(attachment_id, None)


def ticket_from_subprotocol_header(raw: str) -> str:
    protocols = [value.strip() for value in raw.split(",") if value.strip()]
    if not protocols or protocols[0] != WS_SUBPROTOCOL or len(protocols) != 2:
        return ""
    return protocols[1]


class BridgeProtocolError(RuntimeError):
    pass


class BridgeBackpressureError(RuntimeError):
    pass


class HerdrNDJSONBridge:
    def __init__(self, spec: TerminalBridgeSpec, config: RuntimeWebTerminalConfig) -> None:
        self.spec = spec
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self._last_seq: int | None = None
        self._has_full_frame = False
        self._stderr_tail = bytearray()
        self._stderr_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        line_limit = max(64 * 1024, self.config.max_frame_bytes * 2)
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.spec.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=line_limit,
                env=terminal_subprocess_env(),
            )
        except FileNotFoundError as exc:
            raise TerminalRuntimeError(
                "herdr_unavailable", "Herdr bridge binary was not found", status_code=503
            ) from exc
        except OSError as exc:
            raise TerminalRuntimeError(
                "herdr_unavailable", "Herdr bridge process could not start", status_code=503
            ) from exc
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        if self.process is None or self.process.stderr is None:
            return
        while True:
            chunk = await self.process.stderr.read(1024)
            if not chunk:
                return
            self._stderr_tail.extend(chunk)
            if len(self._stderr_tail) > 4096:
                del self._stderr_tail[:-4096]

    async def read_record(self) -> dict[str, Any] | None:
        if self.process is None or self.process.stdout is None:
            raise BridgeProtocolError("bridge is not started")
        try:
            raw = await self.process.stdout.readline()
        except (ValueError, asyncio.LimitOverrunError) as exc:
            raise BridgeProtocolError("Herdr bridge record exceeds line limit") from exc
        if not raw:
            await self.process.wait()
            if self.process.returncode not in (0, None):
                raise BridgeProtocolError("Herdr terminal bridge exited unexpectedly")
            return None
        if len(raw) > self.config.max_frame_bytes * 2:
            raise BridgeProtocolError("Herdr bridge record exceeds configured limit")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BridgeProtocolError("Herdr bridge emitted invalid JSON") from exc
        if not isinstance(value, dict):
            raise BridgeProtocolError("Herdr bridge record must be an object")
        kind = value.get("type")
        if kind == "terminal.closed":
            return {"type": "terminal.closed", "reason": str(value.get("reason") or "")[:240]}
        if kind != "terminal.frame":
            raise BridgeProtocolError(f"unsupported Herdr bridge record: {kind!r}")
        if value.get("encoding") != "ansi":
            raise BridgeProtocolError("Herdr terminal frame encoding must be ansi")
        encoded = value.get("bytes")
        if not isinstance(encoded, str):
            raise BridgeProtocolError("Herdr terminal frame bytes must be base64 text")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise BridgeProtocolError("Herdr terminal frame contains invalid base64") from exc
        if len(decoded) > self.config.max_frame_bytes:
            raise BridgeProtocolError("Herdr decoded terminal frame exceeds configured limit")
        seq = value.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise BridgeProtocolError("Herdr terminal frame seq must be a non-negative integer")
        full = value.get("full") is True
        if not self._has_full_frame and not full:
            raise BridgeProtocolError("first Herdr terminal frame must be full")
        if self._last_seq is not None and seq != self._last_seq + 1:
            raise BridgeProtocolError("Herdr terminal frame seq is not contiguous")
        self._has_full_frame = self._has_full_frame or full
        self._last_seq = seq
        width = value.get("width")
        height = value.get("height")
        if (
            not isinstance(width, int)
            or isinstance(width, bool)
            or not isinstance(height, int)
            or isinstance(height, bool)
            or not 1 <= width <= self.config.max_cols
            or not 1 <= height <= self.config.max_rows
        ):
            raise BridgeProtocolError("Herdr terminal frame dimensions are invalid")
        return {
            "type": "terminal.frame",
            "seq": seq,
            "encoding": "ansi",
            "width": width,
            "height": height,
            "full": full,
            "bytes": encoded,
            "_decoded_bytes": decoded,
        }

    def _validate_command(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise BridgeProtocolError("terminal command must be an object")
        kind = value.get("type")
        if kind == "terminal.input":
            text = value.get("text")
            encoded = value.get("bytes")
            if (text is None) == (encoded is None):
                raise BridgeProtocolError("terminal.input requires exactly one of text or bytes")
            if text is not None:
                if not isinstance(text, str) or len(text.encode("utf-8")) > self.config.max_input_bytes:
                    raise BridgeProtocolError("terminal.input text exceeds configured limit")
                return {"type": kind, "text": text}
            if not isinstance(encoded, str):
                raise BridgeProtocolError("terminal.input bytes must be base64 text")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise BridgeProtocolError("terminal.input bytes are invalid base64") from exc
            if len(raw) > self.config.max_input_bytes:
                raise BridgeProtocolError("terminal.input bytes exceed configured limit")
            return {"type": kind, "bytes": encoded}
        if kind == "terminal.resize":
            cols, rows = value.get("cols"), value.get("rows")
            if (
                not isinstance(cols, int)
                or isinstance(cols, bool)
                or not isinstance(rows, int)
                or isinstance(rows, bool)
                or not 1 <= cols <= self.config.max_cols
                or not 1 <= rows <= self.config.max_rows
            ):
                raise BridgeProtocolError("terminal.resize geometry is invalid")
            return {"type": kind, "cols": cols, "rows": rows}
        if kind == "terminal.mouse" and self.spec.mouse:
            column, row = value.get("column"), value.get("row")
            modifiers = value.get("modifiers", 0)
            if (
                value.get("action") not in ("down", "up")
                or value.get("button") != "left"
                or type(column) is not int
                or not 0 <= column < self.config.max_cols
                or type(row) is not int
                or not 0 <= row < self.config.max_rows
                or type(modifiers) is not int
                or not 0 <= modifiers <= 7
            ):
                raise BridgeProtocolError("terminal.mouse is invalid")
            return {
                "type": kind,
                "action": value["action"],
                "button": "left",
                "column": column,
                "row": row,
                "modifiers": modifiers,
            }
        if kind == "terminal.scroll":
            direction, lines = value.get("direction"), value.get("lines")
            source = value.get("source", "wheel")
            column, row = value.get("column"), value.get("row")
            modifiers = value.get("modifiers", 0)
            if (
                direction not in {"up", "down"}
                or not isinstance(lines, int)
                or isinstance(lines, bool)
                or not 1 <= lines <= 1000
                or source not in {"wheel", "page_key"}
                or (
                    column is not None
                    and (
                        not isinstance(column, int)
                        or isinstance(column, bool)
                        or not 0 <= column < self.config.max_cols
                    )
                )
                or (
                    row is not None
                    and (
                        not isinstance(row, int)
                        or isinstance(row, bool)
                        or not 0 <= row < self.config.max_rows
                    )
                )
                or not isinstance(modifiers, int)
                or isinstance(modifiers, bool)
                or not 0 <= modifiers <= 255
            ):
                raise BridgeProtocolError("terminal.scroll is invalid")
            command: dict[str, object] = {
                "type": kind,
                "direction": direction,
                "lines": lines,
                "source": source,
                "modifiers": modifiers,
            }
            if column is not None:
                command["column"] = column
            if row is not None:
                command["row"] = row
            return command
        if kind == "terminal.release":
            return {"type": kind}
        raise BridgeProtocolError(f"unsupported terminal command: {kind!r}")

    async def send_command(self, value: object) -> None:
        if self.spec.mode != "control":
            raise BridgeProtocolError("observe attachment is read-only")
        command = self._validate_command(value)
        if self.process is None or self.process.stdin is None:
            raise BridgeProtocolError("bridge stdin is unavailable")
        self.process.stdin.write(
            json.dumps(command, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
        )
        await self.process.stdin.drain()

    async def close(self) -> None:
        process = self.process
        if process is None:
            return
        if process.returncode is None and self.spec.mode == "control" and process.stdin is not None:
            try:
                process.stdin.write(b'{"type":"terminal.release"}\n')
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
        if process.stdin is not None:
            process.stdin.close()
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
        if self._stderr_task is not None:
            await asyncio.gather(self._stderr_task, return_exceptions=True)


async def relay_terminal_websocket(
    websocket: WebSocket,
    *,
    spec: TerminalBridgeSpec,
    config: RuntimeWebTerminalConfig,
) -> None:
    """Relay one attachment.  A slow sender is disconnected, never sampled."""

    bridge = HerdrNDJSONBridge(spec, config)
    queue: asyncio.Queue[tuple[str, bytes | None, int, bool] | None] = asyncio.Queue(
        maxsize=config.bridge_queue_frames
    )
    queued_bytes = 0
    queue_lock = asyncio.Lock()

    observer_eof = False

    async def read_bridge() -> None:
        nonlocal queued_bytes, observer_eof
        while True:
            record = await bridge.read_record()
            if record is None:
                observer_eof = spec.mode == "observe"
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull as exc:
                    raise BridgeBackpressureError(
                        "terminal attachment fell behind; reconnect for a full frame"
                    ) from exc
                return
            payload: bytes | None = None
            encoded_bytes = record.pop("bytes", None)
            decoded_bytes = record.pop("_decoded_bytes", None)
            if record.get("type") == "terminal.frame":
                if isinstance(decoded_bytes, bytes):
                    payload = decoded_bytes
                elif isinstance(encoded_bytes, str):
                    try:
                        payload = base64.b64decode(encoded_bytes, validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise BridgeProtocolError(
                            "terminal frame contains invalid base64"
                        ) from exc
                else:
                    raise BridgeProtocolError("terminal frame payload is unavailable")
            encoded = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
            size = len(encoded.encode("utf-8")) + len(payload or b"")
            async with queue_lock:
                if queue.full() or queued_bytes + size > config.bridge_queue_bytes:
                    raise BridgeBackpressureError(
                        "terminal attachment fell behind; reconnect for a full frame"
                    )
                queue.put_nowait(
                    (encoded, payload, size, record.get("type") == "terminal.closed")
                )
                queued_bytes += size
            if record.get("type") == "terminal.closed":
                return

    async def send_frames() -> None:
        nonlocal queued_bytes
        while True:
            item = await queue.get()
            if item is None:
                return
            encoded, payload, size, closed = item
            await websocket.send_text(encoded)
            if payload is not None:
                await websocket.send_bytes(payload)
            async with queue_lock:
                queued_bytes -= size
            if closed:
                return

    async def receive_control() -> None:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
            text = message.get("text")
            if spec.mode != "control":
                raise BridgeProtocolError("observe attachment is read-only")
            if not isinstance(text, str):
                raise BridgeProtocolError("terminal commands must be JSON text")
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise BridgeProtocolError("terminal command is invalid JSON") from exc
            await bridge.send_command(value)

    try:
        await bridge.start()
    except TerminalRuntimeError:
        await bridge.close()
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
        return
    tasks = {
        asyncio.create_task(read_bridge()),
        asyncio.create_task(send_frames()),
        asyncio.create_task(receive_control()),
    }
    close_code = 1000
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        failure: BaseException | None = None
        for task in done:
            try:
                task.result()
            except WebSocketDisconnect:
                pass
            except BaseException as exc:  # task exception is re-raised below
                failure = exc
        if failure is None and any(task.get_coro().__name__ == "read_bridge" for task in done):
            sender = next(task for task in tasks if task.get_coro().__name__ == "send_frames")
            try:
                await asyncio.wait_for(sender, timeout=2.0)
            except asyncio.TimeoutError:
                failure = BridgeBackpressureError(
                    "terminal attachment could not drain; reconnect for a full frame"
                )
        if isinstance(failure, BridgeBackpressureError) or observer_eof:
            close_code = 1013
        elif failure is not None:
            close_code = 1002 if isinstance(failure, BridgeProtocolError) else 1011
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bridge.close()
        try:
            await websocket.close(code=close_code)
        except RuntimeError:
            pass
