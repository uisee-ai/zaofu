"""Bounded loopback Anthropic response fixture; never forwards to a provider."""

from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import threading
import time


RESPONSES = {
    "rate_limit": (429, {"type": "error", "error": {
        "type": "rate_limit_error", "message": "rate limit exceeded (isolated TestKit response)",
    }}),
    "quota": (400, {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "Your credit balance is too low to access the Anthropic API (isolated TestKit response)",
    }}),
}


class ProviderHttpFault:
    """Serve errors or one fixed SSE text result, without handling real secrets."""

    def __init__(self, responses: list[str], *, fallback: str, text: str = "TESTKIT_LOCAL_OK"):
        if any(item not in {*RESPONSES, "success"} for item in [*responses, fallback]):
            raise ValueError("unknown fixture response")
        self._responses = deque(responses)
        self._fallback, self._text = fallback, text
        self.records: list[dict] = []
        self._lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_args):
                pass  # Never log request lines, headers or client bodies.

            def do_POST(self):
                path = self.path.split("?", 1)[0]
                if path != "/v1/messages":
                    self.reply(404, b"{}", "application/json")
                    return
                length = self.headers.get("Content-Length", "")
                if not length.isdecimal() or not 0 < int(length) <= 2_000_000:
                    self.reply(413, b"{}", "application/json")
                    return
                remaining = int(length)
                while remaining:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        return
                    remaining -= len(chunk)
                with fixture._lock:
                    if len(fixture.records) >= 32:
                        self.reply(503, b"{}", "application/json")
                        return
                    mode = fixture._responses.popleft() if fixture._responses else fixture._fallback
                    status = 200 if mode == "success" else RESPONSES[mode][0]
                    fixture.records.append({"sequence": len(fixture.records) + 1, "mode": mode,
                                            "status": status, "request_bytes": int(length),
                                            "monotonic": time.monotonic()})
                if mode == "success":
                    self.reply(200, fixture._sse(), "text/event-stream")
                else:
                    self.reply(status, json.dumps(RESPONSES[mode][1]).encode(), "application/json")

            def reply(self, status, body, content_type):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                if status == 429:
                    self.send_header("Retry-After", "1")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.05),
                                       name="zf-provider-http-fixture", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def restore(self) -> None:
        with self._lock:
            self._responses.clear()
            self._fallback = "success"

    def _sse(self) -> bytes:
        messages = [
            {"type": "message_start", "message": {"id": "msg_testkit_local", "type": "message",
             "role": "assistant", "model": "claude-sonnet-4-6", "content": [], "stop_reason": None,
             "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": self._text}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
             "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]
        return "".join(f"event: {m['type']}\ndata: {json.dumps(m)}\n\n" for m in messages).encode()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        assert not self.thread.is_alive(), "fixture HTTP server did not exit"


def isolated_provider_env(root: Path, endpoint: str) -> dict[str, str]:
    """Only child env; no real credentials, user settings, or outbound proxy."""
    return {
        "CLAUDE_CONFIG_DIR": str(root / "provider-config"),
        "ANTHROPIC_BASE_URL": endpoint,
        "ANTHROPIC_API_KEY": "testkit-not-a-real-key",
        "ANTHROPIC_AUTH_TOKEN": "",
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9", "all_proxy": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    }
