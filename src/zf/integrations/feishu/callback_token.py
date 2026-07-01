"""Signed action tokens for Feishu card buttons (feishu-A2).

feishu-B verifies the *webhook* came from Feishu (transport authenticity) and
maps the principal to a permission level. But the button payload itself was a
bare ``{"action": "plan-approve:<id>"}`` — so an authorized principal could
forge an approve for any plan, an old card's button worked forever, and the
same action could be replayed across chats. This module binds each button to
its exact context with an HMAC token so the button cannot be forged, expired,
or repurposed:

| Threat                              | Defense                          | Field |
|-------------------------------------|----------------------------------|-------|
| Forged button (wrong/extra action)  | HMAC over the whole payload      | sig   |
| Stale card clicked long after issue | expiry                           | x     |
| Same signed click replayed          | single-use nonce (caller store)  | n     |
| Button repurposed for another target| target bound into the signature  | t     |
| Cross-chat injection                | chat_id bound into the signature | c     |
| Secret rotation                     | key version → verify any known   | k     |

The HMAC key is separate from the Feishu verification token. Pure functions;
the nonce store and secret loading live at the edge (push/handler).
"""

from __future__ import annotations

import base64
import json
import secrets
from typing import Callable

from zf.core.security.signing import EventSigner

_PREFIX = "zf1"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def sign_action(
    secret: bytes,
    *,
    action: str,
    target: str,
    chat_id: str,
    ttl_seconds: int,
    now: float,
    key_version: str = "1",
    nonce: str = "",
) -> str:
    """Issue a signed token binding a button to (action, target, chat, expiry).

    ``now`` is injected (no implicit clock) so callers stay deterministic.
    """
    payload = {
        "a": action,
        "t": target,
        "c": chat_id,
        "x": int(now) + int(ttl_seconds),
        "n": nonce or secrets.token_urlsafe(9),
        "k": key_version,
    }
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = EventSigner(secret).sign(body)
    return f"{_PREFIX}.{body}.{sig}"


def verify_action(
    token: str,
    *,
    secrets_by_version: dict[str, bytes],
    expect_action: str,
    expect_target: str,
    expect_chat_id: str,
    now: float,
    consume_nonce: Callable[[str], bool] | None = None,
) -> tuple[bool, str]:
    """Verify a signed action token, fail-closed. Returns (ok, audit_reason).

    Order: format → key lookup → constant-time HMAC → expiry → exact context
    match → single-use nonce. ``consume_nonce(nonce)`` returns False if the
    nonce was already used (replay) — the caller owns the store.
    """
    if not token:
        return False, "missing_token"
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _PREFIX:
        return False, "bad_format"
    _, body, sig = parts
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return False, "bad_payload"
    if not isinstance(payload, dict):
        return False, "bad_payload"
    secret = secrets_by_version.get(str(payload.get("k") or ""))
    if secret is None:
        return False, "unknown_key_version"
    if not EventSigner(secret).verify(body, sig):
        return False, "signature_mismatch"
    if int(payload.get("x") or 0) <= int(now):
        return False, "expired"
    if str(payload.get("a") or "") != expect_action:
        return False, "action_mismatch"
    if str(payload.get("t") or "") != expect_target:
        return False, "target_mismatch"
    if str(payload.get("c") or "") != expect_chat_id:
        return False, "chat_mismatch"
    nonce = str(payload.get("n") or "")
    if consume_nonce is not None and not consume_nonce(nonce):
        return False, "nonce_replay"
    return True, "ok"


def attach_action_token(
    card: dict,
    *,
    secret: bytes,
    chat_id: str,
    ttl_seconds: int,
    now: float,
    key_version: str = "1",
) -> dict:
    """Sign every action button in a card, binding it to its target+chat.

    Walks ``card["elements"]`` for action buttons carrying ``value.action`` of
    the form ``<action>:<target>`` and injects a ``value.t`` token. Mutates the
    button value in place (the card is freshly built per send, not shared).
    """
    for element in card.get("elements", []):
        if not isinstance(element, dict) or element.get("tag") != "action":
            continue
        for button in element.get("actions", []):
            value = button.get("value") if isinstance(button, dict) else None
            if not isinstance(value, dict):
                continue
            raw_action = str(value.get("action") or "")
            if ":" not in raw_action:
                continue
            action, _, target = raw_action.partition(":")
            value["t"] = sign_action(
                secret,
                action=action,
                target=target,
                chat_id=chat_id,
                ttl_seconds=ttl_seconds,
                now=now,
                key_version=key_version,
            )
    return card
