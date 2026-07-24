"""Value objects shared by runtime, CLI, and Web artifact consumers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


QueryMode = Literal["advisory", "canonical"]


@dataclass(frozen=True)
class QueryContext:
    project_root: Path
    state_dir: Path
    actor: str = "operator"
    role: str = ""
    purpose: str = "query"
    mode: QueryMode = "advisory"
    limit: int = 200
    offset: int = 0

    def bounded_limit(self) -> int:
        return max(1, min(int(self.limit or 200), 1000))

    def bounded_offset(self) -> int:
        return max(0, int(self.offset or 0))


@dataclass(frozen=True)
class SourceSnapshot:
    projected_seq: int
    event_manifest_digest: str
    task_store_digest: str = ""
    feature_store_digest: str = ""
    session_store_digest: str = ""
    task_ref_index_digest: str = ""
    package_reducer_version: str = ""
    attempt_handoff_reducer_version: str = ""
    descriptor_extractor_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event_cursor"] = {
            "projected_seq": self.projected_seq,
            "segment_layout_digest": self.event_manifest_digest,
        }
        return payload


@dataclass
class QueryResult:
    schema_version: str
    items: list[dict[str, Any]] = field(default_factory=list)
    item: dict[str, Any] | None = None
    source_snapshot: SourceSnapshot | None = None
    projection_state: str = "ready"
    projection_lag: int | None = 0
    source: str = "read_model.sqlite"
    fallback_used: bool = False
    fallback_source: str = ""
    redaction: str = "metadata-only"
    limit: int = 0
    offset: int = 0
    has_more: bool = False
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "projection_state": self.projection_state,
            "projection_lag": self.projection_lag,
            "source": self.source,
            "fallback": {
                "used": self.fallback_used,
                "source": self.fallback_source,
            },
            "redaction": self.redaction,
            "limit": self.limit,
            "offset": self.offset,
            "has_more": self.has_more,
            "diagnostics": self.diagnostics,
        }
        if self.source_snapshot is not None:
            payload["source_snapshot"] = self.source_snapshot.to_dict()
        if self.item is not None:
            payload["item"] = self.item
        else:
            payload["items"] = self.items
        return payload


__all__ = [
    "QueryContext",
    "QueryMode",
    "QueryResult",
    "SourceSnapshot",
]
