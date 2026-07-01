"""Trust model for inbound Feishu callbacks (backlog feishu-B).

A single Feishu button can emit ``plan.approved`` and unlock writer fanout —
this is the crown jewel, so the "Feishu click → ZaoFu mutation" edge needs an
independent trust model, not a one-line "verify token". Each threat below names
the function that enforces its defense, so the model and the code cannot drift.

Threat model — external attack surface "Feishu click → mutation":

| Threat                          | Defense                                  | Enforced by              |
|---------------------------------|------------------------------------------|--------------------------|
| Forged callback (no real app)   | HMAC-style signature over the raw body   | ``verify_feishu_signature`` |
| Replay of a captured callback   | timestamp window (default 300s)          | ``verify_feishu_signature`` |
| Button payload tampering        | signature covers timestamp+nonce+body    | ``verify_feishu_signature`` |
| Unknown / unmapped principal    | fail-closed: absent → VIEWER → denied    | ``resolve_identity`` / ``identity_auth_levels`` |
| Privilege escalation            | level is config-resident in zf.yaml      | ``FeishuIdentityConfig`` (control plane) |
| Cross-chat injection            | identity keyed on open_id/user_id, not chat | ``resolve_identity``  |
| Duplicate / double-click submit  | idempotency key dedup (existing store)   | ``IdempotencyStore`` (caller) |

App secret storage/rotation: the verification token is read from an env var
(``verification_token_env``), never committed; rotating it is an env change, no
code change. Identity grants live in zf.yaml — same trust level as the kernel
config; "who can change it" is "who can change the control plane".

Pure functions only; wiring (HTTP boundary, audit events) lives in the CLI.
"""

from __future__ import annotations

import hashlib
import hmac

from zf.core.config.schema import FeishuIdentityConfig
from zf.integrations.feishu.gateway import AuthLevel

_LEVELS = {
    "viewer": AuthLevel.VIEWER,
    "operator": AuthLevel.OPERATOR,
    "approver": AuthLevel.APPROVER,
}


def verify_feishu_signature(
    *,
    timestamp: str,
    nonce: str,
    token: str,
    body: bytes,
    signature: str,
    now: float,
    max_age_seconds: int = 300,
) -> tuple[bool, str]:
    """Verify a Feishu event-callback signature, fail-closed.

    Feishu signs ``sha256(timestamp + nonce + token + body)`` (hex). We also
    reject stale timestamps so a captured-and-replayed callback falls outside
    the window. Returns ``(ok, reason)``; reason is an audit code on failure.
    """
    if not token:
        return False, "no_verification_token"
    if not signature:
        return False, "missing_signature"
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False, "bad_timestamp"
    if abs(now - ts) > max_age_seconds:
        return False, "stale_timestamp"
    digest = hashlib.sha256(
        timestamp.encode() + nonce.encode() + token.encode() + body
    ).hexdigest()
    if not hmac.compare_digest(digest, signature):
        return False, "signature_mismatch"
    return True, "ok"


def resolve_identity(
    identity: FeishuIdentityConfig,
    user_id: str,
) -> tuple[str, AuthLevel] | None:
    """Map a Feishu principal → (operator, AuthLevel), fail-closed.

    Disabled config, unmapped principal, or an unknown level all return None
    (caller denies). Never fail-open: a missing mapping is a denial, not a
    default-allow.
    """
    if not identity.enabled or not user_id:
        return None
    entry = identity.users.get(user_id)
    if entry is None:
        return None
    level = _LEVELS.get(entry.level.lower())
    if level is None:
        return None
    return entry.operator or user_id, level


def identity_auth_levels(identity: FeishuIdentityConfig) -> dict[str, AuthLevel]:
    """Project the identity map into the gateway's ``{user_id: AuthLevel}``.

    Unmapped principals are simply absent → the gateway defaults them to VIEWER
    → every mutating command is denied. That is the fail-closed posture.
    """
    levels: dict[str, AuthLevel] = {}
    for user_id in identity.users:
        resolved = resolve_identity(identity, user_id)
        if resolved is not None:
            levels[user_id] = resolved[1]
    return levels
