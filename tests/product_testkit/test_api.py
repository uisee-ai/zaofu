from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
import json

from .api import assert_api_result, request_json


class _ApiHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.headers.get("Authorization") != "Bearer test":
            self.send_response(401); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"status": "ok"}).encode())

    def log_message(self, *_args):
        return


def test_api_probe_checks_auth_status_and_json():
    server = HTTPServer(("127.0.0.1", 0), _ApiHandler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/health"
        assert request_json(url).status_code == 401
        result = request_json(url, token="test")
        assert_api_result(result, expected_status=200, json_keys=("status",))
    finally:
        server.shutdown(); thread.join(timeout=2); server.server_close()


def test_api_probe_classifies_unreachable_endpoint_as_harness_result():
    result = request_json("http://127.0.0.1:1/unreachable", timeout_seconds=0.2)
    assert result.status_code is None
    assert result.error


def test_api_assertion_fails_when_response_schema_is_incomplete():
    server = HTTPServer(("127.0.0.1", 0), _ApiHandler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        result = request_json(f"http://127.0.0.1:{server.server_port}/health", token="test")
        try:
            assert_api_result(result, expected_status=200, json_keys=("missing",))
        except AssertionError:
            pass
        else:
            raise AssertionError("incomplete API schema was accepted")
    finally:
        server.shutdown(); thread.join(timeout=2); server.server_close()
