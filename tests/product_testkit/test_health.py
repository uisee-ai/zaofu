from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from .health import check_http_health


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler protocol
        self.send_response(200 if self.path == "/health" else 503)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args):
        return


def test_health_probe_distinguishes_success_and_non_2xx():
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert check_http_health(base + "/health").ok
        assert not check_http_health(base + "/bad").ok
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_health_probe_rejects_invalid_target():
    result = check_http_health("not-a-url")
    assert not result.ok
    assert "http" in result.error
