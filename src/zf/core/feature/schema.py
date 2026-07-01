"""Feature dataclass — L1 high-level user goal."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


_VALID_STATUSES = ("planning", "active", "done", "cancelled")


def _new_feature_id() -> str:
    return f"F-{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Feature:
    title: str = ""
    id: str = field(default_factory=_new_feature_id)
    description: str = ""
    status: str = "planning"  # planning | active | done | cancelled
    priority: int = 3  # 1 (highest) .. 5 (lowest)
    created_at: str = field(default_factory=_now_iso)
    completed_at: str = ""
    user_message: str = ""  # original natural language input
