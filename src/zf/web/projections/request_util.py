"""Projections layer: request_util (moved verbatim from web/server.py)."""
from __future__ import annotations

from datetime import datetime
from datetime import timezone
from fastapi import Request
from pathlib import Path
from zf.core.security.redaction import redact_event
from zf.core.security.redaction import redact_obj
from zf.core.state.locks import locked_path
import json
import os
from zf.web.projections.common import _append_jsonl, _read_jsonl_dicts


async def _request_json(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _idempotency_path(state_dir: Path) -> Path:
    return state_dir / "web-actions" / "idempotency.jsonl"


def _reserve_idempotency_key(
    state_dir: Path,
    *,
    key: str,
    action: str,
    payload_hash: str,
) -> dict:
    if not key:
        return {"status": "none"}
    path = _idempotency_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked_path(path):
        records = _read_jsonl_dicts(path)
        matches = [record for record in records if record.get("key") == key]
        if matches:
            latest = matches[-1]
            if latest.get("action") != action or latest.get("payload_hash") != payload_hash:
                return {"status": "conflict", "record": latest}
            if latest.get("state") == "completed" and isinstance(latest.get("response"), dict):
                return {
                    "status": "replayed",
                    "record": latest,
                    "response": latest["response"],
                }
            return {"status": "pending", "record": latest}
        _append_jsonl(path, {
            "key": key,
            "action": action,
            "payload_hash": payload_hash,
            "state": "pending",
            "ts": datetime.now(timezone.utc).isoformat(),
        })
    return {"status": "reserved"}


def _complete_idempotency_key(
    state_dir: Path,
    *,
    key: str,
    action: str,
    payload_hash: str,
    response: dict,
) -> None:
    if not key:
        return
    path = _idempotency_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    stored_response = redact_obj(dict(response))
    with locked_path(path):
        _append_jsonl(path, {
            "key": key,
            "action": action,
            "payload_hash": payload_hash,
            "state": "completed",
            "ts": datetime.now(timezone.utc).isoformat(),
            "response": stored_response,
        })


def _web_passcode_configured() -> bool:
    return bool(os.environ.get("ZF_WEB_PASSCODE", ""))


def _request_client_id(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _web_unlock_rate_limit() -> tuple[int, int]:
    raw_limit = os.environ.get("ZF_WEB_PASSCODE_MAX_ATTEMPTS", "").strip()
    raw_window = os.environ.get("ZF_WEB_PASSCODE_WINDOW_SECONDS", "").strip()
    try:
        limit = int(raw_limit) if raw_limit else 8
    except ValueError:
        limit = 8
    try:
        window = int(raw_window) if raw_window else 60
    except ValueError:
        window = 60
    return max(1, min(limit, 100)), max(10, min(window, 3600))


def _web_trusted_session_enabled() -> bool:
    return os.environ.get("ZF_WEB_TRUSTED_SESSION", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "local_trusted",
    }


def _web_trusted_session_nonloopback_override() -> bool:
    return os.environ.get(
        "ZF_WEB_TRUSTED_SESSION_ALLOW_NONLOOPBACK", "",
    ).strip().lower() in {"1", "true", "yes"}


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix):]
    return None


def _sse_event(seq: int, event: object) -> bytes:
    safe_event = redact_event(event)  # type: ignore[arg-type]
    return f"id: {seq}\ndata: {safe_event.to_json()}\n\n".encode("utf-8")


def _sse_gap(*, cursor: int, current: int) -> bytes:
    payload = {
        "type": "stream.gap",
        "payload": {
            "cursor": cursor,
            "current": current,
            "reason": "cursor is outside active replay window",
        },
    }
    return (
        f"id: {current}\n"
        "event: stream.gap\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


def _parse_cursor(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None
