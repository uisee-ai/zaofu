"""Deterministic named failpoints for crash-boundary tests."""

from __future__ import annotations

from dataclasses import dataclass, field


class InjectedFailure(RuntimeError):
    """Raised when a configured test failpoint is reached."""


COMMIT_POINTS = (
    "before_event_append",
    "after_event_append_before_projection",
    "before_store_atomic_replace",
    "after_store_atomic_replace",
    "after_artifact_write_before_digest_event",
    "after_external_effect_before_result_event",
    "during_archive_rotation",
    "after_lock_acquire",
)


@dataclass
class FailpointController:
    """Record hits and inject exactly one deterministic failure."""

    fail_at: str | None = None
    once: bool = True
    hits: list[str] = field(default_factory=list)
    _injected: bool = False

    def hit(self, name: str) -> None:
        self.hits.append(name)
        if self.fail_at != name:
            return
        if self.once and self._injected:
            return
        self._injected = True
        raise InjectedFailure(f"test failpoint reached: {name}")

    def was_hit(self, name: str) -> bool:
        return name in self.hits
