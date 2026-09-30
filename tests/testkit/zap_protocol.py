"""Fixed, bounded passive-scan protocol; not a general user-supplied ZAP DSL.

Official field/source audit: zap_launcher_research.md (implementation addendum).
An empty alert list is meaningful only after the launcher proves execution.
"""

from __future__ import annotations

from collections import Counter
import ipaddress
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from .zap import ZapVerdict, evaluate_zap_json


PORT = 8002
PATHS = ("/health", "/api/state")
HEADER_200_JSON = (
    r"(?im)\AHTTP/[0-9.]+[ \t]+200(?:[ \t]|\r?$)"
    r"(?=[\s\S]*^Content-Type:[ \t]*application/json(?:[ \t]*;[^\r\n]*)?[ \t]*\r?$)"
)


def checked_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    address = ipaddress.IPv4Address(parsed.hostname or "")
    if (
        not address.is_private or address.is_loopback or address.is_link_local
        or address.is_multicast or address.is_unspecified or address.is_reserved
        or origin != f"http://{address}:{PORT}"
    ):
        raise ValueError("ZAP origin must be the inspected private target IPv4 and fixed port")
    return origin


def fixed_plan(origin: str, run_id: str) -> dict[str, Any]:
    checked_origin(origin)
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("ZAP run identity must be a generated nonce")
    urls = [origin + path for path in PATHS]
    tests = []
    for path, url in zip(PATHS, urls, strict=True):
        tests.append({
            "name": f"mandatory-{path}", "type": "url", "url": url,
            "operator": "and", "requestHeaderRegex": r"\AGET[ \t]+",
            "requestBodyRegex": r"\A\z", "responseHeaderRegex": HEADER_200_JSON,
            "responseBodyRegex": (
                rf'"id"\s*:\s*"TASK-ZAP-{run_id}"' if path == "/api/state"
                else r"\A\s*\{"
            ),
            "onFail": "error",
        })
    return {
        "env": {
            "contexts": [{"name": "zf-owned", "urls": urls,
                          "includePaths": ["^" + re.escape(url) + "$" for url in urls]}],
            "parameters": {"failOnError": True, "failOnWarning": True,
                           "continueOnFailure": False, "progressToStdout": True},
        },
        "jobs": [
            {"type": "requestor", "name": "mandatory-read", "enabled": True, "alwaysRun": False,
             "requests": [{"url": url, "method": "GET", "httpVersion": "HTTP/1.1", "responseCode": 200} for url in urls],
             "tests": tests},
            {"type": "passiveScan-wait", "name": "passive-complete", "enabled": True,
             "alwaysRun": False, "parameters": {"maxDuration": 0}},
            {"type": "report", "name": "write-final-report", "enabled": True, "alwaysRun": False,
             "parameters": {"template": "traditional-json", "reportDir": "/zap/wrk",
                            "reportFile": f"zap-{run_id}.json", "displayReport": False}},
        ],
    }


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def strict_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_non_finite)


def read_regular(path: Path, *, limit: int = 4 * 1024 * 1024) -> str:
    if path.is_symlink() or not path.is_file() or any(p.is_symlink() for p in path.parents):
        raise ValueError(f"evidence requires a regular non-symlink file: {path}")
    if not 0 < path.stat().st_size <= limit:
        raise ValueError(f"empty or oversized evidence: {path}")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"evidence exceeded read budget: {path}")
    return raw.decode("utf-8")


def owned_report_verdict(path: Path, *, origin: str, max_risk: int) -> ZapVerdict:
    """Report gate only; caller must first prove AF completion and ownership."""
    try:
        checked_origin(origin)
        payload = strict_json(read_regular(path))
        if not isinstance(payload, dict) or any(
            not isinstance(payload.get(key), str) or not payload[key].strip()
            for key in ("@programName", "@version", "@generated")
        ):
            raise ValueError("missing scanner report identity")
        sites = payload.get("site")
        if not isinstance(sites, list) or len(sites) != 1:
            raise ValueError("report must contain exactly the owned site")
        site = sites[0]
        expected = {"@name": origin, "@host": urlsplit(origin).hostname,
                    "@port": str(PORT), "@ssl": "false"}
        if not isinstance(site, dict) or any(site.get(key) != value for key, value in expected.items()):
            raise ValueError("report site is outside the owned target")
        if not isinstance(site.get("alerts"), list):
            raise ValueError("report alerts missing")
        for alert in site["alerts"]:
            if not isinstance(alert, dict) or not isinstance(alert.get("instances"), list):
                raise ValueError("invalid alert instances")
            for instance in alert["instances"]:
                if not isinstance(instance, dict) or instance.get("uri") not in {origin + p for p in PATHS} or instance.get("method") != "GET":
                    raise ValueError("alert instance is outside the declared GET paths")
        return evaluate_zap_json(payload, max_risk=max_risk, scanner_exit_code=0)
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        return ZapVerdict("harness_failure", reason=str(error))


def access_counts(text: str) -> Counter[str]:
    """Check actual app access logs; counts are not invented from alert totals."""
    counts: Counter[str] = Counter()
    for method, path, status in re.findall(r'"([A-Z]+) ([^" ]+) HTTP/[0-9.]+" (\d{3})', text):
        if method != "GET" or path not in PATHS or status != "200":
            raise ValueError(f"unexpected target request: {method} {path} {status}")
        counts[path] += 1
    return counts
