"""Event signing — HMAC-SHA256."""

from __future__ import annotations

import hashlib
import hmac


class EventSigner:
    """Sign and verify event data with HMAC-SHA256."""

    def __init__(self, secret: bytes) -> None:
        self._secret = secret

    def sign(self, event_json: str) -> str:
        """Sign event JSON and return hex digest."""
        return hmac.new(self._secret, event_json.encode(), hashlib.sha256).hexdigest()

    def verify(self, event_json: str, signature: str) -> bool:
        """Verify event JSON against signature."""
        expected = self.sign(event_json)
        return hmac.compare_digest(expected, signature)

    def cache_fingerprint(self) -> str:
        """Stable non-secret fingerprint for verifier-aware read caches.

        The process-wide event-log cache must distinguish different signing
        keys reading the same path. Returning a keyed digest over a fixed label
        avoids exposing the secret while giving identical keys the same cache
        identity across signer instances.
        """
        return hmac.new(
            self._secret,
            b"zaofu:event-log-cache:v1",
            hashlib.sha256,
        ).hexdigest()
