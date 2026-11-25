"""Script only the model's tool choice. Codex must execute cat and return real file bytes."""

import json
from collections import deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4


@contextmanager
def file_tool_endpoint(target):
    requests = []
    targets = target if isinstance(target, deque) else deque([target])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(self.path)
            n = len(requests)
            rid = "resp_" + uuid4().hex
            item = {
                "type": "message",
                "role": "assistant",
                "id": "msg_" + str(n),
                "content": [{"type": "output_text", "text": "ACK"}],
            }
            if n > 1 and targets:
                item = {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": f"call_file_witness_{n}",
                    "arguments": json.dumps(
                        {
                            "cmd": "/usr/bin/cat " + str(targets.popleft()),
                            "max_output_tokens": 16384,
                        }
                    ),
                }
            events = [
                {"type": "response.created", "response": {"id": rid}},
                {"type": "response.output_item.done", "item": item},
                {"type": "response.completed", "response": {"id": rid}},
            ]
            data = "".join(
                f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
