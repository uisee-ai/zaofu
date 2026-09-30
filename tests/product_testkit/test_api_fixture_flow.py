from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from threading import Thread

from .api import assert_api_result, request_json


class _ResourceHandler(BaseHTTPRequestHandler):
    value = 0

    def do_POST(self):  # noqa: N802
        if self.headers.get("Authorization") != "Bearer test":
            self.send_response(401); self.end_headers(); return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload.get("name"), str) or not payload["name"]:
            self.send_response(400); self.end_headers(); return
        type(self).value += 1
        self.send_response(201); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"id": type(self).value, "name": payload["name"]}).encode())

    def log_message(self, *_args):
        return


def test_generic_api_product_fixture_covers_auth_validation_and_state_change():
    server = HTTPServer(("127.0.0.1", 0), _ResourceHandler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/resources"
        assert request_json(url, method="POST", payload={"name": "x"}).status_code == 401
        assert request_json(url, method="POST", payload={"name": ""}, token="test").status_code == 400
        result = request_json(url, method="POST", payload={"name": "x"}, token="test")
        body = assert_api_result(result, expected_status=201, json_keys=("id", "name"))
        assert body["name"] == "x"
    finally:
        server.shutdown(); thread.join(timeout=2); server.server_close()
