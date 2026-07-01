"""LH-4.T6: exponential backoff helper.

Tuned for long-horizon: base=2s, factor=2, jitter=0-0.5x. Caller applies
the result as a sleep/delay before the next attempt.

Production defaults at attempt=1..5 with base=2:
  1 → ~2s     2 → ~4s     3 → ~8s     4 → ~16s     5 → ~32s
Total worst-case for 5 retries ≈ 62s; fits well inside task timeouts.
"""

from __future__ import annotations

import random


def exponential_backoff(
    attempt: int,
    *,
    base_seconds: float = 2.0,
    factor: float = 2.0,
    jitter: float = 0.5,
    max_seconds: float = 300.0,
) -> float:
    """Return the delay (seconds) for the Nth retry attempt (1-indexed).

    attempt=0 → 0 (no delay for the original try).
    attempt=1 → base_seconds (1 + random jitter).
    attempt>=2 → base_seconds * factor^(attempt-1).

    Clamped to [0, max_seconds].
    """
    if attempt <= 0:
        return 0.0
    raw = base_seconds * (factor ** (attempt - 1))
    if jitter > 0:
        raw *= 1 + random.random() * jitter
    return float(min(max(raw, 0.0), max_seconds))
