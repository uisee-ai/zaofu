"""EventLog construction helpers.

Event signing is configured in `zf.yaml`, but EventLog itself should stay
a small append/read primitive. This module is the boundary that turns
project config into a signer-aware EventLog.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from zf.core.config.schema import ZfConfig
from zf.core.events.log import EventLog
from zf.core.security.signing import EventSigner


class EventSigningConfigError(RuntimeError):
    """Raised when ``security.event_signing.enabled`` is set but the
    configured secret env var is empty and the project has not opted in
    to ``allow_unsigned_fallback``. Fail-closed so misconfiguration
    cannot silently downgrade the event log to unsigned writes."""


def build_event_signer(
    config: ZfConfig | None,
    *,
    warn: bool = True,
) -> EventSigner | None:
    """Build the configured event signer, if event signing is enabled.

    Behavior matrix:
      * enabled=False                       → return None (unsigned mode)
      * enabled=True + secret present       → return EventSigner
      * enabled=True + secret missing
          + allow_unsigned_fallback=False   → raise EventSigningConfigError
          + allow_unsigned_fallback=True    → warn + return None
    """
    if config is None:
        return None

    sec = getattr(config, "security", None)
    es = getattr(sec, "event_signing", None) if sec else None
    if es is None or not es.enabled:
        return None

    secret = os.environ.get(es.secret_env, "")
    if not secret:
        allow_fallback = bool(getattr(es, "allow_unsigned_fallback", False))
        message = (
            f"security.event_signing.enabled but {es.secret_env} is empty"
        )
        if not allow_fallback:
            raise EventSigningConfigError(
                message
                + "; set the secret or opt in to "
                "security.event_signing.allow_unsigned_fallback=true to "
                "preserve the legacy warn-and-continue behavior."
            )
        if warn:
            print(
                f"Warning: {message}; "
                f"allow_unsigned_fallback=true → falling back to unsigned events.",
                file=sys.stderr,
            )
        return None
    return EventSigner(secret.encode("utf-8"))


def event_log_from_project(
    state_dir: Path,
    *,
    config: ZfConfig | None = None,
    warn: bool = True,
) -> EventLog:
    """Return the EventLog for a project's effective runtime state dir."""
    es = getattr(getattr(config, "security", None), "event_signing", None)
    return EventLog(
        Path(state_dir) / "events.jsonl",
        signer=build_event_signer(config, warn=warn),
        # Legacy projects that opted in to allow_unsigned_fallback keep
        # tolerating pre-signing plain lines on read; default is fail-closed.
        allow_unsigned=bool(getattr(es, "allow_unsigned_fallback", False)),
    )
