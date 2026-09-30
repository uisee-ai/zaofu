"""Minimal, dependency-free health-check probes for product entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


@dataclass(frozen=True)
class HealthResult:
    target: str
    ok: bool
    status_code: int | None
    error: str = ""


def check_http_health(url: str, *, timeout_seconds: float = 5.0) -> HealthResult:
    """Probe an HTTP health endpoint; non-2xx is an explicit failed result."""

    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return HealthResult(str(url), False, None, "health URL must use http or https")
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(Request(url, headers={"Accept": "application/json"}), timeout=timeout_seconds) as response:
            status = int(response.status)
            response.read(1024 * 1024)
    except (OSError, URLError, TimeoutError, ValueError) as exc:
        return HealthResult(url, False, None, f"{type(exc).__name__}: {exc}")
    return HealthResult(url, 200 <= status < 300, status, "" if 200 <= status < 300 else "non-2xx health response")

