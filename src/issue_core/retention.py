"""Portable retention decisions for private Issue artifacts and metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class IssueRetentionPolicy:
    draft_recovery_days: int = 7
    private_evidence_days_after_terminal: int = 30
    semantic_metadata_days_after_terminal: int = 90

    def due(
        self,
        retention_class: str,
        *,
        now: datetime,
        terminal_at: datetime | None,
    ) -> bool:
        """Active/published work is retained; terminal work expires by class."""

        if terminal_at is None:
            return False
        days = {
            "draft_recovery": self.draft_recovery_days,
            "private_evidence": self.private_evidence_days_after_terminal,
            "semantic_metadata": self.semantic_metadata_days_after_terminal,
        }.get(retention_class)
        if days is None:
            raise ValueError("unsupported Issue retention class")
        return now >= terminal_at + timedelta(days=days)


__all__ = ["IssueRetentionPolicy"]
