"""A separate-process, loopback-only Feishu protocol receiver for host tests."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import multiprocessing
import threading
from urllib.parse import parse_qs, urlparse
from urllib.request import ProxyHandler, Request, build_opener


TOKEN = "testkit-public-receiver-token"


def _serve(ready, reject):
    messages = []
    attempts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body, status=200):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/testkit/stop":
                self.respond({"stopping": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif parsed.path == "/testkit/received":
                self.respond({"messages": messages, "attempts": attempts})
            elif parsed.path == "/open-apis/im/v1/messages":
                chat = parse_qs(parsed.query).get("container_id", [""])[0]
                self.respond({"code": 0, "data": {"items": [
                    row for row in messages if row["chat_id"] == chat
                ]}})
            else:
                self.respond({"code": 404}, 404)

        def do_POST(self):
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self.respond({"code": 400001, "msg": "unexpected test credential"}, 403)
                return
            if urlparse(self.path).path != "/open-apis/im/v1/messages":
                self.respond({"code": 404}, 404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > 256 * 1024:
                self.respond({"code": 413}, 413)
                return
            body = json.loads(self.rfile.read(length))
            attempts.append(body)
            if reject:
                self.respond({"code": 400002, "msg": "receiver rejected test message"}, 503)
                return
            message_id = f"om_testkit_{len(messages) + 1}"
            messages.append({
                "message_id": message_id, "chat_id": body["receive_id"],
                "msg_type": body["msg_type"], "body": {"content": body["content"]},
            })
            self.respond({"code": 0, "data": {"message_id": message_id}})

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        ready.send(server.server_address[1])
        ready.close()
        server.serve_forever(poll_interval=0.05)


def read_receiver(url):
    with build_opener(ProxyHandler({})).open(Request(url), timeout=5) as response:
        return json.load(response)


@contextmanager
def owner_http_receiver(*, reject=False):
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_serve, args=(writer, reject))
    process.start()
    writer.close()
    url = ""
    try:
        assert reader.poll(10), "test receiver did not become ready"
        url = f"http://127.0.0.1:{reader.recv()}"
        yield url, process.pid
    finally:
        reader.close()
        if process.is_alive() and url:
            try:
                read_receiver(url + "/testkit/stop")
            except OSError:
                pass
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
        assert not process.is_alive(), "owned test receiver leaked"
        assert process.exitcode == 0, f"receiver did not shut down cleanly: {process.exitcode}"
