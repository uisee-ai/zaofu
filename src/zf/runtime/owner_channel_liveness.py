"""Owner-channel liveness — is escalation actually reaching a human? (doc 79 Tier3)

R12: 16x ``owner.visible_message.failed`` (dead token) yet the run kept
escalating 286x to a channel nobody received. Liveness turns fire-and-forget
escalation honest: escalate only if the channel is confirmed reachable;
otherwise the remediation cascade floors to a deterministic safe-halt.

Pure / deterministic — reads the owner-delivery outcome events, no I/O.
"""

from __future__ import annotations

CHANNEL_LIVE = "live"
CHANNEL_DEAD = "dead"
CHANNEL_UNKNOWN = "unknown"

OWNER_DELIVERED = "owner.visible_message.delivered"
OWNER_FAILED = "owner.visible_message.failed"


def _etype(event) -> str | None:
    if isinstance(event, dict):
        return event.get("type")
    return getattr(event, "type", None)


def channel_liveness(events, *, fail_threshold: int = 3) -> str:
    """Decide owner-channel liveness from recent delivery outcomes.

    Scans newest→oldest:
    - the most recent owner outcome is a ``delivered`` → ``live``;
    - ``fail_threshold`` trailing failures with no delivery → ``dead``;
    - some failures but below threshold, or no owner signal at all → ``unknown``.

    ``unknown`` is deliberately not ``dead`` — we only floor to safe-halt when
    the channel is *confirmed* dead, never on absence of evidence.
    """
    trailing_fail = 0
    for event in reversed(list(events)):
        t = _etype(event)
        if t == OWNER_DELIVERED:
            # Most recent owner outcome is a delivery → live. But trailing
            # failures *after* the last delivery (below threshold) mean the
            # channel is degrading — unknown, not confidently live.
            return CHANNEL_LIVE if trailing_fail == 0 else CHANNEL_UNKNOWN
        if t == OWNER_FAILED:
            trailing_fail += 1
            if trailing_fail >= fail_threshold:
                return CHANNEL_DEAD
    return CHANNEL_UNKNOWN


def operator_reachable(events, *, fail_threshold: int = 3) -> bool:
    """True unless the owner channel is *confirmed* dead.

    This is what the remediation cascade's ``liveness`` parameter consumes:
    live/unknown → attempt escalation; dead → floor to safe-halt.
    """
    return channel_liveness(events, fail_threshold=fail_threshold) != CHANNEL_DEAD
