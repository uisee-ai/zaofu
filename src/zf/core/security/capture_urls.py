"""Canonical URL exclusions for browser evidence, not a network allowlist."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit, urlunsplit


def normalize_capture_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("Capture URL must be nonempty and at most 2048 characters.")
    value = value.strip()
    if re.search(r"[\x00-\x20\\*]", value):
        raise ValueError("Capture URLs cannot contain whitespace, backslashes or wildcards.")
    try:
        url = urlsplit(value)
        port = url.port
        host = (url.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid capture URL.") from exc
    if url.scheme not in {"http", "https"} or not host or url.username is not None or url.password is not None:
        raise ValueError("Use an HTTP(S) URL without embedded credentials.")
    path = url.path or "/"
    decoded = unquote(path, errors="strict")
    if (re.search(r"%(?:2f|5c|00|25)", path, re.I) or re.search(r"[\x00-\x20\\]", decoded)
            or any(part in {".", ".."} for part in decoded.split("/")) or "//" in path):
        raise ValueError("Use an unambiguous capture URL path.")
    hostname = f"[{host}]" if ":" in host else host
    authority = hostname + (f":{port}" if port is not None and port != (443 if url.scheme == "https" else 80) else "")
    return urlunsplit((url.scheme, authority, path.rstrip("/") or "/", "", ""))


def validate_capture_urls(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError("Blocked capture URLs must be an array of at most 200 URLs.")
    return list(dict.fromkeys(normalize_capture_url(item) for item in value))


def blocked_capture_url(value: str, rules: list[str]) -> str | None:
    target = urlsplit(normalize_capture_url(value))
    path = unquote(target.path).rstrip("/") or "/"
    for value in rules:
        rule = urlsplit(normalize_capture_url(value))
        prefix = unquote(rule.path).rstrip("/") or "/"
        if (target.scheme, target.netloc) == (rule.scheme, rule.netloc) and (
            prefix == "/" or path == prefix or path.startswith(prefix + "/")
        ):
            return value
    return None
