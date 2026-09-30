"""Deterministic localhost fake-service lifecycle for product integration tests."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import urlsplit

from tests.testkit.evidence import write_json_atomic


@dataclass(frozen=True)
class MockServiceReceipt:
    service_id: str
    base_url: str
    route_count: int
    request_count: int
    requests_ref: str
    receipt_ref: str
    started: bool
    stopped: bool
    error: str = ""

    @property
    def passed(self) -> bool:
        return self.started and self.stopped and not self.error


class MockServiceHandle:
    def __init__(
        self,
        *,
        service_id: str,
        server: ThreadingHTTPServer,
        base_url: str,
        routes: Mapping[tuple[str, str], Mapping[str, Any]],
        output_dir: Path,
    ) -> None:
        self.service_id = service_id
        self.server = server
        self.base_url = base_url
        self.routes = routes
        self.output_dir = output_dir
        self.requests: list[dict[str, Any]] = []
        self._thread = threading.Thread(target=server.serve_forever, name=f"zf-mock-{service_id}", daemon=True)
        self._thread.start()
        self._receipt: MockServiceReceipt | None = None

    @property
    def env(self) -> dict[str, str]:
        return {f"ZF_MOCK_{_env_suffix(self.service_id)}_BASE_URL": self.base_url}

    def stop(self) -> MockServiceReceipt:
        if self._receipt is not None:
            return self._receipt
        error = ""
        try:
            self.server.shutdown()
            self.server.server_close()
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                error = "mock service thread did not stop"
        except Exception as exc:  # noqa: BLE001 - cleanup must become evidence
            error = f"{type(exc).__name__}: {exc}"
        requests_path = self.output_dir / f"mock.{self.service_id}.requests.json"
        receipt_path = self.output_dir / f"mock.{self.service_id}.receipt.json"
        _write_json_value(requests_path, self.requests)
        receipt = MockServiceReceipt(
            service_id=self.service_id,
            base_url=self.base_url,
            route_count=len(self.routes),
            request_count=len(self.requests),
            requests_ref=str(requests_path),
            receipt_ref=str(receipt_path),
            started=True,
            stopped=not self._thread.is_alive(),
            error=error,
        )
        write_json_atomic(receipt_path, {**receipt.__dict__, "passed": receipt.passed})
        self._receipt = receipt
        return receipt


def start_mock_service(spec: Mapping[str, Any], *, output_dir: Path) -> MockServiceHandle:
    service_id = str(spec["id"])
    routes = {
        (str(route["method"]).upper(), str(route["path"])): dict(route)
        for route in spec["routes"]
    }
    handle_ref: dict[str, MockServiceHandle] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib protocol name
            self._serve()

        def do_POST(self) -> None:  # noqa: N802 - stdlib protocol name
            self._serve()

        def do_PUT(self) -> None:  # noqa: N802 - stdlib protocol name
            self._serve()

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib protocol name
            self._serve()

        def log_message(self, format: str, *args: object) -> None:
            return

        def _serve(self) -> None:
            handle = handle_ref["handle"]
            method = self.command.upper()
            path = urlsplit(self.path).path
            route = routes.get((method, path))
            body = self.rfile.read(int(self.headers.get("content-length", "0") or "0"))
            request = {
                "method": method,
                "path": path,
                "body_sha256": _digest_bytes(body),
                "headers": {
                    key.lower(): value
                    for key, value in self.headers.items()
                    if key.lower() not in {"authorization", "proxy-authorization", "cookie"}
                },
            }
            handle.requests.append(request)
            if route is None:
                self._respond(404, {"error": "route_not_declared"})
                return
            count = sum(
                1 for item in handle.requests
                if item["method"] == method and item["path"] == path
            )
            failures = int(route.get("failures_before_success", 0))
            status = int(route.get("failure_status", 503)) if count <= failures else int(route["status"])
            self._respond(status, route.get("body", ""))

        def _respond(self, status: int, body: Any) -> None:
            if isinstance(body, (dict, list)):
                payload = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
                content_type = "application/json"
            else:
                payload = str(body).encode("utf-8")
                content_type = "text/plain; charset=utf-8"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    output_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base_url = f"http://127.0.0.1:{server.server_port}"
    handle = MockServiceHandle(
        service_id=service_id, server=server, base_url=base_url,
        routes=routes, output_dir=output_dir,
    )
    handle_ref["handle"] = handle
    return handle


def _env_suffix(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).upper()


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json_value(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)

