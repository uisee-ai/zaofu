"""Dependency-free HTTP contract probes for product projects."""

from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler


@dataclass(frozen=True)
class ApiResult:
    method: str
    url: str
    status_code: int | None
    headers: dict[str, str]
    body: bytes
    error: str = ""

    def json(self):
        return json.loads(self.body.decode("utf-8"))


def request_json(url: str, *, method: str = "GET", payload=None,
                 token: str | None = None, timeout_seconds: float = 10.0) -> ApiResult:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=body, headers=headers, method=method.upper())
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout_seconds) as response:
            return ApiResult(method.upper(), url, int(response.status), dict(response.headers), response.read(4 * 1024 * 1024))
    except HTTPError as exc:
        return ApiResult(method.upper(), url, int(exc.code), dict(exc.headers), exc.read(4 * 1024 * 1024), str(exc))
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        return ApiResult(method.upper(), url, None, {}, b"", f"{type(exc).__name__}: {exc}")


def assert_api_result(result: ApiResult, *, expected_status: int, json_keys: tuple[str, ...] = ()) -> dict:
    assert result.status_code == expected_status, (result.url, result.status_code, result.error)
    document = result.json()
    assert isinstance(document, dict), document
    for key in json_keys:
        assert key in document, (key, document)
    return document

