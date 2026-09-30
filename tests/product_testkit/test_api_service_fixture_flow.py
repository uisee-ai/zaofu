from __future__ import annotations

import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import assert_api_result, request_json
from .service import start_service


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_api_service_lifecycle_fixture_checks_health_contract_and_cleanup(tmp_path: Path):
    port = _free_port()
    server_script = tmp_path / "api_fixture.py"
    server_script.write_text(
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "import json, sys\n"
        "class H(BaseHTTPRequestHandler):\n"
        "  def do_GET(self):\n"
        "    body = json.dumps({'status': 'ok'}).encode() if self.path == '/items' else b'{}'\n"
        "    self.send_response(200 if self.path in ('/health', '/items') else 404)\n"
        "    self.send_header('Content-Type', 'application/json')\n"
        "    self.end_headers(); self.wfile.write(body)\n"
        "  def log_message(self, *_): pass\n"
        "ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()\n",
        encoding="utf-8",
    )
    handle = start_service(
        command=f"{sys.executable} {server_script} {port}",
        cwd=tmp_path,
        health_url=f"http://127.0.0.1:{port}/health",
        timeout_seconds=5,
    )
    try:
        result = request_json(f"http://127.0.0.1:{port}/items")
        document = assert_api_result(result, expected_status=200, json_keys=("status",))
        assert document["status"] == "ok"
    finally:
        process = handle.process
        handle.stop()
        assert process.poll() is not None
