"""Cross-process ownership for one Channel reply dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import wraps
import hashlib
from pathlib import Path
from typing import Any, Callable

from zf.core.state.locks import locked_path


@dataclass(frozen=True)
class ChannelDispatchResult:
    dispatched: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dispatched": self.dispatched,
            "completed": self.completed,
            "failed": self.failed,
            "skipped": self.skipped,
        }


def channel_reply_dispatch_claim_path(state_dir: Path, channel_id: str, request_id: str) -> Path:
    """Share the live dispatcher fence with controlled recovery."""
    digest = hashlib.sha256(f"{channel_id}:{request_id}".encode("utf-8")).hexdigest()
    return Path(state_dir) / "locks" / "channel-reply-claims" / digest


def with_channel_reply_dispatch_claim(
    dispatch: Callable[..., ChannelDispatchResult],
) -> Callable[..., ChannelDispatchResult]:
    """Allow only one process to dispatch a Channel reply request."""

    @wraps(dispatch)
    def claimed_dispatch(*args: Any, **kwargs: Any) -> ChannelDispatchResult:
        channel_id = str(kwargs["channel_id"])
        request_id = str(kwargs["request_id"])
        lock_target = channel_reply_dispatch_claim_path(kwargs["state_dir"], channel_id, request_id)
        try:
            with locked_path(lock_target, timeout_seconds=0.05):
                return dispatch(*args, **kwargs)
        except TimeoutError:
            return ChannelDispatchResult(skipped=[{
                "request_id": request_id,
                "reason": "dispatch_claim_busy",
            }])

    return claimed_dispatch
